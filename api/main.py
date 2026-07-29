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
from datetime import datetime, timezone
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


def _signed_lote(paths: list[str]) -> dict[str, str | None]:
    """Assina vários caminhos numa chamada só.

    Uma a uma custa ~96 ms (ida e volta à API do Storage): no Boletim 100, com
    511 páginas, eram 49 s só para montar as URLs — era ISSO que fazia o
    documento demorar, não a busca dos blocos. Em lote, as mesmas 511 saem em
    0,2 s.
    """
    limpos = [p for p in paths if p]
    if not limpos:
        return {}
    try:
        res = sb.storage.from_(IMG_BUCKET).create_signed_urls(limpos, URL_TTL)
    except Exception:
        return {p: _signed(p) for p in limpos}      # servidor sem o endpoint em lote
    fora = {}
    for item in res or []:
        caminho = (item.get("path") or "").lstrip("/")
        fora[caminho] = item.get("signedURL") or item.get("signedUrl")
    return {p: fora.get(p.lstrip("/")) for p in limpos}


def _contagens(ids: list[str]) -> dict[str, dict]:
    """Contadores por documento (tabelas/figuras/fórmulas) para a Home.

    Usa a view `document_block_counts`, que agrega no Postgres — uma ida e volta
    para todos os documentos. Sem ela, cai no caminho antigo: baixar todos os
    blocos e contar em Python, que levava ~4,9 s com 12 documentos e piora
    linearmente. Ver supabase/003_contagem_blocos.sql.
    """
    vazio = {"tabelas": 0, "figuras": 0, "formulas": 0}
    try:
        linhas = (sb.table("document_block_counts")
                  .select("pdf_id,tabelas,figuras,formulas")
                  .in_("pdf_id", ids).execute().data)
        fora = {i: dict(vazio) for i in ids}
        for l in linhas:
            fora[l["pdf_id"]] = {"tabelas": l["tabelas"] or 0,
                                 "figuras": l["figuras"] or 0,
                                 "formulas": l["formulas"] or 0}
        return fora
    except Exception:
        pass                              # migração 003 ainda não aplicada

    fora = {i: dict(vazio) for i in ids}
    passo, inicio = 1000, 0
    while True:
        lote = (sb.table("page_blocks").select("pdf_id,block_type")
                .in_("pdf_id", ids).range(inicio, inicio + passo - 1).execute().data)
        for b in lote:
            alvo = fora.get(b["pdf_id"])
            if alvo is None:
                continue
            t = b["block_type"]
            if t == "tabela":
                alvo["tabelas"] += 1
            elif t in ("grafico", "foto"):
                alvo["figuras"] += 1
            elif t == "formula":
                alvo["formulas"] += 1
        if len(lote) < passo:
            break
        inicio += passo
    return fora


