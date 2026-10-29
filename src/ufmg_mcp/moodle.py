"""Cliente da API REST de um Moodle da UFMG, autenticado com o token do app mobile obtido via SSO."""

from __future__ import annotations

import base64
import json
import re
import secrets
from pathlib import Path

from .auth import STATE_DIR, UfmgSession

TOKEN_FILE = STATE_DIR / "tokens.json"


class MoodleError(RuntimeError):
    pass


def _flatten(params: dict, prefix: str = "") -> dict[str, str]:
    """Converte dict/list aninhados no formato do Moodle REST (a[0][b]=...)."""
    out: dict[str, str] = {}
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten(value, name))
        elif isinstance(value, (list, tuple)):
            out.update(_flatten(dict(enumerate(value)), name))
        elif isinstance(value, bool):
            out[name] = "1" if value else "0"
        elif value is not None:
            out[name] = str(value)
    return out


def _load_tokens() -> dict[str, str]:
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_token(base: str, token: str) -> None:
    tokens = _load_tokens()
    tokens[base] = token
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens), encoding="utf-8")


class MoodleClient:
    def __init__(self, session: UfmgSession, base: str):
        self.s = session
        self.base = base.rstrip("/")
        self.token: str | None = _load_tokens().get(self.base)
        self._userid: int | None = None

    # ---------- token ----------
    def _fetch_token(self) -> str:
        """Obtém o token pelo launch.php do app mobile (o SSO é completado pela sessão)."""
        self.s.get(self.base + "/my/")  # garante sessão Moodle ativa
        passport = str(secrets.randbelow(10**9))
        url = f"{self.base}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport={passport}&urlscheme=moodlemobile"
        resp = self.s.client.get(url, follow_redirects=False)
        for _ in range(5):
            if not resp.is_redirect or resp.headers.get("location", "").startswith("moodlemobile://"):
                break
            resp = self.s.client.get(resp.next_request.url, follow_redirects=False)
        m = re.search(r"moodlemobile://token=([A-Za-z0-9+/=]+)", resp.headers.get("location", "") + resp.text)
        if not m:
            raise MoodleError(f"Não foi possível obter o token do app mobile em {self.base}.")
        parts = base64.b64decode(m.group(1)).decode().split(":::")
        if len(parts) < 2:
            raise MoodleError("Resposta inesperada do launch.php.")
        self.token = parts[1]
        _save_token(self.base, self.token)
        return self.token

    # ---------- chamadas ----------
    def ws(self, function: str, _retry: bool = True, **params):
        if not self.token:
            self._fetch_token()
        data = {"wstoken": self.token, "wsfunction": function, "moodlewsrestformat": "json"}
        data.update(_flatten(params))
        resp = self.s.client.post(f"{self.base}/webservice/rest/server.php", data=data)
        try:
            result = resp.json()
        except ValueError as e:
            raise MoodleError(f"Resposta inválida de {function}: {resp.text[:300]}") from e
        if isinstance(result, dict) and result.get("exception"):
            if result.get("errorcode") in ("invalidtoken", "accessexception") and _retry:
                self._fetch_token()
                return self.ws(function, _retry=False, **params)
            raise MoodleError(f"{function}: {result.get('errorcode')}: {result.get('message')}")
        return result

    @property
    def userid(self) -> int:
        if self._userid is None:
            self._userid = self.ws("core_webservice_get_site_info")["userid"]
        return self._userid

    def download(self, url: str):
        """Baixa um arquivo do Moodle (pluginfile recebe o token; o resto usa a sessão)."""
        if "/pluginfile.php" in url:
            if not self.token:
                self._fetch_token()
            url = url.replace("/webservice/pluginfile.php", "/pluginfile.php").replace(
                "/pluginfile.php", "/webservice/pluginfile.php"
            )
            sep = "&" if "?" in url else "?"
            resp = self.s.client.get(f"{url}{sep}token={self.token}")
            if resp.status_code in (401, 403):
                self._fetch_token()
                resp = self.s.client.get(f"{url.split('?')[0]}?token={self.token}")
        else:
            resp = self.s.get(url)
        resp.raise_for_status()
        return resp

    def upload_draft(self, path: Path, itemid: int = 0) -> int:
        """Envia um arquivo para a área de rascunho do usuário; retorna o itemid."""
        if not self.token:
            self._fetch_token()
        with open(path, "rb") as fh:
            resp = self.s.client.post(
                f"{self.base}/webservice/upload.php",
                data={"token": self.token, "filearea": "draft", "itemid": str(itemid)},
                files={"file_1": (path.name, fh)},
            )
        result = resp.json()
        if isinstance(result, dict) and (result.get("error") or result.get("exception")):
            raise MoodleError(f"Upload falhou: {result.get('error') or result.get('message')}")
        return result[0]["itemid"]

    # ---------- alto nível ----------
    def course_module(self, cmid: int) -> dict:
        return self.ws("core_course_get_course_module", cmid=cmid)["cm"]
