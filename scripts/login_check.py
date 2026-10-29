"""Teste rápido de ponta a ponta: login SSO, portal, token do Moodle e prazos.

Uso: uv run python scripts/login_check.py
"""

from ufmg_mcp import server
from ufmg_mcp.auth import load_dotenv

load_dotenv()
turmas = server.listar_turmas()
print(f"Login OK. {len(turmas)} turmas no semestre atual:")
for t in turmas:
    print(f"  {t['chave']:>14}  {t['nome']}")
print("\nPrazos (30 dias):")
for p in server.prazos(dias=30):
    print(f"  {p['quando']}  {p['atividade']}  [{p['turma']}]")
