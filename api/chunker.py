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

Estratégia do texto corrido, escolhida por coleção na aba Vetorização (fica no
metadado da coleção e viaja no pacote). Tabela e figura são iguais em todas:

    blocos     a original (sb100-chunker-1): janelas cortadas no último espaço.
               É a das coleções criadas antes da escolha existir
    fixed      janelas de N caracteres, sem olhar o texto (corta palavra)
    recursive  RecursiveCharacterTextSplitter do LangChain: parágrafo, linha,
               espaço, nessa ordem de preferência
    struct     divide nos títulos de seção e aplica o recursive dentro de cada
               seção: nenhum trecho atravessa duas seções
    semantic   algoritmo do SemanticChunker do LangChain (frases, distância de
               cosseno entre vizinhas, corte no percentil 95) com o Qwen3 denso;
               segmento maior que N é subdividido pelo recursive

As quatro do LangChain são as mesmas definições do estudo de chunking do TG
(Vetorizacao Qdrant/chunking_tg.py), adaptadas aos blocos do SB100.

Sem dependência nenhuma para a estratégia `blocos`: o servidor do site importa
este arquivo sem LangChain. As outras importam `langchain_text_splitters` só
quando usadas, no script da GPU. Se dois lugares fatiassem de jeitos diferentes,
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

VERSAO = "sb100-chunker-1"          # a estratégia `blocos`; não mudar: entra no id dos pontos
VERSAO_LANGCHAIN = "sb100-chunker-2"
ESTRATEGIA_ANTIGA = "blocos"
ESTRATEGIAS = ("recursive", "struct", "semantic", "fixed")   # as do LangChain, para coleção nova
SEMANTIC_BUFFER = 1                  # frases vizinhas combinadas (padrão do SemanticChunker)
SEMANTIC_PERCENTIL = 95              # corte no percentil 95 das distâncias (padrão)
SEMANTIC_MAX_CHARS = 4000            # só para CALCULAR a distância: frase gigante (tabela
                                     # sem ponto) não estoura a GPU; o trecho não é cortado
TAMANHO = 1024
SOBREPOSICAO = 205                      # 20% de 1024
# escolhidos na criação da coleção (aba Vetorização); o padrão acima vale para
# as coleções criadas antes da escolha existir
TAMANHOS = (512, 1024, 2048)
SOBREPOSICOES_PCT = (0, 10, 15, 20, 25)


def sobreposicao_de(tamanho: int, pct: int) -> int:
    """Sobreposição em caracteres: 20% de 1024 = 205 (o padrão), 10% = 102."""
    return round(tamanho * pct / 100)
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


def normalizar_estrategia(estrategia: str | None) -> str:
    e = (estrategia or ESTRATEGIA_ANTIGA).strip().lower()
    if e != ESTRATEGIA_ANTIGA and e not in ESTRATEGIAS:
        raise ValueError(f"estratégia de chunking desconhecida: {estrategia} "
                         f"(use {', '.join(ESTRATEGIAS)})")
    return e


def versao(estrategia: str | None) -> str:
    """Versão do fatiamento, que entra no id dos pontos. A `blocos` mantém a de
    sempre: as coleções antigas, regravadas, substituem os mesmos pontos."""
    e = normalizar_estrategia(estrategia)
    return VERSAO if e == ESTRATEGIA_ANTIGA else f"{VERSAO_LANGCHAIN}-{e}"


# títulos de seção comuns nos artigos do acervo (pt, en, es), sem acento
_SECOES = (
    "resumo", "abstract", "resumen", "introducao", "introduction", "introduccion",
    "material e metodos", "materiais e metodos", "material e metodo", "materials and methods",
    "material and methods", "materiales y metodos", "methods", "methodology", "metodologia",
    "metodos", "resultados", "results", "resultados e discussao", "results and discussion",
    "resultados y discusion", "discussao", "discussion", "discusion", "conclusao", "conclusoes",
    "conclusion", "conclusions", "conclusiones", "consideracoes finais", "referencias",
    "references", "referencias bibliograficas", "bibliografia", "literature cited",
    "agradecimentos", "acknowledgements", "acknowledgments", "agradecimientos",
    "revisao de literatura", "revisao bibliografica", "literature review", "background",
    "objetivo", "objetivos", "objective", "objectives", "palavras-chave", "keywords",
)
_RE_NUMERADO = re.compile(r"^(\d{1,2}(\.\d{1,2}){0,3}\.?|[IVX]{1,5}\.)\s+\S")
# "TABELA 2", "Figure 3": rótulo de tabela ou figura solto, não título de seção
_RE_ROTULO = re.compile(r"^(tabela|table|tabla|quadro|figura|figure|fig\.?|gr[aá]fico)\s*\d",
                        re.IGNORECASE)


