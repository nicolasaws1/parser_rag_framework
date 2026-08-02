"""
Confere que o banco está fechado para acesso direto.

Tenta ler cada tabela com a chave **anon** — a que um atacante teria se o
Supabase ficasse exposto na rede. Qualquer linha devolvida é falha.

    python scripts/checar_rls.py

Sai com código 1 se algo estiver aberto, para poder entrar num pipeline.
Rodar depois de aplicar supabase/007_rls.sql, e de novo depois de qualquer
migração que crie tabela — `alter default privileges` cobre o GRANT, mas não
impede alguém de escrever uma política `using (true)` sem pensar.
"""
import os
import sys

import requests
from dotenv import load_dotenv
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
URL = os.environ["SUPABASE_URL"]
ANON = os.environ["SUPABASE_ANON_KEY"]

TABELAS = ["pdfs", "page_images", "page_blocks", "article_metadata", "profiles",
           "document_edits", "audit_log", "vectorizations", "document_block_counts"]


def main() -> None:
    h = {"apikey": ANON, "Authorization": f"Bearer {ANON}"}
    abertas = []
    print(f"lendo {URL} com a chave anon\n")
    for t in TABELAS:
        try:
            r = requests.get(f"{URL}/rest/v1/{t}?select=*&limit=1", headers=h, timeout=30)
            corpo = r.json()
        except Exception as e:
            print(f"  {t:24} erro de rede: {str(e)[:40]}")
            continue
        linhas = len(corpo) if isinstance(corpo, list) else 0
        if linhas:
            print(f"  {t:24} {r.status_code}  ABERTA — devolveu {linhas} linha(s)")
            abertas.append(t)
        elif r.status_code in (401, 403):
            print(f"  {t:24} {r.status_code}  fechada (sem permissão)")
        elif isinstance(corpo, list):
            # 200 com zero linhas: pode ser RLS sem política OU tabela vazia.
            # Não dá para distinguir daqui, e uma tabela que hoje está vazia
            # amanhã tem dados — por isso o 007 também revoga o GRANT.
            print(f"  {t:24} {r.status_code}  vazia — confira que o revoke passou")
        else:
            print(f"  {t:24} {r.status_code}  {str(corpo)[:46]}")

    print()
    if abertas:
        print(f"FALHOU: {len(abertas)} tabela(s) legíveis por anon: {', '.join(abertas)}")
        sys.exit(1)
    print("OK: nenhuma tabela devolveu dados para a chave anon")


if __name__ == "__main__":
    main()