@app.get("/api/documents", tags=["documentos"])
def listar_documentos():
    """Catálogo para a Home: um item por PDF, com metadados e status do pipeline."""
    BASE = ("id,pdf_file,total_pages,approved,extracted,vectorized,"
            "approved_at,extracted_at,vectorized_at,extraction_time_ms,pipeline,created_at")
    try:
        pdfs = (sb.table("pdfs").select(BASE + ",extraction_requested_at")
                .order("created_at", desc=True).execute().data)
    except Exception:
        # colunas da fila ainda não aplicadas (005_fila_extracao.sql). A Home não
        # pode cair por causa disso — antes desta guarda ela devolvia 500.
        pdfs = (sb.table("pdfs").select(BASE)
                .order("created_at", desc=True).execute().data)
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

    agregados = _contagens(ids)

    thumbs_url = _signed_lote(list(por_thumb.values()))
    return [
        {
            **p,
            "meta": por_pdf.get(p["id"], {}),
            "thumbnail": thumbs_url.get(por_thumb.get(p["id"], "")),
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
    # SÓ os contadores por página: `page_number, block_type` são colunas pequenas,
    # enquanto `markdown_text` e `layout` (jsonb) são o peso. Buscar o documento
    # inteiro com os blocos levava 42 s no Boletim 100 — os blocos agora vêm por
    # página, sob demanda, em /api/document/{id}/pagina/{n}.
    leves: list[dict] = []
    passo, inicio = 1000, 0
    while True:
        lote = (
            sb.table("page_blocks").select("page_number,block_type")
            .eq("pdf_id", pdf_id).range(inicio, inicio + passo - 1).execute().data
        )
        leves.extend(lote)
        if len(lote) < passo:
            break
        inicio += passo

    contagem: dict[int, dict] = {}
    for b in leves:
        c = contagem.setdefault(b["page_number"], {"tabelas": 0, "figuras": 0, "formulas": 0})
        t = b["block_type"]
        if t == "tabela":
            c["tabelas"] += 1
        elif t in ("grafico", "foto"):
            c["figuras"] += 1
        elif t == "formula":
            c["formulas"] += 1

    urls = _signed_lote([img["image_file"] for img in imgs])
    paginas = [
        {
            "n": img["page_number"],
            "img_url": urls.get(img["image_file"]),
            "rota": img.get("route"),
            "tipo": img.get("page_type"),
            "w": img.get("width"),
            "h": img.get("height"),
            "blocos": None,                    # carregado sob demanda
            "counts": contagem.get(img["page_number"], {"tabelas": 0, "figuras": 0, "formulas": 0}),
        }
        for img in imgs
    ]

    ed = _edicao_de(pdf_id)

    return {
        **pdf,
        "meta": meta[0] if meta else {},
        "paginas": paginas,
        "total_blocos": len(leves),
        "editado": bool(ed),
        "edited_at": (ed or {}).get("edited_at"),
        "markdown": (ed or {}).get("markdown") or pdf.get("markdown"),
    }


@app.get("/api/document/{pdf_id}/pagina/{n}", tags=["documentos"])
def obter_pagina(pdf_id: str, n: int, pedido: Request):
    """Blocos de UMA página: bbox, markdown e tipo.

    O documento inteiro do Boletim 100 são 5.074 blocos e levava 42 s para vir.
    Uma página traz ~10 blocos. Sem limite de leitura aqui: navegar página a
    página é uso normal, e o limite por documento (5/min) travaria a leitura.
    """
    blocos = (
        sb.table("page_blocks").select("page_number,block_type,markdown_text,bbox,layout")
        .eq("pdf_id", pdf_id).eq("page_number", n).execute().data
    )

    def _indice(b):
        m = re.search(r"-b(\d+)$", ((b.get("layout") or {}).get("id") or ""))
        return int(m.group(1)) if m else 10**6

    blocos.sort(key=_indice)      # o Postgres não garante ordem; o id carrega a posição
    saida = [b.get("layout") or {"tipo": b["block_type"], "md": b["markdown_text"],
                                 "bbox": b["bbox"]} for b in blocos]

    # a edição manual desta página, se houver, substitui o que a extração produziu
    ed = _edicao_de(pdf_id)
    if ed and ed.get("layout"):
        for p in ed["layout"]:
            if isinstance(p, dict) and p.get("n") == n and p.get("blocos") is not None:
                saida = p["blocos"]
                break
    return {"n": n, "blocos": saida, "editado": bool(ed)}


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
    registrar("documento_editado", edicao.edited_by, pdf_id,
              {"paginas": [p.get("n") for p in (edicao.layout or [])],
               "markdown": edicao.markdown is not None},
              pedido.client.host if pedido.client else None)
    return obter_edicao(pdf_id)


@app.delete("/api/document/{pdf_id}/edicao", tags=["edição"])
def descartar_edicao(pdf_id: str):
    """Joga fora a edição e volta ao que a extração produziu."""
    try:
        sb.table("document_edits").delete().eq("pdf_id", pdf_id).execute()
    except Exception as e:
        raise HTTPException(503, f"tabela document_edits indisponível: {e}")
    registrar("edicao_descartada", None, pdf_id)
    return {"editado": False}


# ── auditoria ────────────────────────────────────────────────────────────────
# Registra entrada/saída, alteração de documento e o que entrou no acervo.
# Append-only: o app só insere e lê (ver supabase/004_auditoria.sql).

class EventoLog(BaseModel):
    evento: str
    ator: str | None = None
    alvo: str | None = None
    detalhe: dict[str, Any] | None = None


EVENTOS = {"login", "logout", "documento_editado", "edicao_descartada",
           "pdf_ingerido", "pdf_removido", "extracao_solicitada",
           "worker_heartbeat"}


def registrar(evento: str, ator=None, alvo=None, detalhe=None, ip=None) -> None:
    """Grava no log. Nunca levanta: auditoria não pode derrubar a operação."""
    try:
        sb.table("audit_log").insert({
            "evento": evento, "ator": ator, "alvo": alvo,
            "detalhe": detalhe, "ip": ip,
        }).execute()
    except Exception as e:
        print(f"[aviso] auditoria não gravou ({evento}): {e}")


@app.post("/api/log", tags=["auditoria"])
def anotar(ev: EventoLog, pedido: Request):
    """Usado pelo front para registrar login e logout."""
    if ev.evento not in EVENTOS:
        raise HTTPException(400, f"evento desconhecido; use um de {sorted(EVENTOS)}")
    registrar(ev.evento, ev.ator, ev.alvo, ev.detalhe,
              pedido.client.host if pedido.client else None)
    return {"ok": True}


@app.get("/api/log", tags=["auditoria"])
def historico(limite: int = 200, evento: str | None = None, alvo: str | None = None):
    """Histórico, do mais recente para o mais antigo."""
    try:
        q = sb.table("audit_log").select("*").order("criado_em", desc=True).limit(min(limite, 1000))
        if evento:
            q = q.eq("evento", evento)
        if alvo:
            q = q.eq("alvo", alvo)
        return q.execute().data
    except Exception as e:
        raise HTTPException(503, f"tabela audit_log indisponível — aplique "
                                 f"supabase/004_auditoria.sql. Detalhe: {e}")


# ── fila de extração ─────────────────────────────────────────────────────────
# A extração roda no lado com GPU (hoje o notebook no Colab). ESTE servidor não
# extrai nada — ele registra o pedido, e o lado GPU consulta a fila. Ver
# supabase/005_fila_extracao.sql.

class PedidoExtracao(BaseModel):
    solicitado_por: str | None = None


@app.post("/api/document/{pdf_id}/extrair", tags=["extração"])
def pedir_extracao(pdf_id: str, pedido_ext: PedidoExtracao, pedido: Request):
    """Coloca o documento na fila de extração. NÃO extrai aqui."""
    linha = sb.table("pdfs").select("id,pdf_file,extracted,extraction_requested_at")         .eq("id", pdf_id).execute().data
    if not linha:
        raise HTTPException(404, "PDF não encontrado")
    if linha[0].get("extracted"):
        raise HTTPException(409, "este documento já foi extraído")
    limitar(pedido, "edicao", pdf_id)
    agora = datetime.now(timezone.utc).isoformat()
    try:
        sb.table("pdfs").update({"extraction_requested_at": agora,
                                 "extraction_requested_by": pedido_ext.solicitado_por})             .eq("id", pdf_id).execute()
    except Exception as e:
        raise HTTPException(503, f"colunas da fila ausentes — aplique "
                                 f"supabase/005_fila_extracao.sql. Detalhe: {e}")
    registrar("extracao_solicitada", pedido_ext.solicitado_por, pdf_id,
              {"arquivo": linha[0]["pdf_file"]},
              pedido.client.host if pedido.client else None)
    return {"na_fila": True, "solicitado_em": agora,
            "aviso": "A extração roda no lado com GPU; este servidor apenas registrou o pedido."}


@app.get("/api/fila", tags=["extração"])
def fila_extracao():
    """O que está pedido e ainda não foi extraído — é isto que o lado GPU consulta."""
    try:
        return (sb.table("pdfs")
                .select("id,pdf_file,total_pages,extraction_requested_at,extraction_requested_by")
                .eq("extracted", False).not_.is_("extraction_requested_at", "null")
                .order("extraction_requested_at").execute().data)
    except Exception as e:
        raise HTTPException(503, f"colunas da fila ausentes — aplique "
                                 f"supabase/005_fila_extracao.sql. Detalhe: {e}")


# ── estado do worker de extração ─────────────────────────────────────────────
# O lado com GPU reporta que está vivo e o que está fazendo. Guardado como evento
# no audit_log — não precisa de tabela nova, e o histórico do worker fica junto
# com o resto. O estado atual é o último heartbeat.

class Heartbeat(BaseModel):
    rodando: bool = False
    gpu: str | None = None
    documento: str | None = None      # o que está processando agora
    pagina: int | None = None
    total_paginas: int | None = None
    detalhe: dict[str, Any] | None = None


@app.post("/api/worker/heartbeat", tags=["extração"])
def worker_heartbeat(hb: Heartbeat):
    """Chamado pelo lado GPU. Sem isso, o site mostra 'sem worker conectado'."""
    registrar("worker_heartbeat", "worker", None, hb.model_dump())
    return {"ok": True}


@app.get("/api/worker", tags=["extração"])
def worker_estado():
    """Último heartbeat + há quanto tempo. É o que a página de Extração mostra."""
    try:
        r = (sb.table("audit_log").select("detalhe,criado_em")
             .eq("evento", "worker_heartbeat")
             .order("criado_em", desc=True).limit(1).execute().data)
    except Exception:
        return {"conectado": False, "motivo": "audit_log indisponível"}
    if not r:
        return {"conectado": False, "motivo": "nenhum heartbeat recebido"}
    visto = r[0]["criado_em"]
    try:
        idade = (datetime.now(timezone.utc) - datetime.fromisoformat(visto)).total_seconds()
        # o relógio do banco e o desta máquina não batem exatamente (medi ~163 s de
        # diferença); sem o piso em zero o "há quantos segundos" saía negativo
        idade = max(0.0, idade)
    except Exception:
        idade = None
    # 3 min sem sinal = considera fora do ar; o notebook reporta a cada página
    conectado = idade is not None and idade < 180
    return {"conectado": conectado, "visto_em": visto,
            "segundos_desde": int(idade) if idade is not None else None,
            "estado": r[0]["detalhe"] or {}}


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
