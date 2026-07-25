"""
API de leitura do Squad 2 — serve o front e expõe os artefatos de extração.

Roda no servidor SEM GPU. Lê do Supabase (que o lado GPU populou via
scripts/ingest_supabase.py). Não faz extração nem vetorização.

Rodar:
    pip install -r requirements.txt
    uvicorn api.main:app --reload
    # http://localhost:8000
"""
import os
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from supabase import create_client

load_dotenv()

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "front"
IMG_BUCKET = "images"
URL_TTL = 3600  # segundos de validade das URLs assinadas

app = FastAPI(
    title="Squad 2 — Vetorização Cientométrica",
    description="API de leitura dos PDFs extraídos (extração + vetorização são do lado GPU).",
    version="0.1.0",
)


def _signed(path: str) -> str | None:
    """URL temporária para um arquivo do bucket privado de imagens."""
    if not path:
        return None
    try:
        res = sb.storage.from_(IMG_BUCKET).create_signed_url(path, URL_TTL)
        return res.get("signedURL") or res.get("signedUrl")
    except Exception:
        return None


@app.get("/api/documents", tags=["documentos"])
def listar_documentos():
    """Catálogo para a Home: um item por PDF, com metadados e status do pipeline."""
    pdfs = (
        sb.table("pdfs")
        .select("id,pdf_file,total_pages,approved,extracted,vectorized,"
                "approved_at,extracted_at,vectorized_at,extraction_time_ms,pipeline")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    if not pdfs:
        return []

    ids = [p["id"] for p in pdfs]
    metas = (
        sb.table("article_metadata")
        .select("pdf_id,title,authors,journal,year,doi")
        .in_("pdf_id", ids)
        .execute()
        .data
    )
    por_pdf = {m["pdf_id"]: m for m in metas}

    # thumbnail = primeira página
    thumbs = (
        sb.table("page_images")
        .select("pdf_id,page_number,image_file")
        .in_("pdf_id", ids)
        .eq("page_number", 1)
        .execute()
        .data
    )
    por_thumb = {t["pdf_id"]: t["image_file"] for t in thumbs}

    # contadores por documento (tabelas/figuras/fórmulas) para a Home
    # NOTA: agrega no Python. Em escala (dezenas de milhares de blocos) trocar por
    #       uma view/RPC no Postgres que já devolva os totais agregados.
    agregados: dict[str, dict] = {i: {"tabelas": 0, "figuras": 0, "formulas": 0} for i in ids}
    blocos = (
        sb.table("page_blocks").select("pdf_id,block_type")
        .in_("pdf_id", ids).limit(100_000).execute().data
    )
    for b in blocos:
        alvo = agregados.get(b["pdf_id"])
        if alvo is None:
            continue
        t = b["block_type"]
        if t == "tabela":
            alvo["tabelas"] += 1
        elif t in ("grafico", "foto"):
            alvo["figuras"] += 1
        elif t == "formula":
            alvo["formulas"] += 1

    return [
        {
            **p,
            "meta": por_pdf.get(p["id"], {}),
            "thumbnail": _signed(por_thumb.get(p["id"], "")),
            "resumo": agregados.get(p["id"], {}),
        }
        for p in pdfs
    ]


@app.get("/api/document/{pdf_id}", tags=["documentos"])
def obter_documento(pdf_id: str):
    """Documento completo para a tela de Extração: páginas, imagens e blocos (bbox + markdown)."""
    try:
        UUID(pdf_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"'{pdf_id}' não é um id válido. Use o campo 'id' de /api/documents.",
        )

    pdf = sb.table("pdfs").select("*").eq("id", pdf_id).execute().data
    if not pdf:
        raise HTTPException(status_code=404, detail="PDF não encontrado")
    pdf = pdf[0]

    meta = sb.table("article_metadata").select("*").eq("pdf_id", pdf_id).execute().data
    try:
        imgs = (
            sb.table("page_images")
            .select("page_number,image_file,route,page_type,width,height")
            .eq("pdf_id", pdf_id).order("page_number").execute().data
        )
    except Exception:
        # schema sem as colunas de metadados por página
        imgs = (
            sb.table("page_images").select("page_number,image_file")
            .eq("pdf_id", pdf_id).order("page_number").execute().data
        )
    blocos = (
        sb.table("page_blocks").select("page_number,block_type,markdown_text,bbox,layout")
        .eq("pdf_id", pdf_id).order("page_number").execute().data
    )

    # agrupa blocos por página, no formato que o front já entende
    por_pagina: dict[int, list] = {}
    for b in blocos:
        por_pagina.setdefault(b["page_number"], []).append(b.get("layout") or {
            "tipo": b["block_type"], "md": b["markdown_text"], "bbox": b["bbox"],
        })

    paginas = [
        {
            "n": img["page_number"],
            "img_url": _signed(img["image_file"]),
            "rota": img.get("route"),          # docling | chandra
            "tipo": img.get("page_type"),      # organica | escaneada
            "w": img.get("width"),
            "h": img.get("height"),
            "blocos": por_pagina.get(img["page_number"], []),
        }
        for img in imgs
    ]

    return {
        **pdf,
        "meta": meta[0] if meta else {},
        "paginas": paginas,
        "total_blocos": len(blocos),
    }


@app.get("/api/health", tags=["infra"])
def health():
    """Sonda de saúde: confirma que a API responde e o Supabase está acessível."""
    try:
        sb.table("pdfs").select("id").limit(1).execute()
        return {"status": "ok", "supabase": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Supabase indisponível: {e}")


# ── front (SPA) ──────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")


if (WEB_DIR / "data").exists():
    app.mount("/data", StaticFiles(directory=WEB_DIR / "data"), name="data")
