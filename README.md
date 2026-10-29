# ufmg-mcp

Servidor MCP para gerenciar suas turmas da UFMG pelo Claude. Ele usa o [Portal Minhas Turmas](https://virtual.ufmg.br/minhasturmas/) e os Moodles de cada semestre (`virtual.ufmg.br/20262`, `/20261`, …).

## Como funciona

1. **Login SSO.** O servidor faz o login SAML no IdP minhaUFMG (`sistemas.ufmg.br/idp`) com seu usuário e senha, igual ao navegador. Os cookies ficam em `~/.ufmg_mcp/cookies.json`.
2. **Lista de turmas.** Vem do portal Minhas Turmas. Cada turma é identificada por uma chave `<instância>:<id>`, por exemplo `20262:12368`.
3. **Conteúdo.** Para cada Moodle, o servidor obtém o token do **app mobile oficial** via `admin/tool/mobile/launch.php`, o mesmo fluxo do app Moodle com SSO. Com ele usa a API REST oficial, sem ler HTML. Os tokens ficam em `~/.ufmg_mcp/tokens.json`.

## Ferramentas

| Ferramenta | O que faz |
|---|---|
| `listar_turmas` | Turmas do semestre atual (ou `semestre="2026/1"`, `todas=True`) |
| `conteudo_turma` | Seções, atividades e arquivos de uma turma |
| `prazos` | Próximas entregas de todas as turmas do semestre, ordenadas por data |
| `ler_arquivo` | Texto de PDF, DOCX, PPTX, TXT ou HTML da turma |
| `baixar_arquivo` | Salva um arquivo no disco |
| `detalhes_tarefa` | Enunciado, prazos, regras de envio, seu status, nota e feedback |
| `ler_forum` / `ler_discussao` | Avisos e fóruns |
| `notas` | Seu quadro de notas |
| `enviar_tarefa` ✍️ | Envia arquivos e/ou texto (com `finalizar=True`, também "Enviar para avaliação") |
| `responder_forum` ✍️ | Responde a um post |

As ferramentas ✍️ só agem com `confirmar=True`. Sem isso, devolvem uma **prévia**: o que vai ser enviado e o que vai ser substituído. O envio de tarefa **substitui** os arquivos já enviados.

## Instalação

Requer [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp .env.example .env   # preencha UFMG_USER (login minhaUFMG, minúsculo) e UFMG_PASSWORD
uv run python scripts/login_check.py   # deve listar suas turmas e prazos
```

Para registrar no Claude Code (as credenciais são lidas do `.env` do projeto):

```sh
claude mcp add ufmg-turmas --scope user -- uv --directory C:/caminho/para/ufmg_minhas_turmas_mcp run ufmg-mcp
```

No Claude Desktop, use o mesmo comando em `claude_desktop_config.json`:
`"command": "uv", "args": ["--directory", "<repo>", "run", "ufmg-mcp"]`.

## Notas

- **CAPTCHA.** Depois de várias senhas erradas, o IdP passa a pedir CAPTCHA, e o servidor avisa em vez de insistir. Nesse caso, faça um login pelo navegador.
- **Cabeçalhos de navegador.** O IdP (WebSphere) rejeita o login quando faltam `Referer`/`Origin`, por isso o cliente imita um navegador.
- **Segurança.** O `.env`, os cookies e os tokens dão acesso à sua conta, então não os compartilhe. Apague `~/.ufmg_mcp/` para encerrar a sessão.
- **Testes offline:** `uv run pytest`.
