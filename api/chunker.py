# -*- coding: utf-8 -*-
"""Fatia um documento extraído em trechos para vetorizar.

Regras (definidas para o SB100 Squad 2):

    texto     janelas de 1024 caracteres com 20% de sobreposição (205),
              cortadas no último espaço antes do limite
    tabela    inteira, nunca cortada, por maior que seja
    figura    inteira: legenda do autor + descrição gerada, com o caminho
              do recorte no payload ("1 gráfico vale mais que 1.000 palavras")

Tabela e figura não interrompem o texto corrido: um parágrafo que continua
depois de uma tabela segue no mesmo fluxo, e a tabela vira um trecho à parte.
Cortar o texto em cada tabela partiria frases no meio e quebraria a
sobreposição justamente onde o assunto continua.

Sem dependência nenhuma, de propósito: este arquivo roda igual no script da GPU,
no servidor do site e nos testes. Se dois lugares fatiassem de jeitos diferentes,
a mesma coleção teria trechos incompatíveis e ninguém perceberia.

A entrada é a lista de páginas com blocos, no formato do `layout.json` da
extração (ou do `blocos.json` do pacote de vetorização, que é o mesmo):

    [{"n": 1, "blocos": [{"id": "p1-b0", "tipo": "texto", "md": "...",
                          "bbox": [...], "fig": "...", "caption": "..."}]}]
"""
from __future__ import annotations

import html
import re
import uuid
from html.parser import HTMLParser

VERSAO = "sb100-chunker-1"
TAMANHO = 1024
SOBREPOSICAO = 205                      # 20% de 1024
TIPOS_TABELA = ("tabela",)
TIPOS_FIGURA = ("grafico", "foto")

# namespace fixo: o id de um trecho não pode depender de quando ele foi gerado,
# senão revetorizar o mesmo documento duplicaria os pontos em vez de substituí-los
_NS = uuid.UUID("5b100c0d-0a1e-4c9b-9f2e-5b100c0d0a1e")


# ───────────────────────── leitura dos blocos ───────────────────────────────

def _ordem(bloco: dict) -> int:
    """Posição do bloco na página, tirada do id (p12-b7 -> 7).

    Numérica: ordenar o id como texto põe b10 antes de b2, e 408 das 724 páginas
    do acervo têm 10 blocos ou mais.
    """
    m = re.search(r"-b(\d+)$", str(bloco.get("id") or ""))
    return int(m.group(1)) if m else 10**6


class _Tabela(HTMLParser):
    """<table> do Chandra -> linhas "a | b | c". Só o necessário para o texto
    que vai ao modelo; o HTML original segue no payload."""

    def __init__(self):
        super().__init__()
        self.linhas, self._linha, self._cel, self._dentro = [], [], [], False

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self._dentro, self._cel = True, []
        elif tag == "tr":
            self._linha = []
        elif tag == "br" and self._dentro:
            self._cel.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._linha.append(" ".join("".join(self._cel).split()))
            self._dentro = False
        elif tag == "tr" and self._linha:
            self.linhas.append(" | ".join(self._linha))

    def handle_data(self, data):
        if self._dentro:
            self._cel.append(data)


def tabela_em_texto(md: str) -> str:
    if "<table" not in (md or ""):
        return (md or "").strip()
    p = _Tabela()
    try:
        p.feed(md)
    except Exception:
        return re.sub(r"<[^>]+>", " ", md).strip()
    antes = re.split(r"<table", md, maxsplit=1)[0]
    titulo = html.unescape(re.sub(r"<[^>]+>", " ", antes)).strip()
    corpo = "\n".join(p.linhas)
    return (titulo + "\n" + corpo).strip() if titulo else corpo


def _limpo(md: str) -> str:
    return re.sub(r"[ \t]+", " ", (md or "").replace("\u0000", "")).strip()


# ───────────────────────────── fatiar ───────────────────────────────────────

def _janelas(texto: str, tamanho: int, sobreposicao: int) -> list[tuple[int, int]]:
    """Intervalos [ini, fim) de no máximo `tamanho`, cortados no último espaço."""
    if not texto:
        return []
    fora, ini = [], 0
    n = len(texto)
    while ini < n:
        fim = min(ini + tamanho, n)
        if fim < n:
            corte = max(texto.rfind(" ", ini, fim), texto.rfind("\n", ini, fim))
            # só aceita o corte se não encolher a janela demais: palavra
            # gigante (URL, sequência de DNA) cortaria em quase nada
            if corte > ini + tamanho // 2:
                fim = corte
        fora.append((ini, fim))
        if fim >= n:
            break
        prox = fim - sobreposicao
        # recomeça no começo de palavra, e sempre anda para a frente
        espaco = texto.find(" ", prox, fim)
        prox = espaco + 1 if espaco != -1 else prox
        ini = prox if prox > ini else fim
    return fora