def _sem_acento(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def eh_titulo(md: str) -> bool:
    """Bloco de texto que é título de seção. O Chandra marca o título, mas a
    extração guarda só o texto, sem o `#`: aqui ele é reconhecido pela forma
    (linha curta, sem ponto final) mais um destes sinais: numeração ("2.1
    Coleta"), caixa alta, ou nome de seção conhecido ("Material e Métodos")."""
    t = re.sub(r"[*_#]+", "", (md or "")).strip()
    if not t or "\n" in t or len(t) > 90 or len(t.split()) > 12:
        return False
    if t[-1] in ".,;" and not _RE_NUMERADO.match(t):
        return False
    letras = [c for c in t if c.isalpha()]
    if len(letras) < 3 or _RE_ROTULO.match(t):
        return False
    nome =re.sub(r"^[\dIVX.\s]+", "", _sem_acento(t).lower()).strip(" :.-")
    if nome in _SECOES:
        return True
    if _RE_NUMERADO.match(t) and t[-1] not in ".,;":
        return True
    maiusc = sum(c.isupper() for c in letras) / len(letras)
    return maiusc >= 0.8 and len(letras) >= 4


# ── estratégias do LangChain: devolvem intervalos [ini, fim) do texto ──

def _recursive(tamanho: int, sobreposicao: int):
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    return RecursiveCharacterTextSplitter(chunk_size=tamanho, chunk_overlap=sobreposicao,
                                          length_function=len, add_start_index=True,
                                          strip_whitespace=True)


def _intervalos(divisor, texto: str, base: int = 0) -> list[tuple[int, int]]:
    fora = []
    for d in divisor.create_documents([texto]):
        ini = base + d.metadata["start_index"]
        fora.append((ini, ini + len(d.page_content)))
    return fora


def _fixed(texto: str, tamanho: int, sobreposicao: int) -> list[tuple[int, int]]:
    passo = tamanho - sobreposicao
    fora = []
    for ini in range(0, len(texto), passo):
        fim = min(len(texto), ini + tamanho)
        if texto[ini:fim].strip():
            fora.append((ini, fim))
        if fim == len(texto):
            break
    return fora


def _struct(texto: str, tamanho: int, sobreposicao: int,
            titulos: list[int]) -> list[tuple[int, int]]:
    div = _recursive(tamanho, sobreposicao)
    inicios = [0] + [p for p in titulos if p > 0]
    fora = []
    for a, b in zip(inicios, inicios[1:] + [len(texto)]):
        if texto[a:b].strip():
            fora += _intervalos(div, texto[a:b], base=a)
    return fora


_RE_FRASE = re.compile(r"(?<=[.?!])\s+")       # a mesma regra de frase do SemanticChunker


def _frases(texto: str) -> list[tuple[int, int]]:
    fora, ini = [], 0
    for m in _RE_FRASE.finditer(texto):
        if texto[ini:m.start()].strip():
            fora.append((ini, m.start()))
        ini = m.end()
    if texto[ini:].strip():
        fora.append((ini, len(texto)))
    return fora


def _semantic(texto: str, tamanho: int, sobreposicao: int, embed_fn) -> list[tuple[int, int]]:
    if embed_fn is None:
        raise ValueError("a estratégia semantic precisa do modelo denso (embed_fn)")
    fs = _frases(texto)
    if len(fs) <= 1:
        segmentos = [(0, len(texto))] if texto.strip() else []
    else:
        import numpy as np
        janelas = [texto[fs[max(0, i - SEMANTIC_BUFFER)][0]:
                         fs[min(len(fs) - 1, i + SEMANTIC_BUFFER)][1]][:SEMANTIC_MAX_CHARS]
                   for i in range(len(fs))]
        emb = np.asarray(embed_fn(janelas), dtype="float32")
        emb /= np.linalg.norm(emb, axis=1, keepdims=True) + 1e-12
        dist = 1.0 - (emb[:-1] * emb[1:]).sum(axis=1)
        limiar = float(np.percentile(dist, SEMANTIC_PERCENTIL))
        segmentos, ini = [], fs[0][0]
        for i, d in enumerate(dist):
            if d > limiar:
                segmentos.append((ini, fs[i][1]))
                ini = fs[i + 1][0]
        segmentos.append((ini, fs[-1][1]))
    div = _recursive(tamanho, sobreposicao)
    fora = []
    for a, b in segmentos:
        if b - a <= tamanho:
            fora.append((a, b))
        else:
            fora += _intervalos(div, texto[a:b], base=a)
    return fora


def gerar_chunks(paginas: list[dict], tamanho: int = TAMANHO,
                 sobreposicao: int = SOBREPOSICAO, estrategia: str | None = None,
                 embed_fn=None) -> list[dict]:
    """Trechos na ordem de leitura. Cada um:

        {"indice", "tipo": texto|tabela|figura, "texto", "pagina",
         "paginas", "blocos", e para tabela/figura os campos de origem}

    `estrategia` muda só o texto corrido (ver o topo do arquivo). `embed_fn`
    (lista de textos -> matriz de vetores) só é usada pela `semantic`.
    """
    if sobreposicao >= tamanho:
        raise ValueError("a sobreposição precisa ser menor que o tamanho")
    estrategia = normalizar_estrategia(estrategia)
    # a antiga junta os blocos com uma quebra de linha, como sempre fez; as do
    # LangChain com linha em branco, para o recursive preferir cortar entre blocos
    sep = "\n" if estrategia == ESTRATEGIA_ANTIGA else "\n\n"

    fluxo: list[str] = []
    mapa: list[tuple[int, int, int, str]] = []      # (ini, fim, página, bloco)
    inteiros: list[dict] = []                       # tabelas e figuras, na ordem
    titulos: list[tuple[int, str]] = []             # (posição, título) das seções
    pos = 0
    # cabeçalho corrido (nome da revista, autores) se repete em várias páginas e
    # tem cara de título: o que aparece em 3 páginas ou mais não abre seção
    repetidos: set[str] = set()
    if estrategia == "struct":
        vistos: dict[str, set] = {}
        for pg in paginas:
            for b in pg.get("blocos") or []:
                if (b.get("tipo") or "texto") not in TIPOS_TABELA + TIPOS_FIGURA:
                    vistos.setdefault(_limpo(b.get("md") or "").lower(), set()).add(pg.get("n"))
        repetidos = {t for t, pgs in vistos.items() if t and len(pgs) >= 3}

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
            if estrategia == "struct" and txt.lower() not in repetidos and eh_titulo(txt):
                titulos.append((ini, re.sub(r"[*_#]+", "", txt).strip()))
            fluxo.append(txt)
            pos += len(txt) + len(sep)               # + o separador que junta os blocos
            mapa.append((ini, ini + len(txt), n, b.get("id")))

    texto = sep.join(fluxo)
    if estrategia == ESTRATEGIA_ANTIGA:
        intervalos = _janelas(texto, tamanho, sobreposicao)
    elif estrategia == "fixed":
        intervalos = _fixed(texto, tamanho, sobreposicao)
    elif estrategia == "recursive":
        intervalos = _intervalos(_recursive(tamanho, sobreposicao), texto) if texto.strip() else []
    elif estrategia == "struct":
        intervalos = _struct(texto, tamanho, sobreposicao, [p for p, _ in titulos])
    else:
        intervalos = _semantic(texto, tamanho, sobreposicao, embed_fn)

    trechos: list[dict] = []
    for ini, fim in intervalos:
        corpo = texto[ini:fim].strip()
        if not corpo:
            continue
        cobre = [(min(f, fim) - max(i, ini), p, bid) for (i, f, p, bid) in mapa
                 if i < fim and f > ini]
        por_pag: dict[int, int] = {}
        for qto, p, _ in cobre:
            por_pag[p] = por_pag.get(p, 0) + qto
        pags = sorted(por_pag)
        t = {"tipo": "texto", "texto": corpo,
             "pagina": max(por_pag, key=por_pag.get) if por_pag else None,
             "paginas": pags, "blocos": [b for _, _, b in cobre],
             "_pos": ini}
        if titulos:
            secao = [s for p, s in titulos if p <= ini]
            if secao:
                t["secao"] = secao[-1]
        trechos.append(t)

    # tabelas e figuras entram onde aparecem no documento
    todos = sorted(trechos + inteiros, key=lambda c: (c["_pos"], c["tipo"] != "texto"))
    for i, c in enumerate(todos):
        c.pop("_pos", None)
        c["indice"] = i
    return todos


# ───────────────────────────── payload ──────────────────────────────────────

def id_do_ponto(colecao: str, pdf_id: str, indice: int, estrategia: str | None = None) -> str:
    """Determinístico: o mesmo trecho do mesmo documento na mesma coleção tem
    sempre o mesmo id, então regravar substitui em vez de duplicar."""
    return str(uuid.uuid5(_NS, f"{colecao}|{pdf_id}|{indice}|{versao(estrategia)}"))


def montar_payload(chunk: dict, doc: dict, colecao: str, modelos: dict,
                   ingestao: str, tamanho: int = TAMANHO,
                   sobreposicao: int = SOBREPOSICAO, estrategia: str | None = None) -> dict:
    estrategia = normalizar_estrategia(estrategia)
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
        "chunk_type": ("sb100_blocos" if estrategia == ESTRATEGIA_ANTIGA
                       else f"langchain_{estrategia}"),
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
        "extraido_em": doc.get("extraido_em") or "",
        "versao_chunker": versao(estrategia),
        "estrategia_chunk": estrategia,
        "modelo_denso": modelos.get("denso"),
        "modelo_esparso": modelos.get("esparso"),
    }
    if chunk.get("secao"):
        p["secao"] = chunk["secao"]
    if chunk["tipo"] == "tabela" and chunk.get("tabela_html"):
        p["tabela_html"] = chunk["tabela_html"]
    if chunk["tipo"] == "figura":
        p["figura"] = chunk.get("figura")
        p["legenda"] = chunk.get("legenda")
        p["tipo_figura"] = chunk.get("tipo_figura")
    return p
