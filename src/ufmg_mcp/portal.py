"""Lista de turmas do portal Minhas Turmas (cada semestre vive em um Moodle diferente)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

PORTAL_URL = "https://virtual.ufmg.br/minhasturmas/"


@dataclass
class Turma:
    chave: str  # "<instância>:<id>", ex.: "20262:12368"
    nome: str
    grupo: str  # ex.: "2026/2o Semestre" ou "Colegiados/Turmas adicionais"
    base: str  # ex.: "https://virtual.ufmg.br/20262"
    course_id: int
    url: str


def parse_portal(html: str) -> list[Turma]:
    soup = BeautifulSoup(html, "lxml")
    turmas: list[Turma] = []
    for card in soup.select("div.card"):
        header = card.find(["h2", "h3", "h4", "h5"])
        grupo = header.get_text(" ", strip=True) if header else ""
        for a in card.select("a[href*='course/view.php']"):
            url = a["href"]
            parsed = urlparse(url)
            ids = parse_qs(parsed.query).get("id")
            m = re.match(r"(.*)/course/view\.php", parsed.path)
            if not ids or not m:
                continue
            instancia = m.group(1).strip("/")
            turmas.append(
                Turma(
                    chave=f"{instancia}:{ids[0]}",
                    nome=a.get_text(" ", strip=True),
                    grupo=grupo,
                    base=f"{parsed.scheme}://{parsed.netloc}{m.group(1)}",
                    course_id=int(ids[0]),
                    url=url,
                )
            )
    return turmas


def semestres(turmas: list[Turma]) -> list[str]:
    """Grupos de semestre em ordem do portal (mais recente primeiro)."""
    return list(dict.fromkeys(t.grupo for t in turmas if re.match(r"\d{4}/", t.grupo)))
