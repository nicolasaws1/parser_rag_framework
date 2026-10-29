# -*- coding: utf-8 -*-
"""Recebe o resultado de uma extração feita fora daqui e grava no banco.

A extração roda onde há GPU (Colab, ou um servidor com placa). O que ela produz
é uma pasta por documento com `layout.json`, `document.md` e `pages/`. Este
módulo é o caminho de entrada desse material, e serve dois consumidores:

    scripts/ingerir_extracao.py   quem roda o extrator ingere direto
    POST /api/extracao/upload     quem rodou no Colab envia o zip pelo site

O segundo existe porque nem sempre quem extrai tem as chaves do banco à mão:
baixa o notebook, roda, e manda o zip pela tela. Uma coisa a menos para
configurar errado.

Antes de gravar qualquer coisa, o pacote é conferido inteiro: estrutura,
documentos que não existem no acervo, e documentos **já extraídos**. Duplicata é
recusada por padrão — regravar por engano apaga páginas e blocos de uma extração
que pode ter sido curada à mão.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz

PIPELINE = "hibrido: DocLayout-YOLO + Chandra OCR 2 + Docling + PyMuPDF"

# o único formato aceito no upload: é o que a célula final do notebook produz
EXTENSOES = (".zip",)
TAMANHO_MAXIMO = 600 * 1024 * 1024        # 600 MB

ESCALA_FIGURA = 2.4      # ~2600 px numa A4; recorte típico passa de 1200 px
MARGEM_FIGURA = 0.012    # fração da página, para não cortar rótulo de eixo
TIPOS_FIGURA = ("grafico", "foto")


def limpar(v: Any) -> Any:
    """O Postgres recusa \\u0000 em text e jsonb."""
    if isinstance(v, str):
        return v.replace("\u0000", "")
    if isinstance(v, list):
        return [limpar(x) for x in v]
    if isinstance(v, dict):
        return {k: limpar(x) for k, x in v.items()}
    return v


# ─────────────────────────── achar o documento ──────────────────────────────

def achar_documento(sb, slug: str) -> str:
    """id do documento a partir do slug do extrator.

    O nome no banco vem do `slug_de` da curadoria, que corta em 60 caracteres e
    às vezes deixa um traço no fim. O `slugify` do extrator faz `.strip('-')` e
    devolve um nome sem ele: cinco dos 230 divergem por esse traço, e a busca
    exata não acha nenhum deles.
    """
    exato = sb.table("pdfs").select("id").eq("pdf_file", f"{slug}.pdf").execute().data
    if exato:
        return exato[0]["id"]
    cand = (sb.table("pdfs").select("id,pdf_file")
            .ilike("pdf_file", f"{slug}%").execute().data)
    alvo = slug.rstrip("-")
    iguais = [c for c in cand if c["pdf_file"].removesuffix(".pdf").rstrip("-") == alvo]
    if len(iguais) == 1:
        return iguais[0]["id"]
    if len(iguais) > 1:
        raise ValueError(f"{len(iguais)} documentos do acervo batem com este nome")
    raise ValueError("não está no acervo; sincronize a curadoria antes")


# ───────────────────────── lote de ida ──────────────────────────────────────
# O computador com GPU não precisa de chave nenhuma do banco: baixa um lote pelo
# site, extrai, e devolve o zip pelo site. O lote leva os PDFs, os metadados da
# curadoria e um `lote.json` com o pdf_id de cada arquivo, que volta junto no zip
# de resposta. É por esse id que a volta casa — não pelo nome do arquivo, que o
# extrator reescreve (o slugify tira o traço final, e um documento já se perdeu
# assim).

import re
import uuid

MAX_LOTE = 50


def slugify(nome: str) -> str:
    """O MESMO do extrator. Se divergir, o lote.json deixa de casar."""
    return re.sub(r"[^a-z0-9]+", "-", Path(nome).stem.lower()).strip("-")[:60]


# chaves que o extrator procura em metadados_api.json (meta_do)
_META = {"title": "Título", "authors": "Autor(es)", "year": "Ano",
         "journal": "Título do periódico", "volume": "Volume", "doi": "DOI",
         "category": "CATEGORIA", "document_type": "Tipo de documento",
         "keywords": "Palavras-chave"}


def escolher_lote(sb, quantos: int, menores: bool = True,
                  ids: list[str] | None = None) -> list[dict]:
    """Documentos ainda não extraídos. Os pedidos pelo botão Analisar vêm antes."""
    try:
        cand = (sb.table("pdfs")
                .select("id,pdf_file,total_pages,extraction_requested_at,curation_status")
                .eq("extracted", False).execute().data)
    except Exception:
        cand = (sb.table("pdfs").select("id,pdf_file,total_pages")
                .eq("extracted", False).execute().data)
    from api.curadoria import fora_do_fluxo
    cand = [c for c in cand if not fora_do_fluxo(c.get("curation_status"))]
    if ids:
        quer = set(ids)
        cand = [c for c in cand if c["id"] in quer]
    cand.sort(key=lambda d: (not d.get("extraction_requested_at"),
                             (d.get("total_pages") or 10**6) if menores else 0))
    return cand[: max(1, min(quantos, MAX_LOTE))]


def montar_lote(sb, docs: list[dict], quem: str) -> tuple[bytes, dict]:
    """zip com pdfs/, metadados_api.json e lote.json. Devolve (bytes, resumo)."""
    ids = [d["id"] for d in docs]
    metas = {m["pdf_id"]: m for m in
             sb.table("article_metadata").select("*").in_("pdf_id", ids).execute().data}

    lote_id = datetime.now().strftime("%Y%m%d-%H%M") + "-" + uuid.uuid4().hex[:6]
    manifesto = {"lote": lote_id, "criado_em": datetime.now().isoformat(timespec="seconds"),
                 "criado_por": quem, "documentos": []}
    meta_extrator: dict[str, dict] = {}
    buf = io.BytesIO()
    faltaram = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d in docs:
            try:
                dados = sb.storage.from_("pdfs").download(d["pdf_file"])
            except Exception:
                faltaram.append(d["pdf_file"]); continue
            z.writestr(f"pdfs/{d['pdf_file']}", dados)
            m = metas.get(d["id"], {})
            meta_extrator[d["pdf_file"]] = {v: m.get(k) for k, v in _META.items() if m.get(k)}
            manifesto["documentos"].append({
                "pdf_id": d["id"], "arquivo": d["pdf_file"],
                "slug": slugify(d["pdf_file"]), "paginas": d.get("total_pages"),
                "titulo": m.get("title"), "autores": m.get("authors"),
                "ano": m.get("year"), "doi": m.get("doi"), "categoria": m.get("category"),
            })
        z.writestr("metadados_api.json", json.dumps(meta_extrator, ensure_ascii=False, indent=1))
        z.writestr("lote.json", json.dumps(manifesto, ensure_ascii=False, indent=1))
    return buf.getvalue(), {"lote": lote_id, "documentos": len(manifesto["documentos"]),
                            "paginas": sum(x["paginas"] or 0 for x in manifesto["documentos"]),
                            "sem_binario": faltaram}


def _mapa_do_lote(pasta: Path) -> dict[str, str]:
    """slug -> pdf_id, se o pacote de volta trouxer o lote.json."""
    f = pasta / "lote.json"
    if not f.exists():
        return {}
    try:
        man = json.loads(f.read_text(encoding="utf-8"))
        return {x["slug"]: x["pdf_id"] for x in man.get("documentos", [])
                if x.get("slug") and x.get("pdf_id")}
    except Exception:
        return {}


# ───────────────────────────── conferir ─────────────────────────────────────

ARQUIVOS_OBRIGATORIOS = ("layout.json", "document.md")


def conferir(sb, pasta: Path) -> list[dict]:
    """Olha o pacote inteiro ANTES de gravar e diz o que fazer com cada item.

    Situações: `novo`, `ja_extraido` (duplicata), `invalido`, `desconhecido`.
    """
    idx = pasta / "index.json"
    if not idx.exists():
        raise ValueError("o pacote não tem index.json — não parece saída do extrator")
    try:
        entradas = json.loads(idx.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"index.json ilegível: {e}")
    if not isinstance(entradas, list) or not entradas:
        raise ValueError("index.json vazio")

    mapa = _mapa_do_lote(pasta)
    fora = []
    for e in entradas:
        slug = (e or {}).get("slug")
        item: dict[str, Any] = {"slug": slug, "paginas": e.get("n_paginas")}
        if not slug:
            item.update(situacao="invalido", motivo="entrada sem slug")
            fora.append(item); continue

        faltando = [a for a in ARQUIVOS_OBRIGATORIOS if not (pasta / slug / a).exists()]
        if faltando:
            item.update(situacao="invalido", motivo=f"falta {', '.join(faltando)}")
            fora.append(item); continue

        pdf_id = mapa.get(slug)
        if not pdf_id:
            try:
                pdf_id = achar_documento(sb, slug)
            except ValueError as err:
                item.update(situacao="desconhecido", motivo=str(err))
                fora.append(item); continue

        achado = (sb.table("pdfs").select("pdf_file,extracted,extracted_at")
                  .eq("id", pdf_id).execute().data)
        if not achado:
            item.update(situacao="desconhecido", motivo="o documento do lote saiu do acervo")
            fora.append(item); continue
        linha = achado[0]
        item["pdf_id"] = pdf_id
        item["arquivo"] = linha["pdf_file"]
        if linha.get("extracted"):
            item.update(situacao="ja_extraido",
                        motivo=f"extraído em {str(linha.get('extracted_at'))[:16]}")
        else:
            item.update(situacao="novo")
        fora.append(item)
    return fora


# ───────────────────────────── gravar ───────────────────────────────────────

def _nada(**_):
    pass


def ingerir_um(sb, pasta: Path, slug: str, pdf_id: str, avisar=_nada) -> dict:
    """Atualiza o documento no lugar. Não encosta em `article_metadata`.

    Apagar e recriar a linha de `pdfs` levaria junto, por cascata, os metadados
    vindos da API de curadoria — título, autores, DOI. Quem extrai não tem de
    onde repor isso.
    """
    lay = json.loads((pasta / slug / "layout.json").read_text(encoding="utf-8"))
    md = (pasta / slug / "document.md").read_text(encoding="utf-8")
    paginas = lay.get("paginas", [])
    agora = datetime.now().isoformat(timespec="seconds")

    sb.table("pdfs").update({
        "markdown": limpar(md),
        "total_pages": lay.get("n_paginas"),
        "extracted": True, "extracted_at": agora,
        "extraction_time_ms": int(round((lay.get("tempo_s") or 0) * 1000)) or None,
        "pipeline": PIPELINE,
    }).eq("id", pdf_id).execute()

    # re-extração não pode acumular
    sb.table("page_blocks").delete().eq("pdf_id", pdf_id).execute()
    sb.table("page_images").delete().eq("pdf_id", pdf_id).execute()

    imgs = []
    for k, pg in enumerate(paginas, 1):
        avisar(etapa="imagens das páginas", feito=k, total=len(paginas))
        local = pasta / slug / pg["img"]
        remoto = f"{slug}/{pg['img']}"
        if local.exists():
            sb.storage.from_("images").upload(
                remoto, local.read_bytes(),
                {"content-type": "image/jpeg", "upsert": "true"})
        imgs.append({"pdf_id": pdf_id, "page_number": pg["n"], "image_file": remoto,
                     "route": pg.get("rota"), "page_type": pg.get("tipo"),
                     "width": pg.get("w"), "height": pg.get("h")})
    for i in range(0, len(imgs), 500):
        sb.table("page_images").insert(imgs[i:i + 500]).execute()

    blocos = [{"pdf_id": pdf_id, "page_number": pg["n"], "block_type": b.get("tipo"),
               "markdown_text": limpar(b.get("md")), "bbox": b.get("bbox"),
               "layout": limpar(b)}
              for pg in paginas for b in pg.get("blocos", [])]
    avisar(etapa="blocos de texto", feito=0, total=len(blocos))
    for i in range(0, len(blocos), 500):
        sb.table("page_blocks").insert(blocos[i:i + 500]).execute()

    return {"paginas": len(imgs), "blocos": len(blocos)}


# ─────────────────────── recortes de figura ────────────────────────────────

def prefixo_do_documento(sb, pdf_id: str) -> str | None:
    """A pasta que as páginas já usam no bucket. As figuras vão ao lado delas, e
    não pelo `pdf_file`: o Boletim foi renomeado e os dois divergem."""
    r = (sb.table("page_images").select("image_file")
         .eq("pdf_id", pdf_id).limit(1).execute().data)
    if not r or not r[0].get("image_file"):
        return None
    return r[0]["image_file"].split("/")[0]


def recortar(pagina: fitz.Page, bbox: list[float]) -> bytes:
    x0, y0, x1, y1 = bbox
    r = pagina.rect
    caixa = fitz.Rect(
        max(r.x0, r.x0 + (x0 - MARGEM_FIGURA) * r.width),
        max(r.y0, r.y0 + (y0 - MARGEM_FIGURA) * r.height),
        min(r.x1, r.x0 + (x1 + MARGEM_FIGURA) * r.width),
        min(r.y1, r.y0 + (y1 + MARGEM_FIGURA) * r.height),
    )
    pix = pagina.get_pixmap(matrix=fitz.Matrix(ESCALA_FIGURA, ESCALA_FIGURA), clip=caixa)
    buf = io.BytesIO()
    pix.pil_save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def gerar_figuras(sb, pdf_id: str, pdf_file: str, refazer: bool = False,
                  avisar=_nada) -> int:
    """Recorta cada figura do PDF do bucket, pela bbox do próprio banco.

    Não vem da pasta da extração de propósito: a numeração dos blocos muda entre
    corridas, então casar por nome de arquivo cola o recorte de uma figura no
    bloco de outra sem nada denunciar. Aqui as duas pontas saem da mesma fonte.
    """
    prefixo = prefixo_do_documento(sb, pdf_id)
    if not prefixo:
        return 0
    blocos = (sb.table("page_blocks").select("id,page_number,bbox,layout")
              .eq("pdf_id", pdf_id).in_("block_type", list(TIPOS_FIGURA)).execute().data)
    blocos = [b for b in blocos if b.get("bbox") and len(b["bbox"]) == 4]
    if not refazer:
        blocos = [b for b in blocos
                  if "/figuras/" not in ((b["layout"] or {}).get("fig") or "")]
    if not blocos:
        return 0

    pdf = fitz.open(stream=sb.storage.from_("pdfs").download(pdf_file), filetype="pdf")
    feitas = 0
    try:
        for k, b in enumerate(blocos, 1):
            avisar(etapa="recortes de figura", feito=k, total=len(blocos))
            n = b["page_number"]
            if not (1 <= n <= pdf.page_count):
                continue
            lay = b["layout"] or {}
            nome = (lay.get("id") or f"p{n}-{b['id'][:6]}") + ".jpg"
            destino = f"{prefixo}/figuras/{nome}"
            sb.storage.from_("images").upload(
                destino, recortar(pdf[n - 1], b["bbox"]),
                {"content-type": "image/jpeg", "upsert": "true"})
            sb.table("page_blocks").update({"layout": {**lay, "fig": destino}}) \
                .eq("id", b["id"]).execute()
            feitas += 1
    finally:
        pdf.close()
    return feitas


# ──────────────────────── o pacote inteiro ──────────────────────────────────

def abrir_pacote(dados: bytes, destino: Path) -> Path:
    """Descompacta e devolve a pasta que contém o index.json.

    O zip pode vir com a pasta da corrida por fora, conforme quem compactou.
    """
    if len(dados) > TAMANHO_MAXIMO:
        raise ValueError(f"pacote de {len(dados)/2**20:.0f} MB; o limite é "
                         f"{TAMANHO_MAXIMO/2**20:.0f} MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(dados))
    except zipfile.BadZipFile:
        raise ValueError("não é um arquivo .zip válido")

    for nome in z.namelist():
        # zip com caminho para fora da pasta escreveria onde não deve
        alvo = (destino / nome).resolve()
        if not str(alvo).startswith(str(destino.resolve())):
            raise ValueError(f"caminho suspeito no zip: {nome}")
    z.extractall(destino)

    if (destino / "index.json").exists():
        return destino
    achados = sorted(destino.glob("*/index.json"))
    if achados:
        return achados[0].parent
    raise ValueError("não achei index.json no pacote; é a saída do extrator?")


def processar(sb, pasta: Path, substituir: bool = False, avisar=_nada) -> dict:
    """Confere, grava o que pode, e devolve o relatório do que fez e não fez.

    `avisar(**campos)` recebe o andamento (documento atual, etapa, quanto falta),
    para a tela mostrar o progresso enquanto grava.
    """
    itens = conferir(sb, pasta)
    gravados, pulados, falhas = [], [], []
    vao = [it for it in itens if it["situacao"] == "novo"
           or (it["situacao"] == "ja_extraido" and substituir)]

    for it in itens:
        if it["situacao"] == "ja_extraido" and not substituir:
            pulados.append(it); continue
        if it["situacao"] in ("invalido", "desconhecido"):
            falhas.append(it); continue
        avisar(documento=vao.index(it) + 1, documentos=len(vao),
               arquivo=it.get("arquivo") or it["slug"], etapa="começando", feito=0, total=0)
        try:
            r = ingerir_um(sb, pasta, it["slug"], it["pdf_id"], avisar)
            try:
                r["figuras"] = gerar_figuras(sb, it["pdf_id"], it["arquivo"], avisar=avisar)
            except Exception as e:
                r["figuras"] = 0
                r["aviso_figuras"] = str(e)[:120]
            gravados.append({**it, **r})
        except Exception as e:
            falhas.append({**it, "situacao": "erro", "motivo": str(e)[:160]})

    return {"gravados": gravados, "pulados": pulados, "falhas": falhas,
            "total": len(itens)}
