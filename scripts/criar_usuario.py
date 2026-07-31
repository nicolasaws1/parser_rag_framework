"""
Cria um usuário direto no Supabase Auth, pela linha de comando.

Existe por um motivo só: o cadastro no site é INTERNO — só quem já tem conta cria
outra. Com o banco vazio, ninguém conseguiria entrar para criar a primeira. Este
script resolve esse impasse e depois disso o cadastro é pelo site.

    python scripts/criar_usuario.py nicolas@exemplo.com "Nicolas" admin

A senha NÃO vai por argumento (ficaria no histórico do shell): é pedida na hora,
sem eco. Cargos: admin, curador, leitor.
"""
import getpass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

CARGOS = ("admin", "curador", "leitor")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    email = sys.argv[1].strip()
    nome = sys.argv[2].strip() if len(sys.argv) > 2 else email.split("@")[0]
    cargo = (sys.argv[3].strip() if len(sys.argv) > 3 else "admin").lower()
    if cargo not in CARGOS:
        sys.exit(f"cargo deve ser um de: {', '.join(CARGOS)}")

    senha = getpass.getpass("senha (mín. 8 caracteres, não aparece na tela): ")
    if len(senha) < 8:
        sys.exit("senha curta demais")
    if senha != getpass.getpass("repita a senha: "):
        sys.exit("as senhas não conferem")

    try:
        criado = sb.auth.admin.create_user(
            {"email": email, "password": senha, "email_confirm": True})
    except Exception as e:
        sys.exit(f"não deu para criar no Auth: {str(e)[:200]}")

    uid = criado.user.id
    sb.table("profiles").upsert({"id": uid, "name": nome, "role": cargo}).execute()
    try:
        sb.table("audit_log").insert({
            "evento": "integrante_cadastrado", "ator": "criar_usuario.py", "alvo": uid,
            "detalhe": {"email": email, "cargo": cargo, "via": "linha de comando"},
        }).execute()
    except Exception:
        pass
    print(f"criado: {email}  |  {nome}  |  cargo {cargo}  |  id {uid}")
    print("A partir daqui, novos integrantes são cadastrados pelo site.")


if __name__ == "__main__":
    main()
