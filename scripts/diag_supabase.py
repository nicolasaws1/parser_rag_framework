"""Diagnóstico da conexão Supabase — mostra status HTTP, nunca as chaves."""
import os
import httpx
from dotenv import load_dotenv

load_dotenv()
URL = os.environ["SUPABASE_URL"].rstrip("/")
SECRET = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ANON = os.environ.get("SUPABASE_ANON_KEY", "")


def mask(k: str) -> str:
    """Só o prefixo do formato, para identificar o TIPO de chave (nunca o valor)."""
    if not k:
        return "(vazia)"
    if k.startswith("sb_secret_"):
        return "novo formato: sb_secret_***"
    if k.startswith("sb_publishable_"):
        return "novo formato: sb_publishable_***"
    if k.startswith("eyJ"):
        return "formato legado JWT: eyJ***"
    return f"formato desconhecido ({len(k)} chars)"


print("URL      :", URL)
print("secret   :", mask(SECRET))
print("anon     :", mask(ANON))
print("=" * 60)

H = {"apikey": SECRET, "Authorization": f"Bearer {SECRET}"}

testes = [
    ("REST raiz (OpenAPI)", f"{URL}/rest/v1/"),
    ("tabela pdfs",         f"{URL}/rest/v1/pdfs?select=*&limit=1"),
    ("storage buckets",     f"{URL}/storage/v1/bucket"),
]

with httpx.Client(follow_redirects=True, timeout=30) as c:
    for nome, url in testes:
        try:
            r = c.get(url, headers=H)
            corpo = r.text[:200].replace("\n", " ")
            print(f"\n[{nome}]  HTTP {r.status_code}")
            print(f"   content-type: {r.headers.get('content-type', '?')}")
            print(f"   corpo: {corpo}")
        except Exception as e:
            print(f"\n[{nome}]  EXCEÇÃO: {type(e).__name__}: {e}")
