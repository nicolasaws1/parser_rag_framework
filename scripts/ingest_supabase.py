"""
Ingestão dos artefatos de extração -> Supabase (tabelas + buckets).

Lê front/data/<slug>/ (layout.json, document.md, pages/) + o PDF original e popula:
    tabelas : pdfs, article_metadata, page_images, page_blocks
    buckets : pdfs (PDF original), images (imagens de página)

Seed: marca os PDFs como aprovados + extraídos (como se já tivessem sido puxados
da API da Squad 01). vectorized fica False — é o próximo passo do pipeline.

Uso:
    python scripts/ingest_supabase.py           # pula o que já foi ingerido
    python scripts/ingest_supabase.py --force   # re-ingere (apaga e recria)
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from supabase import create_client

# console do Windows (cp1252) não imprime emoji — força UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "front" / "data"      # artefatos da extração
PDF_DIR = Path.home() / "Downloads"      # PDFs originais
PIPELINE = "hibrido: DocLayout-YOLO + Chandra OCR 2 + Docling + PyMuPDF"
FORCE = "--force" in sys.argv


def _int(v, default=None):
    """Converte para int com segurança ('2005' -> 2005, '—' -> default)."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _upload(bucket: str, path: str, local: Path, content_type: str) -> None:
    with open(local, "rb") as f:
        sb.storage.from_(bucket).upload(
            path, f.read(), {"content-type": content_type, "upsert": "true"}
        )


def ingest_doc(entry: dict) -> None:
    slug = entry["slug"]
    doc_dir = DATA_DIR / slug
    lay = json.loads((doc_dir / "layout.json").read_text(encoding="utf-8"))
    md = (doc_dir / "document.md").read_text(encoding="utf-8")
    meta = lay.get("meta", {})
    paginas = lay.get("paginas", [])
    agora = datetime.now().isoformat(timespec="seconds")   # coluna é timestamp sem tz
    pdf_path = f"{slug}.pdf"

    # idempotência: já existe registro para este PDF?
    existente = sb.table("pdfs").select("id").eq("pdf_file", pdf_path).execute().data
    if existente:
        if not FORCE:
            print(f"    ⏭️  {slug} já ingerido (use --force para refazer)")
            return
        sb.table("pdfs").delete().eq("pdf_file", pdf_path).execute()  # FKs em cascata
        print(f"    ♻️  {slug}: registro anterior removido (--force)")

    # 1) PDF original -> bucket "pdfs"
    pdf_local = PDF_DIR / entry["arquivo"]
    if pdf_local.exists():
        _upload("pdfs", pdf_path, pdf_local, "application/pdf")
    else:
        print(f"    ⚠️  PDF original não encontrado ({pdf_local.name}); registro segue sem o binário")

    # 2) tabela pdfs
    tempo_ms = _int(round((lay.get("tempo_s") or 0) * 1000), None) or None
    pdf_id = sb.table("pdfs").insert({
        "pdf_file": pdf_path,
        "markdown": md,
        "total_pages": entry.get("n_paginas"),
        "approved": True,      "approved_at": agora,
        "extracted": True,     "extracted_at": agora,
        "vectorized": False,   "vectorized_at": None,
        "extraction_time_ms": tempo_ms,
        "pipeline": PIPELINE,
    }).execute().data[0]["id"]

    # 3) article_metadata
    sb.table("article_metadata").insert({
        "pdf_id": pdf_id,
        "title": meta.get("titulo"),
        "authors": meta.get("autores"),
        "journal": meta.get("periodico"),
        "year": _int(meta.get("ano")),
        "doi": meta.get("doi"),
    }).execute()

    # 4) page_images (+ upload no bucket "images")
    imgs = []
    for pg in paginas:
        local = doc_dir / pg["img"]                 # pages/pNNN.jpg
        remoto = f"{slug}/{pg['img']}"
        if local.exists():
            _upload("images", remoto, local, "image/jpeg")
        imgs.append({"pdf_id": pdf_id, "page_number": pg["n"], "image_file": remoto})
    if imgs:
        sb.table("page_images").insert(imgs).execute()

    # 5) page_blocks — layout por bloco (bbox + markdown + bloco completo)
    blocos = [
        {
            "pdf_id": pdf_id,
            "page_number": pg["n"],
            "block_type": b.get("tipo"),
            "markdown_text": b.get("md"),
            "bbox": b.get("bbox"),      # jsonb
            "layout": b,                # jsonb: bloco completo (tabela, fig, caption...)
        }
        for pg in paginas for b in pg.get("blocos", [])
    ]
    for i in range(0, len(blocos), 500):            # insere em lotes
        sb.table("page_blocks").insert(blocos[i:i + 500]).execute()

    print(f"    ✅ {slug}: {len(paginas)} páginas | {len(blocos)} blocos | pdf_id={pdf_id}")


def main() -> None:
    index = json.loads((DATA_DIR / "index.json").read_text(encoding="utf-8"))
    print(f"Ingerindo {len(index)} documento(s) para o Supabase...\n")
    for entry in index:
        try:
            ingest_doc(entry)
        except Exception as e:
            print(f"    ❌ ERRO em {entry['slug']}: {e}")
    print("\n🎉 Ingestão concluída.")


if __name__ == "__main__":
    main()
