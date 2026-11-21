# -*- coding: utf-8 -*-
"""Aba Figuras: todas as figuras extraídas, de qualquer documento, para analisar
a descrição de cada uma, e o dataset para baixar (imagens + descrição + de onde
veio cada uma).

O tipo da figura (barras, linhas, dispersão, radar, fotografia...) vem da
descrição que o Chandra OCR 2 escreve para o recorte. Ele não devolve um campo de
classe: a descrição começa dizendo o que é ("A horizontal stacked bar chart...",
"A close-up photograph..."), e aqui esse começo é lido por regras. Vale o
primeiro tipo citado na descrição, então "a bar chart with a line" é barras.

A coluna `block_type` (gráfico ou foto) continua sendo a classificação usada na
vetorização; o subtipo daqui é só para análise.
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

TIPOS_FIGURA = ("grafico", "foto")

# (id, rótulo, padrão). A ordem só desempata quando dois padrões começam no
# mesmo ponto; o que vale é o que aparece primeiro na descrição.
SUBTIPOS = [
    ("barras", "Gráfico de barras",
     r"\b(stacked |grouped |clustered |horizontal |vertical )*(bar|column)s? ?(chart|graph|plot|diagram)s?\b|\bbar-?graphs?\b|\bbarplots?\b"),
    ("linhas", "Gráfico de linhas",
     r"\bline (graph|chart|plot)s?\b|\bline-(graph|chart|plot)s?\b|\btime[- ]series (plot|graph|chart)s?\b"),
    ("dispersao", "Gráfico de dispersão", r"\bscatter ?(plot|graph|chart|diagram)s?\b|\bscatterplots?\b"),
    ("radar", "Gráfico de radar", r"\b(radar|spider|polar) (chart|plot|graph|diagram)s?\b"),
    ("pizza", "Gráfico de pizza", r"\b(pie|donut|doughnut) ?(chart|graph|plot)s?\b"),
    ("boxplot", "Box plot", r"\bbox[- ]?(and[- ]whiskers?[- ])?plots?\b|\bbox[- ]and[- ]whiskers?\b"),
    ("histograma", "Histograma", r"\bhistograms?\b"),
    ("area", "Gráfico de área", r"\barea (chart|graph|plot)s?\b"),
    ("calor", "Mapa de calor", r"\bheat ?maps?\b"),
    ("mapa", "Mapa", r"\b(geographic(al)? |topographic(al)? )?maps?\b|\bsatellite (image|imagery|photo)"),
    ("arvore", "Dendrograma ou árvore", r"\bdendrograms?\b|\bphylogenetic (tree|analysis)\b|\btree diagram\b|\bcladograms?\b"),
    ("micrografia", "Micrografia",
     r"\b(photo)?micrographs?\b|\bmicroscop(y|ic|e)\b|\belectron micro|\bSEM image"),
    ("gel", "Gel ou blot", r"\bgel (electrophoresis|image|photo)|\belectrophoresis\b|\b(western|southern|northern) blot"),
    ("fluxograma", "Fluxograma ou diagrama",
     r"\bflow ?charts?\b|\bflow diagram|\bschematic|\bdiagrams?\b|\bconceptual (model|framework)|\binfographic"),
    # a tabela de dados que o Chandra extrai do gráfico sai como <table> e é
    # removida antes; aqui só a figura descrita COMO tabela
    ("tabela", "Tabela em imagem", r"\b(a|the|this) (data |summary )?table\b|\btabular\b"),
    ("logo", "Logo, capa ou QR code", r"\blogos?\b|\bQR code|\b(magazine|journal|book) cover\b|\bcover page\b"),
    ("foto", "Fotografia", r"\bphotographs?\b|\bphotos?\b"),
    ("ilustracao", "Ilustração ou desenho", r"\billustrations?\b|\bdrawings?\b|\bsketch(es)?\b|\bcartoon"),
]
_COMPILADOS = [(i, r, re.compile(p, re.IGNORECASE)) for i, r, p in SUBTIPOS]
ROTULOS = {i: r for i, r, _ in SUBTIPOS}
ROTULOS.update({"grafico_outro": "Gráfico (tipo não dito)", "imagem_outra": "Imagem (outro tipo)",
                "dados_tabela": "Gráfico transcrito em tabela", "sem_descricao": "Sem descrição"})
# a descrição costuma dizer o tipo logo no começo; mais adiante aparece "the
# table below", "compared to the map"... que não dizem o que a figura é
JANELA = 400
_RE_GRAFICO = re.compile(r"\b(chart|graph|plot)s?\b", re.IGNORECASE)


def separar(md: str, legenda: str | None) -> tuple[str, str]:
    """(legenda do autor, descrição do Chandra). O markdown do bloco é a legenda
    seguida da descrição; o recorte da legenda no layout pode estar truncado,
    então a legenda inteira é o primeiro parágrafo quando ele começa igual."""
    md = (md or "").strip()
    leg = (legenda or "").strip()
    if leg:
        partes = md.split("\n\n", 1)
        if partes[0].strip().startswith(leg[:40]):
            return partes[0].strip(), (partes[1].strip() if len(partes) > 1 else "")
        if md.startswith(leg):
            return leg, md[len(leg):].strip()
    return leg, md


def subtipo(descricao: str, tipo: str | None) -> str:
    """Tipo da figura lido da descrição do Chandra."""
    d = (descricao or "").strip()
    if not d:
        return "sem_descricao"
    # fora o que não descreve a figura: a tabela de dados que o Chandra tira do
    # gráfico, outras tags HTML e as fórmulas em LaTeX
    limpo = re.sub(r"<table.*?</table>", " ", d, flags=re.S | re.I)
    limpo = re.sub(r"<[^>]+>", " ", limpo)
    limpo = re.sub(r"\$[^$]*\$", " ", limpo)
    trecho = limpo[:JANELA]
    melhor, pos = None, None
    for i, _, rx in _COMPILADOS:
        m = rx.search(trecho)
        if m and (pos is None or m.start() < pos):
            melhor, pos = i, m.start()
    if melhor:
        return melhor
    if "<table" in d[:JANELA].lower() and not _RE_GRAFICO.search(trecho):
        return "dados_tabela"         # o Chandra só transcreveu os dados, sem dizer o tipo
    if _RE_GRAFICO.search(trecho):
        return "grafico_outro"
    return "imagem_outra" if tipo == "foto" else "grafico_outro"


# ───────────────────────── leitura do banco ─────────────────────────────────

def _tudo(sb, tabela: str, colunas: str, **filtros) -> list[dict]:
    fora, off = [], 0
    while True:
        q = sb.table(tabela).select(colunas)
        for k, v in filtros.items():
            q = q.in_(k, v) if isinstance(v, (list, tuple)) else q.eq(k, v)
        lote = q.range(off, off + 999).execute().data
        if not lote:
            return fora
        fora += lote
        off += 1000


_CACHE: dict[str, tuple[float, list[dict]]] = {}
CACHE_S = 60


def listar(sb, usar_cache: bool = True) -> list[dict]:
    """Todas as figuras, com o texto corrigido na tela Analisar por cima do da
    extração (a mesma regra da vetorização: página editada vale inteira)."""
    agora = time.time()
    if usar_cache and "tudo" in _CACHE and agora - _CACHE["tudo"][0] < CACHE_S:
        return _CACHE["tudo"][1]

    blocos = _tudo(sb, "page_blocks", "id,pdf_id,page_number,block_type,markdown_text,bbox,layout",
                   block_type=list(TIPOS_FIGURA))
    editados: dict[str, dict[int, list[dict]]] = {}
    try:
        for e in sb.table("document_edits").select("pdf_id,layout").execute().data:
            pgs = {}
            for pg in e.get("layout") or []:
                if isinstance(pg, dict) and pg.get("n") is not None and pg.get("blocos") is not None:
                    pgs[pg["n"]] = pg["blocos"]
            if pgs:
                editados[e["pdf_id"]] = pgs
    except Exception:
        pass

    figs: list[dict] = []
    for b in blocos:
        if b["page_number"] in editados.get(b["pdf_id"], {}):
            continue                                   # vem da edição, abaixo
        lay = b.get("layout") or {}
        figs.append({"bloco_uuid": b["id"], "pdf_id": b["pdf_id"], "pagina": b["page_number"],
                     "bloco": lay.get("id") or "", "tipo": b["block_type"],
                     "md": b.get("markdown_text") or lay.get("md") or "",
                     "caption": lay.get("caption"), "figura": lay.get("fig"),
                     "bbox": lay.get("bbox") or b.get("bbox"), "conf": lay.get("conf"),
                     "editado": False})
    for pdf_id, pgs in editados.items():
        for n, bl in pgs.items():
            for lay in bl:
                if (lay.get("tipo") or "") in TIPOS_FIGURA:
                    figs.append({"bloco_uuid": None, "pdf_id": pdf_id, "pagina": n,
                                 "bloco": lay.get("id") or "", "tipo": lay["tipo"],
                                 "md": lay.get("md") or "", "caption": lay.get("caption"),
                                 "figura": lay.get("fig"), "bbox": lay.get("bbox"),
                                 "conf": lay.get("conf"), "editado": True})

    ids = sorted({f["pdf_id"] for f in figs})
    pdfs, metas = {}, {}
    for i in range(0, len(ids), 200):
        parte = ids[i:i + 200]
        for p in sb.table("pdfs").select("id,pdf_file,document_url,total_pages").in_("id", parte).execute().data:
            pdfs[p["id"]] = p
        for m in (sb.table("article_metadata").select("pdf_id,title,authors,year,doi,journal,category")
                  .in_("pdf_id", parte).execute().data):
            metas[m["pdf_id"]] = m

    fora = []
    for f in figs:
        p, m = pdfs.get(f["pdf_id"]), metas.get(f["pdf_id"]) or {}
        if not p:
            continue                                   # documento removido do acervo
        legenda, descricao = separar(f.pop("md"), f.pop("caption"))
        st = subtipo(descricao, f["tipo"])
        fora.append({**f, "arquivo": p["pdf_file"], "document_url": p.get("document_url"),
                     "titulo": m.get("title") or p["pdf_file"], "autores": m.get("authors") or "",
                     "ano": m.get("year"), "doi": m.get("doi") or "", "periodico": m.get("journal") or "",
                     "categoria": m.get("category") or "", "legenda": legenda, "descricao": descricao,
                     "subtipo": st, "subtipo_rotulo": ROTULOS[st]})

    def _ordem(f):
        num = re.search(r"-b(\d+)$", f["bloco"] or "")
        return (f["titulo"].lower(), f["pagina"], int(num.group(1)) if num else 0)
    fora.sort(key=_ordem)
    _CACHE["tudo"] = (agora, fora)
    return fora


def _sem_acento(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower()


def filtrar(figs: list[dict], q: str = "", subtipo_id: str = "", tipo: str = "") -> list[dict]:
    termos = [t for t in _sem_acento(q or "").split() if t]
    fora = []
    for f in figs:
        if subtipo_id and subtipo_id != "todos" and f["subtipo"] != subtipo_id:
            continue
        if tipo and tipo != "todos" and f["tipo"] != tipo:
            continue
        if termos:
            alvo = _sem_acento(" ".join(str(f.get(k) or "") for k in
                                        ("descricao", "legenda", "titulo", "arquivo", "autores", "subtipo_rotulo")))
            if not all(t in alvo for t in termos):
                continue
        fora.append(f)
    return fora


def contagem(figs: list[dict]) -> list[dict]:
    n: dict[str, int] = {}
    for f in figs:
        n[f["subtipo"]] = n.get(f["subtipo"], 0) + 1
    return [{"id": k, "rotulo": ROTULOS[k], "n": v} for k, v in sorted(n.items(), key=lambda kv: -kv[1])]


# ───────────────────────── dataset para baixar ───────────────────────────────

COLUNAS = ["imagem", "pdf_id", "pdf_arquivo", "titulo", "autores", "ano", "doi", "periodico",
           "categoria", "pagina", "bloco", "tipo", "subtipo", "subtipo_rotulo", "legenda",
           "descricao", "bbox", "confianca_yolo", "texto_editado", "url_documento"]

LEIA_ME = """Dataset de figuras do SB100 Squad 2
====================================

