"""
Baixa PDFs da API de curadoria e registra no Supabase, prontos para extração.

Fecha o ciclo: a API dá o metadado E o binário, então o servidor não depende de
nenhuma pasta local.

    python scripts/baixar_da_curadoria.py --lista        # o que falta baixar
    python scripts/baixar_da_curadoria.py --um           # baixa 1 (teste)
    python scripts/baixar_da_curadoria.py --todos        # baixa tudo que falta
    python scripts/baixar_da_curadoria.py --categoria "citros e cana"

Um a um, de propósito: `GET /api/download-all` devolve HTTP 524 (timeout do
Cloudflare montando o zip). Cada PDF sozinho leva ~0,2 s e nunca chega perto do
limite do proxy — e dá para retomar de onde parou.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import fitz
import requests
from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from api import curadoria  # noqa: E402  (precisa do .env carregado antes)

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
API_BASE = os.environ["CURATION_API_URL"]


def token() -> str:
    r = requests.post(f"{API_BASE}/api/login",
                      json={"username": os.environ["CURATION_API_USER"],
                            "password": os.environ["CURATION_API_PASSWORD"]}, timeout=30)
    r.raise_for_status()
    t = next((v for v in r.json().values() if isinstance(v, str) and v.startswith("eyJ")), None)
    if not t:
        sys.exit("a API respondeu sem token JWT")
    return t


def baixar_pdf(nome: str, tok: str) -> bytes:
    r = requests.get(f"{API_BASE}/api/documents/{requests.utils.quote(nome)}",
                     headers={"Authorization": f"Bearer {tok}"}, timeout=180)
    r.raise_for_status()
    dados = r.content
    if not dados.startswith(b"%PDF"):
        raise ValueError(f"a resposta não é um PDF (começa com {dados[:8]!r})")
    return dados


def registrar(nome_api: str, artigo: dict, dados: bytes) -> str:
    """Cria pdfs + article_metadata e sobe o binário. Devolve o pdf_id."""
    slug = curadoria.slug_de(nome_api)                      # 'alva-2005-nue.pdf'
    doc = fitz.open(stream=dados, filetype="pdf")
    n_paginas = doc.page_count
    doc.close()

    sb.storage.from_("pdfs").upload(
        slug, dados, {"content-type": "application/pdf", "upsert": "true"})

    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    linha = {"pdf_file": slug, "total_pages": n_paginas,
             "approved": True, "approved_at": agora,
             "extracted": False, "vectorized": False,
             "curation_status": curadoria.texto(artigo.get("status")) or "approved",
             # nome exato na API: é por ele que se rebaixa o binário depois
             "document_url": nome_api}
    try:
        pdf_id = sb.table("pdfs").insert(linha).execute().data[0]["id"]
    except Exception as e:
        # colunas variam entre migrações; mantém só o mínimo necessário
        print(f"    (insert completo falhou, tentando mínimo: {str(e)[:70]})")
        pdf_id = sb.table("pdfs").insert(
            {"pdf_file": slug, "total_pages": n_paginas, "extracted": False}
        ).execute().data[0]["id"]

    meta = {k: curadoria.texto(artigo.get(orig)) for k, orig in curadoria.CAMPOS.items()}
    try:
        meta["year"] = int(curadoria.texto(artigo.get("year"))[:4])
    except ValueError:
        meta["year"] = None
    meta.update({"pdf_id": pdf_id, "raw": artigo,
                 "api_id": curadoria.texto(artigo.get("_id")), "synced_at": agora})
    sb.table("article_metadata").insert(meta).execute()

    try:
        sb.table("audit_log").insert({
            "evento": "pdf_ingerido", "ator": "baixar_da_curadoria.py", "alvo": pdf_id,
            "detalhe": {"slug": slug, "paginas_pdf": n_paginas, "extraido": False,
                        "origem": "API de curadoria"},
        }).execute()
    except Exception as e:
        print(f"    (auditoria não gravou: {e})")
    return pdf_id


def faltando(categoria: str | None) -> list[tuple[str, dict]]:
    aprov = curadoria.aprovados(curadoria.baixar())
    try:
        no_banco = {p["pdf_file"] for p in sb.table("pdfs").select("pdf_file").execute().data}
    except Exception:
        no_banco = set()
    fora = []
    for nome, art in sorted(aprov.items()):
        if curadoria.slug_de(nome) in no_banco:
            continue
        if categoria and curadoria.texto(art.get("category")).lower() != categoria.lower():
            continue
        fora.append((nome, art))
    return fora


def main() -> None:
    cat = None
    if "--categoria" in sys.argv:
        cat = sys.argv[sys.argv.index("--categoria") + 1]

    fila = faltando(cat)
    print(f"faltam baixar: {len(fila)}" + (f"  (categoria '{cat}')" if cat else ""))
    if "--lista" in sys.argv or not any(a in sys.argv for a in ("--um", "--todos")):
        for nome, art in fila[:15]:
            print(f"   {curadoria.texto(art.get('category'))[:16]:<18} {nome[:60]}")
        if len(fila) > 15:
            print(f"   ... e outros {len(fila)-15}")
        print("\n(use --um para baixar um, ou --todos)")
        return

    alvo = fila[:1] if "--um" in sys.argv else fila
    tok = token()
    ok = erros = 0
    for nome, art in alvo:
        try:
            t0 = datetime.now()
            dados = baixar_pdf(nome, tok)
            pdf_id = registrar(nome, art, dados)
            dt = (datetime.now() - t0).total_seconds()
            print(f"   OK  {len(dados)/1024:>7.0f} KB  {dt:>4.1f}s  {nome[:46]}  -> {pdf_id[:8]}")
            ok += 1
        except Exception as e:
            print(f"   ERRO {nome[:52]}: {str(e)[:90]}")
            erros += 1
    print(f"\nbaixados: {ok}   erros: {erros}")


if __name__ == "__main__":
    main()
