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

# onde procurar o PDF original (para subir ao bucket). Aceita PDF_DIR no ambiente;
# senão tenta os lugares onde os PDFs aprovados costumam estar.
PDF_DIRS = ([Path(os.environ["PDF_DIR"])] if os.environ.get("PDF_DIR") else []) + [
    Path.home() / "OneDrive" / "Área de Trabalho" / "SB100" / "PDFS aprovados",
    Path.home() / "Downloads",
]


def achar_pdf(nome: str) -> Path | None:
    for d in PDF_DIRS:
        p = d / nome
        if p.exists():
            return p
    return None


PIPELINE = "hibrido: DocLayout-YOLO + Chandra OCR 2 + Docling + PyMuPDF"
FORCE = "--force" in sys.argv

# pasta dos artefatos: 1º argumento, ou front/data por padrão
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
DATA_DIR = Path(_args[0]) if _args else ROOT / "front" / "data"


def _limpar(v):
    """Remove bytes NUL (\\u0000) — o Postgres rejeita em text e jsonb.
    Alguns PDFs trazem NUL na camada de texto; sem isso o insert falha (código 22P05)."""
    if isinstance(v, str):
        return v.replace("\x00", "")
    if isinstance(v, dict):
        return {k: _limpar(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_limpar(x) for x in v]
    return v


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
    pdf_local = achar_pdf(entry["arquivo"])
    if pdf_local:
        _upload("pdfs", pdf_path, pdf_local, "application/pdf")
    else:
        print(f"    ⚠️  PDF original não encontrado ({entry['arquivo'][:44]}); registro segue sem o binário")

    # 2) tabela pdfs
    tempo_ms = _int(round((lay.get("tempo_s") or 0) * 1000), None) or None
    pdf_id = sb.table("pdfs").insert({
        "pdf_file": pdf_path,
        "markdown": _limpar(md),
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
        "title": _limpar(meta.get("titulo")),
        "authors": _limpar(meta.get("autores")),
        "journal": _limpar(meta.get("periodico")),
        "year": _int(meta.get("ano")),
        "doi": _limpar(meta.get("doi")),
    }).execute()

    # 4) page_images (+ upload no bucket "images") + metadados da página
    imgs = []
    for pg in paginas:
        local = doc_dir / pg["img"]                 # pages/pNNN.jpg
        remoto = f"{slug}/{pg['img']}"
        if local.exists():
            _upload("images", remoto, local, "image/jpeg")
        imgs.append({
            "pdf_id": pdf_id,
            "page_number": pg["n"],
            "image_file": remoto,
            # metadados por página (colunas opcionais — ver COLUNAS_PAGINA)
            "route": pg.get("rota"),
            "page_type": pg.get("tipo"),
            "width": pg.get("w"),
            "height": pg.get("h"),
        })
    if imgs:
        try:
            sb.table("page_images").insert(imgs).execute()
        except Exception as e:
            if "column" not in str(e).lower():
                raise
            # colunas de metadados ainda não existem no schema -> insere o básico
            print("    ⚠️  page_images sem as colunas de metadados; gravando só a imagem")
            sb.table("page_images").insert(
                [{k: v for k, v in i.items() if k in ("pdf_id", "page_number", "image_file")}
                 for i in imgs]
            ).execute()

    # 5) page_blocks — layout por bloco (bbox + markdown + bloco completo)
    blocos = [
        {
            "pdf_id": pdf_id,
            "page_number": pg["n"],
            "block_type": b.get("tipo"),
            "markdown_text": _limpar(b.get("md")),
            "bbox": b.get("bbox"),      # jsonb
            "layout": _limpar(b),                # jsonb: bloco completo (tabela, fig, caption...)
        }
        for pg in paginas for b in pg.get("blocos", [])
    ]
    for i in range(0, len(blocos), 500):            # insere em lotes
        sb.table("page_blocks").insert(blocos[i:i + 500]).execute()

    try:
        sb.table("audit_log").insert({
            "evento": "pdf_ingerido", "ator": "ingest_supabase.py", "alvo": pdf_id,
            "detalhe": {"slug": slug, "paginas_pdf": entry.get("n_paginas"),
                        "blocos": len(blocos)},
        }).execute()
    except Exception as e:
        print(f"    (auditoria não gravou: {e})")
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
