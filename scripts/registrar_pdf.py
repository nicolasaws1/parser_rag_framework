"""
Registra um PDF aprovado que AINDA NÃO foi extraído.

É o estado em que um documento chega da Squad 1: metadados no banco, binário no
bucket, `extracted = False`. Nenhuma página nem bloco — isso só existe depois que
o parser roda no lado com GPU.

Uso:
    python scripts/registrar_pdf.py "Azevedo(2020)-Frontiers - Fernanda Bochi Dos Santos.pdf"
    python scripts/registrar_pdf.py --lista        # mostra os aprovados ainda fora do banco
"""
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import fitz
from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

PDF_DIR = Path.home() / "OneDrive" / "Área de Trabalho" / "SB100" / "PDFS aprovados"
slugify = lambda n: re.sub(r"[^a-z0-9]+", "-", Path(n).stem.lower()).strip("-")[:60]


def ja_no_banco() -> set[str]:
    return {p["pdf_file"] for p in sb.table("pdfs").select("pdf_file").execute().data}


def registrar(nome: str) -> None:
    origem = PDF_DIR / nome
    if not origem.exists():
        sys.exit(f"não achei o PDF: {origem}")
    slug = slugify(nome)
    destino = f"{slug}.pdf"
    if destino in ja_no_banco():
        sys.exit(f"'{slug}' já está no banco")

    doc = fitz.open(origem)
    n_paginas = doc.page_count
    meta = doc.metadata or {}
    doc.close()

    with open(origem, "rb") as f:
        sb.storage.from_("pdfs").upload(
            destino, f.read(), {"content-type": "application/pdf", "upsert": "true"})

    agora = datetime.now().isoformat(timespec="seconds")
    pdf_id = sb.table("pdfs").insert({
        "pdf_file": destino,
        "total_pages": n_paginas,
        "approved": True,   "approved_at": agora,
        "extracted": False, "extracted_at": None,     # <- ainda não passou pelo parser
        "vectorized": False,
    }).execute().data[0]["id"]

    sb.table("article_metadata").insert({
        "pdf_id": pdf_id,
        "title": (meta.get("title") or Path(nome).stem).strip(),
        "authors": (meta.get("author") or "").strip(),
        "journal": "", "year": None, "doi": "",
    }).execute()

    try:
        sb.table("audit_log").insert({
            "evento": "pdf_ingerido", "ator": "registrar_pdf.py", "alvo": pdf_id,
            "detalhe": {"slug": slug, "paginas_pdf": n_paginas, "extraido": False},
        }).execute()
    except Exception as e:
        print(f"    (auditoria não gravou: {e})")

    print(f"registrado: {slug}  |  {n_paginas} páginas  |  extracted=False  |  {pdf_id}")


def listar() -> None:
    dentro = ja_no_banco()
    fora = [p.name for p in sorted(PDF_DIR.glob("*.pdf")) if f"{slugify(p.name)}.pdf" not in dentro]
    print(f"{len(fora)} PDFs aprovados ainda fora do banco. Primeiros:")
    for n in fora[:15]:
        print("   ", n[:70])


if __name__ == "__main__":
    if "--lista" in sys.argv or len(sys.argv) < 2:
        listar()
    else:
        registrar(sys.argv[1])
