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

        try:
            pdf_id = achar_documento(sb, slug)
        except ValueError as err:
            item.update(situacao="desconhecido", motivo=str(err))
            fora.append(item); continue

        linha = (sb.table("pdfs").select("pdf_file,extracted,extracted_at")
                 .eq("id", pdf_id).execute().data)[0]
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

def ingerir_um(sb, pasta: Path, slug: str, pdf_id: str) -> dict:
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
    for pg in paginas:
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


def gerar_figuras(sb, pdf_id: str, pdf_file: str, refazer: bool = False) -> int:
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
        for b in blocos:
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


def processar(sb, pasta: Path, substituir: bool = False) -> dict:
    """Confere, grava o que pode, e devolve o relatório do que fez e não fez."""
    itens = conferir(sb, pasta)
    gravados, pulados, falhas = [], [], []

    for it in itens:
        if it["situacao"] == "ja_extraido" and not substituir:
            pulados.append(it); continue
        if it["situacao"] in ("invalido", "desconhecido"):
            falhas.append(it); continue
        try:
            r = ingerir_um(sb, pasta, it["slug"], it["pdf_id"])
            try:
                r["figuras"] = gerar_figuras(sb, it["pdf_id"], it["arquivo"])
            except Exception as e:
                r["figuras"] = 0
                r["aviso_figuras"] = str(e)[:120]
            gravados.append({**it, **r})
        except Exception as e:
            falhas.append({**it, "situacao": "erro", "motivo": str(e)[:160]})

    return {"gravados": gravados, "pulados": pulados, "falhas": falhas,
            "total": len(itens)}
