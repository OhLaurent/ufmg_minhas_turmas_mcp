"""Sessão HTTP com SSO SAML (IdP minhaUFMG) compartilhada entre o portal e os Moodles da UFMG."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

IDP_HOST = "sistemas.ufmg.br"
STATE_DIR = Path.home() / ".ufmg_mcp"
COOKIE_FILE = STATE_DIR / "cookies.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


class LoginError(RuntimeError):
    pass


def load_dotenv(path: Path | None = None) -> None:
    """Carrega um .env simples (KEY=VALUE) sem sobrescrever variáveis já definidas."""
    path = path or Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def form_data(form) -> dict[str, str]:
    """Valores de um <form> como o navegador enviaria (sem botões nem arquivos)."""
    data = {}
    for inp in form.find_all(["input", "select", "textarea"]):
        name = inp.get("name")
        if not name:
            continue
        typ = (inp.get("type") or "text").lower()
        if typ in ("submit", "button", "image", "reset", "file"):
            continue
        if typ in ("checkbox", "radio") and not inp.has_attr("checked"):
            continue
        if inp.name == "select":
            opt = inp.find("option", selected=True) or inp.find("option")
            data[name] = opt.get("value", opt.text) if opt else ""
        elif inp.name == "textarea":
            data[name] = inp.text
        else:
            data[name] = inp.get("value", "")
    return data


def _submit_button(form) -> dict[str, str]:
    for btn in form.find_all(["button", "input"]):
        typ = (btn.get("type") or ("submit" if btn.name == "button" else "")).lower()
        if typ == "submit" and btn.get("name"):
            return {btn["name"]: btn.get("value", "")}
    return {}


def _needs_sso(resp: httpx.Response) -> bool:
    if resp.url.host == IDP_HOST:
        return True
    return "text/html" in resp.headers.get("content-type", "") and 'name="SAMLResponse"' in resp.text


class UfmgSession:
    """Cliente HTTP que completa o SSO SAML automaticamente quando um site da UFMG o exige."""

    def __init__(self, user: str | None = None, password: str | None = None):
        self.user = (user or os.environ.get("UFMG_USER") or "").strip().lower() or None
        self.password = password or os.environ.get("UFMG_PASSWORD")
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=60,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            },
        )
        self._load()

    # ---------- persistência de cookies ----------
    def _load(self) -> None:
        try:
            cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for c in cookies:
            self.client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))

    def save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in self.client.cookies.jar]
        COOKIE_FILE.write_text(json.dumps(cookies), encoding="utf-8")
        try:
            os.chmod(COOKIE_FILE, 0o600)
        except OSError:
            pass

    # ---------- requisições ----------
    def request(self, method: str, url: str, **kw) -> httpx.Response:
        """Faz a requisição; se cair no SSO, autentica e repete a requisição original."""
        resp = self.client.request(method, url, **kw)
        if not _needs_sso(resp):
            return resp
        resp = self._complete_sso(resp)
        self.save()
        if method.upper() != "GET" or urlparse(str(resp.url)).path != urlparse(url).path:
            resp = self.client.request(method, url, **kw)
        if _needs_sso(resp):
            raise LoginError(f"Não foi possível autenticar em {url}.")
        return resp

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self.request("POST", url, **kw)

    # ---------- fluxo SAML ----------
    def _complete_sso(self, resp: httpx.Response) -> httpx.Response:
        password_tries = 0
        for _ in range(12):
            if not _needs_sso(resp):
                return resp
            soup = BeautifulSoup(resp.text, "lxml")
            forms = soup.find_all("form")
            if not forms:
                raise LoginError(f"Fluxo de login inesperado em {resp.url} (nenhum formulário).")
            pw_form = next((f for f in forms if f.find("input", type="password")), None)
            if pw_form is not None:
                if not self.user or not self.password:
                    raise LoginError("Defina UFMG_USER e UFMG_PASSWORD (variáveis de ambiente ou .env).")
                password_tries += 1
                if password_tries > 1 or pw_form.find("input", attrs={"name": "cAnswer"}):
                    raise LoginError(self._login_failure_message(soup, resp))
                form = pw_form
                data = form_data(form)
                user_inp = next(
                    (
                        i
                        for i in form.find_all("input")
                        if (i.get("type") or "text").lower() in ("text", "email") and i.get("name")
                    ),
                    None,
                )
                if user_inp is None:
                    raise LoginError("Campo de usuário não encontrado no formulário do IdP.")
                data[user_inp["name"]] = self.user
                data[form.find("input", type="password")["name"]] = self.password
            else:
                # Formulário automático (SAMLResponse, consentimento etc.)
                form = next((f for f in forms if f.find("input", attrs={"name": "SAMLResponse"})), forms[0])
                data = form_data(form)
            data.update(_submit_button(form))
            action = urljoin(str(resp.url), form.get("action") or str(resp.url))
            if (form.get("method") or "get").lower() == "post":
                # O IdP (WebSphere) rejeita o POST sem Referer/Origin de navegador.
                page = urlparse(str(resp.url))
                headers = {"Referer": str(resp.url), "Origin": f"{page.scheme}://{page.netloc}"}
                resp = self.client.post(action, data=data, headers=headers)
            else:
                resp = self.client.get(action, params=data)
        raise LoginError("Fluxo de login não terminou após várias etapas.")

    def _login_failure_message(self, soup, resp: httpx.Response) -> str:
        debug = STATE_DIR / "last_login_error.html"
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        debug.write_text(resp.text, encoding="utf-8")
        if soup.find("input", attrs={"name": "cAnswer"}):
            return (
                "O IdP minhaUFMG está pedindo CAPTCHA (normalmente após tentativas falhas). "
                "Faça login uma vez em https://virtual.ufmg.br/minhasturmas/ pelo navegador e tente de novo."
            )
        texts = [
            el.get_text(" ", strip=True)
            for el in soup.select("[class*=erro], [class*=error], [id*=erro], [id*=error], .alert, font[color]")
        ]
        texts += [t.strip() for t in soup.find_all(string=re.compile(r"inv[aá]lid|incorret|bloquead", re.I))]
        msg = "; ".join(dict.fromkeys(t for t in texts if t)) or "credenciais rejeitadas"
        hint = ""
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,49}", self.user or ""):
            hint = " (o login deve ter 3-50 caracteres: letras minúsculas, números e hífen, começando por letra)"
        return f"Falha no login minhaUFMG: {msg}{hint}. HTML salvo em {debug}"