def gerar_chunks(paginas: list[dict], tamanho: int = TAMANHO,
                 sobreposicao: int = SOBREPOSICAO) -> list[dict]:
    """Trechos na ordem de leitura. Cada um:

        {"indice", "tipo": texto|tabela|figura, "texto", "pagina",
         "paginas", "blocos", e para tabela/figura os campos de origem}
    """
    if sobreposicao >= tamanho:
        raise ValueError("a sobreposição precisa ser menor que o tamanho")

    fluxo: list[str] = []
    mapa: list[tuple[int, int, int, str]] = []      # (ini, fim, página, bloco)
    inteiros: list[dict] = []                       # tabelas e figuras, na ordem
    pos = 0

    for pg in sorted(paginas, key=lambda p: p.get("n") or 0):
        n = pg.get("n")
        for b in sorted(pg.get("blocos") or [], key=_ordem):
            tipo = b.get("tipo") or "texto"
            md = b.get("md") or ""
            if tipo in TIPOS_TABELA:
                txt = tabela_em_texto(md)
                if txt:
                    inteiros.append({"tipo": "tabela", "texto": txt, "pagina": n,
                                     "paginas": [n], "blocos": [b.get("id")],
                                     "tabela_html": md if "<table" in md else None,
                                     "_pos": pos})
                continue
            if tipo in TIPOS_FIGURA:
                legenda = _limpo(b.get("caption") or "")
                desc = _limpo(md)
                if legenda and desc.startswith(legenda):
                    desc = desc[len(legenda):].strip()
                txt = "\n\n".join(x for x in (legenda, desc) if x)
                if txt or b.get("fig"):
                    inteiros.append({"tipo": "figura", "texto": txt, "pagina": n,
                                     "paginas": [n], "blocos": [b.get("id")],
                                     "figura": b.get("fig"), "legenda": legenda or None,
                                     "tipo_figura": tipo, "_pos": pos})
                continue
            txt = _limpo(md)
            if not txt:
                continue
            ini = pos
            fluxo.append(txt)
            pos += len(txt) + 1                      # +1 do "\n" que junta os blocos
            mapa.append((ini, pos - 1, n, b.get("id")))

    texto = "\n".join(fluxo)
    trechos: list[dict] = []
    for ini, fim in _janelas(texto, tamanho, sobreposicao):
        corpo = texto[ini:fim].strip()
        if not corpo:
            continue
        cobre = [(min(f, fim) - max(i, ini), p, bid) for (i, f, p, bid) in mapa
                 if i < fim and f > ini]
        por_pag: dict[int, int] = {}
        for qto, p, _ in cobre:
            por_pag[p] = por_pag.get(p, 0) + qto
        pags = sorted(por_pag)
        trechos.append({"tipo": "texto", "texto": corpo,
                        "pagina": max(por_pag, key=por_pag.get) if por_pag else None,
                        "paginas": pags, "blocos": [b for _, _, b in cobre],
                        "_pos": ini})

    # tabelas e figuras entram onde aparecem no documento
    todos = sorted(trechos + inteiros, key=lambda c: (c["_pos"], c["tipo"] != "texto"))
    for i, c in enumerate(todos):
        c.pop("_pos", None)
        c["indice"] = i
    return todos


# ───────────────────────────── payload ──────────────────────────────────────

def id_do_ponto(colecao: str, pdf_id: str, indice: int) -> str:
    """Determinístico: o mesmo trecho do mesmo documento na mesma coleção tem
    sempre o mesmo id, então regravar substitui em vez de duplicar."""
    return str(uuid.uuid5(_NS, f"{colecao}|{pdf_id}|{indice}|{VERSAO}"))


def montar_payload(chunk: dict, doc: dict, colecao: str, modelos: dict,
                   ingestao: str, tamanho: int = TAMANHO,
                   sobreposicao: int = SOBREPOSICAO) -> dict:
    """Payload compatível com o que os scripts antigos gravavam no Qdrant
    (content, file, pagina_pdf, titulo, autores...), para quem consulta as
    coleções hoje continuar funcionando, mais o que faltava para rastrear de
    volta: pdf_id, páginas, blocos e o recorte da figura."""
    m = doc.get("metadados") or {}
    p = {
        # ── chaves antigas, mesmos nomes ──
        "content": chunk["texto"],
        "chunk_index": chunk["indice"],
        "chunk_size": len(chunk["texto"]),
        "chunk_type": "sb100_blocos",
        "config_chunk": tamanho,
        "config_overlap": sobreposicao,
        "config_unit": "char",
        "tipo": chunk["tipo"],
        "pagina_pdf": chunk.get("pagina"),
        "file": doc.get("file") or doc.get("pdf_file"),
        "ingestao": ingestao,
        "titulo": m.get("title") or "",
        "autores": m.get("authors") or "",
        "ano": str(m.get("year") or ""),
        "doi": m.get("doi") or "",
        "periodico": m.get("journal") or "",
        "volume": m.get("volume") or "",
        "numero": m.get("issue") or "",
        "paginas": m.get("pages") or "",
        "editora": m.get("publisher") or "",
        "tipo_doc": m.get("document_type") or "",
        "instituicao": m.get("institution") or "",
        "local": m.get("location") or "",
        "cultura": m.get("category") or "",
        "palavras_chave": m.get("keywords") or "",
        "nutrientes": m.get("nutrients") or "",
        "culturas": m.get("crops") or "",
        "ferramentas": m.get("tools") or "",
        # ── novas ──
        "pdf_id": doc["pdf_id"],
        "pdf_file": doc.get("pdf_file"),
        "colecao": colecao,
        "paginas_pdf": chunk.get("paginas") or [],
        "blocos": chunk.get("blocos") or [],
        "origem_texto": doc.get("origem_texto") or "extracao",
        "versao_chunker": VERSAO,
        "modelo_denso": modelos.get("denso"),
        "modelo_esparso": modelos.get("esparso"),
    }
    if chunk["tipo"] == "tabela" and chunk.get("tabela_html"):
        p["tabela_html"] = chunk["tabela_html"]
    if chunk["tipo"] == "figura":
        p["figura"] = chunk.get("figura")
        p["legenda"] = chunk.get("legenda")
        p["tipo_figura"] = chunk.get("tipo_figura")
    return p
