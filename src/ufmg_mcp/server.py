"""Servidor MCP para o portal Minhas Turmas / Moodles da UFMG."""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .auth import UfmgSession, load_dotenv
from .files import extract_text, filename_from, html_to_text
from .moodle import MoodleClient, MoodleError
from .portal import PORTAL_URL, Turma, parse_portal, semestres

TZ = ZoneInfo("America/Sao_Paulo")
mcp = MCPServer("ufmg-minhas-turmas")
ESCRITA = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)

_session: UfmgSession | None = None
_clients: dict[str, MoodleClient] = {}
_turmas_cache: tuple[float, list[Turma]] | None = None


# ---------- infraestrutura ----------
def session() -> UfmgSession:
    global _session
    if _session is None:
        _session = UfmgSession()
    return _session


def client(base: str) -> MoodleClient:
    if base not in _clients:
        _clients[base] = MoodleClient(session(), base)
    return _clients[base]


def todas_turmas(refresh: bool = False) -> list[Turma]:
    global _turmas_cache
    if refresh or _turmas_cache is None or time.time() - _turmas_cache[0] > 600:
        resp = session().get(PORTAL_URL)
        _turmas_cache = (time.time(), parse_portal(resp.text))
    return _turmas_cache[1]


def turma(chave: str) -> Turma:
    for t in todas_turmas():
        if t.chave == chave:
            return t
    raise ValueError(f"Turma '{chave}' não encontrada. Use listar_turmas para ver as chaves (ex.: '20262:12368').")


def turmas_semestre_atual() -> list[Turma]:
    ts = todas_turmas()
    sems = semestres(ts)
    return [t for t in ts if sems and t.grupo == sems[0]]


