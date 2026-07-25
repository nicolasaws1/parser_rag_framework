"""
Inspeciona o schema real do Supabase (tabelas, colunas, tipos) e os buckets.

Usa o spec OpenAPI que o PostgREST expõe — funciona mesmo com as tabelas vazias.
Não imprime nenhuma credencial.

Uso:
    python scripts/inspect_schema.py
"""
import os
import json
import urllib.request

from dotenv import load_dotenv

load_dotenv()

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

TABELAS = ["pdfs", "article_metadata", "page_images", "page_blocks", "profiles"]


def openapi() -> dict:
    req = urllib.request.Request(
        f"{URL}/rest/v1/",
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main() -> None:
    spec = openapi()
    defs = spec.get("definitions") or spec.get("components", {}).get("schemas", {})

    print("=" * 62)
    print("SCHEMA REAL DO SUPABASE")
    print("=" * 62)

    for t in TABELAS:
        d = defs.get(t)
        if not d:
            print(f"\n### {t}  ❌ não encontrada")
            continue
        props = d.get("properties", {})
        req = set(d.get("required", []))
        print(f"\n### {t}  ({len(props)} colunas)")
        for col, info in props.items():
            tipo = info.get("format") or info.get("type", "?")
            flags = []
            if col in req:
                flags.append("NOT NULL")
            desc = (info.get("description") or "").replace("\n", " ")
            if "Primary Key" in desc:
                flags.append("PK")
            if "Foreign Key" in desc:
                fk = desc.split("Foreign Key to")[-1].strip().strip("`.")
                flags.append(f"FK->{fk}")
            print(f"    - {col:<28} {tipo:<26} {' '.join(flags)}")

    # buckets
    print("\n" + "=" * 62)
    try:
        from supabase import create_client

        sb = create_client(URL, KEY)
        print("BUCKETS:", ", ".join(b.name for b in sb.storage.list_buckets()) or "(nenhum)")
    except Exception as e:
        print("BUCKETS: erro ao listar —", e)

    print("=" * 62)
    print("\nCole esta saída no chat para eu ajustar o script de ingestão.")


if __name__ == "__main__":
    main()