Gerado em {quando} por {quem}, na aba Figuras do site.
Filtros: {filtros}
Figuras: {n} ({com_img} com imagem; {sem_img} sem recorte disponível)

Arquivos
--------
figuras.csv    uma linha por figura, separado por ponto e vírgula (abre direto no
               Excel em português), UTF-8
figuras.jsonl  as mesmas linhas, uma por linha em JSON
imagens/       os recortes, em imagens/<pdf>/<bloco>.jpg (a coluna "imagem" aponta)

Colunas
-------
imagem          caminho do recorte dentro deste zip (vazio se o recorte não está
                no armazenamento do site; a descrição e os metadados vêm assim mesmo)
pdf_id          id do documento no banco do site
pdf_arquivo     nome do PDF no acervo
titulo, autores, ano, doi, periodico, categoria
                metadados da curadoria
pagina          página do PDF onde a figura está (1 = primeira)
bloco           id do bloco na página (pN-bM), o mesmo da tela Analisar
tipo            grafico ou foto: a classificação usada na vetorização (pela
                quantidade de fundo branco do recorte)
subtipo         o tipo lido da descrição do Chandra OCR 2 (barras, linhas,
                dispersao, radar, foto...): vale o primeiro tipo que a descrição
                cita nos primeiros {janela} caracteres
