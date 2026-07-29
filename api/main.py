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
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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
    blocos: list[dict] = []
    passo = 1000                     # o PostgREST limita a resposta; pagina até esgotar
    inicio = 0
    while True:
        lote = (
            sb.table("page_blocks").select("pdf_id,block_type")
            .in_("pdf_id", ids).range(inicio, inicio + passo - 1).execute().data
        )
        blocos.extend(lote)
        if len(lote) < passo:
            break
        inicio += passo
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
def obter_documento(pdf_id: str, pedido: Request):
    """Documento completo para a tela de Extração: páginas, imagens e blocos (bbox + markdown)."""
    limitar(pedido, "leitura", pdf_id)
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
    # pagina até esgotar: o PostgREST corta a resposta em 1000 linhas, e um documento
    # grande (o Boletim 100 tem 5332 blocos) apareceria truncado no site
    blocos: list[dict] = []
    passo, inicio = 1000, 0
    while True:
        lote = (
            sb.table("page_blocks").select("page_number,block_type,markdown_text,bbox,layout")
            .eq("pdf_id", pdf_id).order("page_number")
            .range(inicio, inicio + passo - 1).execute().data
        )
        blocos.extend(lote)
        if len(lote) < passo:
            break
        inicio += passo
    # O Postgres não garante ordem dentro da página, e a ordem de leitura importa:
    # sem isto os blocos saem embaralhados (o título de seção antes do parágrafo que
    # o antecede). O id do bloco ('pN-bM') carrega a posição original.
    def _indice(b):
        m = re.search(r"-b(\d+)$", ((b.get("layout") or {}).get("id") or ""))
        return int(m.group(1)) if m else 10**6

    blocos.sort(key=lambda b: (b["page_number"], _indice(b)))

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

    # se houver edição manual, ela substitui o que a extração produziu; o original
    # continua intacto no banco e volta assim que a edição for descartada
    ed = _edicao_de(pdf_id)
    if ed and ed.get("layout"):
        por_n = {p["n"]: p for p in ed["layout"] if isinstance(p, dict) and "n" in p}
        for pg in paginas:
            e = por_n.get(pg["n"])
            if e and e.get("blocos") is not None:
                pg["blocos"] = e["blocos"]

    return {
        **pdf,
        "meta": meta[0] if meta else {},
        "paginas": paginas,
        "total_blocos": sum(len(p["blocos"]) for p in paginas),
        "editado": bool(ed),
        "edited_at": (ed or {}).get("edited_at"),
        "markdown": (ed or {}).get("markdown") or pdf.get("markdown"),
    }


# ── edição manual ────────────────────────────────────────────────────────────
# A extração original é imutável (pdfs.markdown, page_blocks). Cada documento tem
# NO MÁXIMO UMA linha em document_edits, sobrescrita a cada alteração — sem
# histórico e sem cópias novas, conforme decidido. `edited_at` diz quando foi a
# última. Ver supabase/002_edicoes.sql.

class Edicao(BaseModel):
    """`layout` traz SÓ as páginas alteradas — o servidor funde no que já existe."""
    layout: list[dict[str, Any]] | None = None
    markdown: str | None = None
    edited_by: str | None = None


# Por (cliente, documento): 5 edições/min e 5 leituras/min. Segura engano (script
# em laço) e abuso, já que o login do front ainda não autentica de verdade.
# Em memória: com mais de um worker do uvicorn o limite vale por worker — quando
# for para produção com vários, trocar por Redis ou pelo rate limit do proxy.
LIMITES = {"edicao": 5, "leitura": 5}
_JANELA = 60.0
_marcas: dict[tuple, list[float]] = {}


def limitar(pedido: Request, chave: str, alvo: str) -> None:
    from time import monotonic
    quem = (pedido.client.host if pedido.client else "?", chave, alvo)
    agora = monotonic()
    marcas = [t for t in _marcas.get(quem, []) if agora - t < _JANELA]
    if len(marcas) >= LIMITES[chave]:
        espera = int(_JANELA - (agora - marcas[0])) + 1
        _marcas[quem] = marcas
        raise HTTPException(429, f"limite de {LIMITES[chave]} por minuto atingido "
                                 f"para este documento; tente em {espera}s",
                            headers={"Retry-After": str(espera)})
    marcas.append(agora)
    _marcas[quem] = marcas


def _edicao_de(pdf_id: str) -> dict | None:
    """Edição do documento, se houver. Devolve None se a tabela ainda não existe."""
    try:
        r = sb.table("document_edits").select("*").eq("pdf_id", pdf_id).execute().data
        return r[0] if r else None
    except Exception:
        return None            # migração 002 ainda não aplicada


@app.get("/api/document/{pdf_id}/edicao", tags=["edição"])
def obter_edicao(pdf_id: str):
    """Metadados da edição: se existe e quando foi feita."""
    e = _edicao_de(pdf_id)
    if not e:
        return {"editado": False}
    return {"editado": True, "edited_at": e.get("edited_at"),
            "edited_by": e.get("edited_by"),
            "tem_layout": e.get("layout") is not None,
            "tem_markdown": e.get("markdown") is not None}


@app.put("/api/document/{pdf_id}/edicao", tags=["edição"])
def salvar_edicao(pdf_id: str, edicao: Edicao, pedido: Request):
    """Grava a edição, SOBRESCREVENDO a anterior. O original não é tocado.

    Continua sendo UMA linha por documento: as páginas recebidas são fundidas nas
    que já estavam guardadas, e `edited_at` passa a ser o momento desta gravação.
    """
    if not sb.table("pdfs").select("id").eq("id", pdf_id).execute().data:
        raise HTTPException(404, "PDF não encontrado")
    limitar(pedido, "edicao", pdf_id)
    if edicao.layout is None and edicao.markdown is None:
        raise HTTPException(400, "envie 'layout' e/ou 'markdown'")
    linha = {"pdf_id": pdf_id, "edited_by": edicao.edited_by}
    if edicao.layout is not None:
        anterior = _edicao_de(pdf_id) or {}
        paginas = {p["n"]: p for p in (anterior.get("layout") or []) if "n" in p}
        for p in edicao.layout:
            if "n" not in p:
                raise HTTPException(400, "cada página precisa do campo 'n'")
            paginas[p["n"]] = p
        linha["layout"] = [paginas[n] for n in sorted(paginas)]
    if edicao.markdown is not None:
        linha["markdown"] = edicao.markdown
    try:
        sb.table("document_edits").upsert(linha, on_conflict="pdf_id").execute()
    except Exception as e:
        raise HTTPException(503, f"tabela document_edits indisponível — aplique "
                                 f"supabase/002_edicoes.sql. Detalhe: {e}")
    return obter_edicao(pdf_id)


@app.delete("/api/document/{pdf_id}/edicao", tags=["edição"])
def descartar_edicao(pdf_id: str):
    """Joga fora a edição e volta ao que a extração produziu."""
    try:
        sb.table("document_edits").delete().eq("pdf_id", pdf_id).execute()
    except Exception as e:
        raise HTTPException(503, f"tabela document_edits indisponível: {e}")
    return {"editado": False}


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