def fmt(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M (%a)")


def limpar(html: str | None) -> str:
    return html_to_text(html) if html else ""


# ---------- leitura ----------
@mcp.tool()
def listar_turmas(semestre: str | None = None, todas: bool = False) -> list[dict]:
    """Lista as turmas do portal Minhas Turmas.

    Por padrão mostra só o semestre mais recente. `semestre` filtra pelo nome do grupo
    (ex.: "2026/1"); `todas=True` lista todos os semestres e colegiados.
    A `chave` retornada (ex.: "20262:12368") identifica a turma nas outras ferramentas.
    """
    ts = todas_turmas(refresh=True)
    if not todas:
        if semestre:
            ts = [t for t in ts if semestre in t.grupo]
        else:
            ts = turmas_semestre_atual()
    return [{"chave": t.chave, "nome": t.nome, "grupo": t.grupo, "url": t.url} for t in ts]


@mcp.tool()
def conteudo_turma(chave: str, incluir_descricoes: bool = False) -> dict:
    """Mostra as seções e atividades/recursos de uma turma (arquivos, tarefas, fóruns, links...).

    Cada módulo traz `cmid` (use em detalhes_tarefa/ler_forum) e, para arquivos, `arquivos`
    com a `url` a ser passada para ler_arquivo/baixar_arquivo.
    """
    t = turma(chave)
    secoes = client(t.base).ws("core_course_get_contents", courseid=t.course_id)
    out = []
    for sec in secoes:
        modulos = []
        for m in sec.get("modules", []):
            if not m.get("uservisible", True):
                continue
            item = {"cmid": m["id"], "tipo": m["modname"], "nome": m["name"], "url": m.get("url")}
            datas = {d["label"]: fmt(d["timestamp"]) for d in m.get("dates", [])}
            if datas:
                item["datas"] = datas
            if m.get("contents"):
                item["arquivos"] = [
                    {"nome": c["filename"], "url": c["fileurl"], "tamanho": c.get("filesize")}
                    for c in m["contents"]
                    if c.get("type") == "file"
                ] or [{"nome": c.get("filename"), "url": c["fileurl"]} for c in m["contents"] if c.get("type") == "url"]
            if incluir_descricoes and m.get("description"):
                item["descricao"] = limpar(m["description"])
            modulos.append(item)
        sumario = limpar(sec.get("summary"))
        if modulos or sumario:
            out.append({"secao": sec["name"], "resumo": sumario[:1000] or None, "modulos": modulos})
    return {"turma": t.nome, "secoes": out}


@mcp.tool()
def prazos(dias: int = 30, chave: str | None = None, incluir_atrasados: bool = False) -> list[dict]:
    """Atividades com prazo (tarefas, questionários etc.) nos próximos `dias`, ordenadas por data.

    Sem `chave`, busca em todas as turmas do semestre atual. `incluir_atrasados=True`
    inclui também os últimos 14 dias.
    """
    agora = int(time.time())
    inicio = agora - 14 * 86400 if incluir_atrasados else agora
    alvo = [turma(chave)] if chave else turmas_semestre_atual()
    por_base: dict[str, dict[int, Turma]] = {}
    for t in alvo:
        por_base.setdefault(t.base, {})[t.course_id] = t
    eventos = []
    for base, cursos in por_base.items():
        data = client(base).ws(
            "core_calendar_get_action_events_by_timesort",
            timesortfrom=inicio,
            timesortto=agora + dias * 86400,
            limitnum=50,
        )
        for ev in data.get("events", []):
            curso = ev.get("course") or {}
            t = cursos.get(curso.get("id"))
            if t is None:
                continue
            eventos.append(
                {
                    "quando": fmt(ev["timesort"]),
                    "timestamp": ev["timesort"],
                    "turma": t.nome,
                    "chave": t.chave,
                    "atividade": ev.get("activityname") or ev["name"],
                    "tipo": ev.get("modulename"),
                    "evento": ev["name"],
                    "cmid": _cmid_from_url(ev.get("url") or ""),
                    "atrasado": ev.get("overdue", False),
                    "acao": (ev.get("action") or {}).get("name"),
                    "url": ev.get("url"),
                }
            )
    eventos.sort(key=lambda e: e["timestamp"])
    return eventos


def _cmid_from_url(url: str) -> int | None:
    m = re.search(r"[?&]id=(\d+)", url)
    return int(m.group(1)) if m else None


@mcp.tool()
def ler_arquivo(chave: str, url: str, max_chars: int = 50000, inicio: int = 0) -> dict:
    """Baixa um arquivo da turma (PDF, DOCX, TXT, HTML...) e devolve o texto extraído.

    `url` vem de conteudo_turma (campo arquivos[].url). Use `inicio` para paginar textos longos.
    """
    t = turma(chave)
    resp = client(t.base).download(url)
    nome = filename_from(resp)
    texto = extract_text(resp.content, nome, resp.headers.get("content-type", ""))
    trecho = texto[inicio : inicio + max_chars]
    return {
        "arquivo": nome,
        "total_caracteres": len(texto),
        "inicio": inicio,
        "tem_mais": inicio + max_chars < len(texto),
        "texto": trecho,
    }


@mcp.tool()
def baixar_arquivo(chave: str, url: str, destino: str) -> dict:
    """Salva um arquivo da turma no disco. `destino` pode ser uma pasta ou caminho completo."""
    t = turma(chave)
    resp = client(t.base).download(url)
    alvo = Path(destino).expanduser()
    if alvo.is_dir() or destino.endswith(("/", "\\")):
        alvo.mkdir(parents=True, exist_ok=True)
        alvo = alvo / filename_from(resp)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_bytes(resp.content)
    return {"salvo_em": str(alvo.resolve()), "bytes": len(resp.content)}


def _assign(t: Turma, cmid: int) -> dict:
    data = client(t.base).ws("mod_assign_get_assignments", courseids=[t.course_id])
    for course in data.get("courses", []):
        for a in course.get("assignments", []):
            if a["cmid"] == cmid:
                return a
    raise ValueError(f"Tarefa com cmid {cmid} não encontrada na turma {t.chave}.")


def _status_resumo(status: dict) -> dict:
    last = status.get("lastattempt") or {}
    sub = last.get("submission") or {}
    arquivos, texto = [], None
    for plugin in sub.get("plugins", []):
        for area in plugin.get("fileareas", []):
            arquivos += [f["filename"] for f in area.get("files", [])]
        for field in plugin.get("editorfields", []):
            if field.get("text"):
                texto = limpar(field["text"])
    feedback = status.get("feedback") or {}
    comentarios = [
        limpar(f.get("text"))
        for p in feedback.get("plugins", [])
        for f in p.get("editorfields", [])
        if f.get("text")
    ]
    return {
        "status_envio": sub.get("status"),  # new / draft / submitted
        "status_avaliacao": last.get("gradingstatus"),
        "pode_editar": last.get("canedit"),
        "pode_enviar_para_avaliacao": last.get("cansubmit"),
        "arquivos_enviados": arquivos,
        "texto_enviado": texto,
        "nota": (feedback.get("gradefordisplay") and limpar(feedback["gradefordisplay"])) or None,
        "comentarios_professor": comentarios or None,
        "modificado_em": fmt(sub.get("timemodified")),
    }


@mcp.tool()
def detalhes_tarefa(chave: str, cmid: int) -> dict:
    """Enunciado, prazos, configuração de envio e o status do seu envio/nota de uma tarefa."""
    t = turma(chave)
    a = _assign(t, cmid)
    c = client(t.base)
    status = c.ws("mod_assign_get_submission_status", assignid=a["id"])
    configs = {(cfg["plugin"], cfg["name"]): cfg["value"] for cfg in a.get("configs", [])}
    return {
        "nome": a["name"],
        "enunciado": limpar(a.get("intro")),
        "anexos_enunciado": [
            {"nome": f["filename"], "url": f["fileurl"]} for f in a.get("introattachments", [])
        ],
        "abre_em": fmt(a.get("allowsubmissionsfromdate")),
        "prazo": fmt(a.get("duedate")),
        "data_limite": fmt(a.get("cutoffdate")),
        "aceita_arquivos": configs.get(("file", "enabled")) == "1",
        "max_arquivos": configs.get(("file", "maxfilesubmissions")),
        "tipos_aceitos": configs.get(("file", "filetypeslist")) or None,
        "aceita_texto_online": configs.get(("onlinetext", "enabled")) == "1",
        "exige_botao_enviar": bool(a.get("submissiondrafts")),
        "meu_envio": _status_resumo(status),
    }


@mcp.tool()
def ler_forum(chave: str, cmid: int, pagina: int = 0) -> dict:
    """Lista as discussões de um fórum (ex.: Avisos) da turma."""
    t = turma(chave)
    c = client(t.base)
    cm = c.course_module(cmid)
    data = c.ws("mod_forum_get_forum_discussions", forumid=cm["instance"], page=pagina, perpage=20)
    return {
        "forum": cm["name"],
        "discussoes": [
            {
                "discussion_id": d["discussion"],
                "post_id": d["id"],
                "assunto": d["subject"],
                "autor": d.get("userfullname"),
                "criado_em": fmt(d.get("created")),
                "ultima_resposta": fmt(d.get("timemodified")),
                "respostas": d.get("numreplies"),
                "mensagem": limpar(d.get("message"))[:600],
            }
            for d in data.get("discussions", [])
        ],
    }


@mcp.tool()
def ler_discussao(chave: str, discussion_id: int) -> list[dict]:
    """Lê todos os posts de uma discussão de fórum."""
    t = turma(chave)
    data = client(t.base).ws("mod_forum_get_discussion_posts", discussionid=discussion_id, sortby="created", sortdirection="ASC")
    return [
        {
            "post_id": p["id"],
            "responde_a": p.get("parentid"),
            "autor": (p.get("author") or {}).get("fullname"),
            "quando": fmt(p.get("timecreated")),
            "assunto": p.get("subject"),
            "mensagem": limpar(p.get("message")),
            "anexos": [{"nome": a["filename"], "url": a["url"]} for a in p.get("attachments", [])],
        }
        for p in data.get("posts", [])
    ]


@mcp.tool()
def notas(chave: str) -> dict:
    """Suas notas em cada item de avaliação da turma."""
    t = turma(chave)
    c = client(t.base)
    data = c.ws("gradereport_user_get_grade_items", courseid=t.course_id, userid=c.userid)
    itens = []
    for g in (data.get("usergrades") or [{}])[0].get("gradeitems", []):
        itens.append(
            {
                "item": g.get("itemname") or ("Total do curso" if g.get("itemtype") == "course" else g.get("itemtype")),
                "nota": html.unescape(g.get("gradeformatted") or ""),
                "intervalo": html.unescape(g.get("rangeformatted") or ""),
                "percentual": html.unescape(g.get("percentageformatted") or ""),
                "comentario": limpar(g.get("feedback")) or None,
            }
        )
    return {"turma": t.nome, "itens": itens}


# ---------- escrita ----------
@mcp.tool(annotations=ESCRITA)
def enviar_tarefa(
    chave: str,
    cmid: int,
    arquivos: list[str] | None = None,
    texto: str | None = None,
    finalizar: bool = False,
    confirmar: bool = False,
) -> dict:
    """Envia arquivos e/ou texto online para uma tarefa.

    ATENÇÃO: o envio SUBSTITUI os arquivos/texto já enviados nessa tarefa.
    Sem `confirmar=True` apenas mostra uma prévia do que seria feito — sempre mostre a prévia
    ao usuário e só confirme com a autorização explícita dele.
    `finalizar=True` também clica em "Enviar para avaliação" (pode impedir novas edições).
    """
    if not arquivos and texto is None:
        raise ValueError("Informe `arquivos` e/ou `texto`.")
    t = turma(chave)
    c = client(t.base)
    a = _assign(t, cmid)
    caminhos = [Path(p).expanduser() for p in (arquivos or [])]
    faltando = [str(p) for p in caminhos if not p.is_file()]
    if faltando:
        raise ValueError(f"Arquivos não encontrados: {faltando}")
    atual = _status_resumo(c.ws("mod_assign_get_submission_status", assignid=a["id"]))
    previa = {
        "tarefa": a["name"],
        "turma": t.nome,
        "prazo": fmt(a.get("duedate")),
        "arquivos_a_enviar": [f"{p.name} ({p.stat().st_size} bytes)" for p in caminhos],
        "texto_a_enviar": texto,
        "envio_atual_sera_substituido": {"arquivos": atual["arquivos_enviados"], "texto": atual["texto_enviado"]},
        "status_atual": atual["status_envio"],
        "pode_editar": atual["pode_editar"],
        "finalizar_envio_para_avaliacao": finalizar,
    }
    if not confirmar:
        return {"previa": previa, "aviso": "Nada foi enviado. Chame novamente com confirmar=True após o usuário aprovar."}
    if atual["pode_editar"] is False:
        raise MoodleError("O Moodle não permite editar este envio (prazo encerrado ou já avaliado/enviado).")

    plugindata: dict = {}
    if caminhos:
        itemid = 0
        for p in caminhos:
            itemid = c.upload_draft(p, itemid)
        plugindata["files_filemanager"] = itemid
    if texto is not None:
        plugindata["onlinetext_editor"] = {"text": texto, "format": 1, "itemid": 0}
    avisos = c.ws("mod_assign_save_submission", assignmentid=a["id"], plugindata=plugindata)
    if finalizar:
        avisos += c.ws("mod_assign_submit_for_grading", assignmentid=a["id"], acceptsubmissionstatement=1)
    novo = _status_resumo(c.ws("mod_assign_get_submission_status", assignid=a["id"]))
    return {"enviado": True, "avisos": avisos or None, "status": novo}


@mcp.tool(annotations=ESCRITA)
def responder_forum(chave: str, post_id: int, mensagem: str, assunto: str | None = None, confirmar: bool = False) -> dict:
    """Responde a um post de fórum. Sem `confirmar=True` apenas mostra a prévia.

    Mostre a prévia ao usuário e só confirme com a autorização explícita dele.
    """
    t = turma(chave)
    previa = {"turma": t.nome, "responde_post": post_id, "assunto": assunto or "(Re: assunto original)", "mensagem": mensagem}
    if not confirmar:
        return {"previa": previa, "aviso": "Nada foi publicado. Chame novamente com confirmar=True após o usuário aprovar."}
    params = {"postid": post_id, "message": mensagem, "messageformat": 1}
    if assunto:
        params["subject"] = assunto
    else:
        params["subject"] = "Re: " + (client(t.base).ws("mod_forum_get_discussion_post", postid=post_id)["post"]["subject"])
    data = client(t.base).ws("mod_forum_add_discussion_post", **params)
    return {"publicado": True, "post_id": data.get("postid"), "avisos": data.get("warnings") or None}


def main() -> None:
    load_dotenv()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run()


if __name__ == "__main__":
    main()