subtipo_rotulo  o subtipo por extenso
legenda         a legenda do autor, como está no artigo
descricao       a descrição que o Chandra OCR 2 escreveu para o recorte (inglês)
bbox            posição na página, frações de 0 a 1: x0, y0, x1, y1
confianca_yolo  confiança do DocLayout-YOLO na região da figura
texto_editado   sim quando o texto foi corrigido à mão na tela Analisar
url_documento   o nome do documento na API da curadoria
"""

_TAREFAS: dict[str, dict] = {}
_PASTA = Path(tempfile.gettempdir()) / "sb100_figuras"
VALIDADE_S = 3600


def _limpar_antigos() -> None:
    agora = time.time()
    for tid, t in list(_TAREFAS.items()):
        if agora - t["criada"] > VALIDADE_S:
            try:
                Path(t.get("arquivo") or "").unlink(missing_ok=True)
            except Exception:
                pass
            _TAREFAS.pop(tid, None)


def iniciar_dataset(sb, baixar_imagem, figs: list[dict], filtros: dict, quem: str) -> dict:
    """Monta o zip num processo à parte (o Cloudflare corta resposta acima de
    100 s). Devolve a tarefa; o site acompanha pelo id."""
    _limpar_antigos()
    _PASTA.mkdir(parents=True, exist_ok=True)
    tid = uuid.uuid4().hex[:10]
    t = {"id": tid, "estado": "baixando imagens", "feitas": 0, "total": len(figs),
         "criada": time.time(), "arquivo": None, "erro": None, "por": quem}
    _TAREFAS[tid] = t

    def correr():
        try:
            caminho = _PASTA / f"figuras_{tid}.zip"
            _montar(figs, filtros, quem, baixar_imagem, caminho, t)
            t["arquivo"] = str(caminho)
            t["tamanho_mb"] = round(caminho.stat().st_size / 2**20, 1)
            t["estado"] = "pronto"
        except Exception as e:
            t["estado"], t["erro"] = "falhou", str(e)[:300]

    threading.Thread(target=correr, daemon=True).start()
    return {k: v for k, v in t.items() if k != "arquivo"}


def tarefa(tid: str) -> dict | None:
    t = _TAREFAS.get(tid)
    return None if t is None else {k: v for k, v in t.items() if k != "arquivo"}


def arquivo_da_tarefa(tid: str) -> Path | None:
    t = _TAREFAS.get(tid)
    if not t or t["estado"] != "pronto" or not t.get("arquivo"):
        return None
    p = Path(t["arquivo"])
    return p if p.exists() else None


def _nome_pasta(arquivo: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", Path(arquivo or "pdf").stem)[:80] or "pdf"


def _baixar_com_retentativa(baixar_imagem, caminho: str, tentativas: int = 5):
    """O Storage derruba a conexão compartilhada quando vários downloads correm
    juntos ("Server disconnected", "ConnectionTerminated"): tenta de novo, com
    espera crescente. Arquivo que não existe (404) não adianta repetir."""
    for i in range(tentativas):
        try:
            return baixar_imagem(caminho)
        except Exception as e:
            msg = str(e)
            if "not_found" in msg or "404" in msg or i == tentativas - 1:
                raise
            time.sleep(0.4 * (i + 1))


def _montar(figs, filtros, quem, baixar_imagem, caminho: Path, t: dict) -> None:
    def pegar(f):
        if not f.get("figura"):
            return f, None
        try:
            return f, _baixar_com_retentativa(baixar_imagem, f["figura"])
        except Exception:
            return f, None

    linhas, com_img = [], 0
    tmp = caminho.with_suffix(".parcial")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z, ThreadPoolExecutor(4) as ex:
        for f, dados in ex.map(pegar, figs):
            t["feitas"] += 1
            nome_img = ""
            if dados:
                ext = Path(f["figura"]).suffix.lower() or ".jpg"
                nome_img = f"imagens/{_nome_pasta(f['arquivo'])}/{f['bloco'] or f['bloco_uuid']}{ext}"
                # JPEG já é comprimido: guardar sem recomprimir
                z.writestr(zipfile.ZipInfo(nome_img, time.localtime()[:6]), dados,
                           compress_type=zipfile.ZIP_STORED)
                com_img += 1
            bbox = f.get("bbox")
            if isinstance(bbox, str):
                try:
                    bbox = json.loads(bbox)
                except Exception:
                    pass
            linhas.append({
                "imagem": nome_img, "pdf_id": f["pdf_id"], "pdf_arquivo": f["arquivo"],
                "titulo": f["titulo"], "autores": f["autores"], "ano": f.get("ano") or "",
                "doi": f["doi"], "periodico": f["periodico"], "categoria": f["categoria"],
                "pagina": f["pagina"], "bloco": f["bloco"], "tipo": f["tipo"],
                "subtipo": f["subtipo"], "subtipo_rotulo": f["subtipo_rotulo"],
                "legenda": f["legenda"], "descricao": f["descricao"],
                "bbox": bbox if isinstance(bbox, list) else (bbox or ""),
                "confianca_yolo": f.get("conf") or "", "texto_editado": "sim" if f["editado"] else "nao",
                "url_documento": f.get("document_url") or ""})
        t["estado"] = "montando o zip"
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLUNAS, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for l in linhas:
            w.writerow({**l, "bbox": ", ".join(str(v) for v in l["bbox"]) if isinstance(l["bbox"], list)
                        else l["bbox"]})
        z.writestr("figuras.csv", "﻿" + buf.getvalue())
        z.writestr("figuras.jsonl", "".join(json.dumps(l, ensure_ascii=False) + "\n" for l in linhas))
        desc_filtros = ", ".join(f"{k}={v}" for k, v in filtros.items() if v and v != "todos") or "nenhum (todas)"
        z.writestr("LEIA-ME.txt", LEIA_ME.format(
            quando=datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC"), quem=quem or "?",
            filtros=desc_filtros, n=len(linhas), com_img=com_img, sem_img=len(linhas) - com_img,
            janela=JANELA))
    shutil.move(str(tmp), str(caminho))
