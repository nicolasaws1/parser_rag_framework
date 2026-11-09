# -*- coding: utf-8 -*-
"""Liga cada figura à legenda escrita pelo autor, na mesma página.

O extrator só olha o bloco imediatamente antes e depois da figura, e só reconhece
"Figura 1" e "Fig. 1". "Figure 1" e "Gráfico 1" ficavam de fora, e também a legenda
que a ordem de leitura separou da figura. O texto estava lá, num bloco comum, mas o
trecho da figura ia para a busca sem ele. E olhar só o vizinho às vezes pega a
legenda da figura ao lado.

A decisão aqui é pela posição na página: a legenda precisa começar como legenda e
estar encostada na figura. Na dúvida, fica sem legenda: uma figura sem legenda ainda
é encontrada pela descrição, e uma figura com a legenda de outra responde errado.

Sem dependência nenhuma, para rodar igual na ingestão, no script e nos testes.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

TIPOS_FIGURA = ("grafico", "foto")

# "Figura 1", "Fig. 2", "FIGURE 3", "Gráfico 4", "Fig. 3a", "**Figure 5.**"
_INICIO = re.compile(
    r"^[\s#*_>]*(?:fig(?:ure|ura|\.)?|gr[aá]fico|graph|chart|esquema|scheme|diagrama?|foto|photo|plate)"
    r"[\s.*_\-–]*(\d+)\s*([a-zA-Z]?)\b(.*)$",
    re.I | re.S,
)
# "Figure 3 shows..." é um parágrafo que cita a figura, não a legenda dela
_CITA = {
    "shows", "show", "showed", "shown", "presents", "present", "presented", "illustrates", "illustrated",
    "depicts", "depicted", "indicates", "indicate", "demonstrates", "reveals", "summarizes", "displays",
    "suggests", "compares", "is", "are", "was", "were", "and", "or", "to", "in", "also", "gives", "provides",
    "mostra", "mostram", "mostrou", "apresenta", "apresentam", "apresentou", "ilustra", "ilustram", "indica",
    "indicam", "revela", "revelam", "evidencia", "demonstra", "demonstram", "representa", "representam",
    "resume", "compara", "permite", "observa", "observa-se", "nota-se", "e", "ou", "a", "as", "ao", "aos",
    "na", "no", "nas", "nos", "em", "muestra", "muestran", "presenta", "presentan", "ilustran", "y",
}
MAX_CARACTERES = 1500       # mais que isso é parágrafo, não legenda
MAX_DISTANCIA = 0.10        # fração da altura da página entre a figura e a legenda
MIN_SOBREPOSICAO = 0.30     # fração da coluna mais estreita em comum, na horizontal
MAX_VAO_LATERAL = 0.15     # fração da largura da página, para legenda ao lado da figura


def inicio_de_legenda(texto: str) -> int | None:
    """O número da figura, se o texto começa como legenda; senão, None."""
    t = (texto or "").strip()
    if not t or len(t) > MAX_CARACTERES:
        return None
    m = _INICIO.match(t)
    if not m:
        return None
    # só a palavra colada no número decide: "Fig. 2. Shows" tem pontuação antes, é legenda
    primeira = re.match(r"\s*([^\W\d_]+(?:-[^\W\d_]+)?)", m.group(3))
    if primeira and primeira.group(1).lower() in _CITA:
        return None
    return int(m.group(1))


def _bbox(bloco: dict) -> list[float] | None:
    b = bloco.get("bbox")
    return b if isinstance(b, (list, tuple)) and len(b) == 4 else None


def _sem_espaco(texto: str) -> str:
    return re.sub(r"[\W_]+", "", texto or "").lower()


def _distancia(fig: list[float], cap: list[float]) -> float | None:
    """Quão longe a legenda está da figura, ou None se não está encostada.

    Abaixo ganha de acima: é o costume, e numa página com duas figuras empilhadas é
    o que separa a legenda de uma da legenda da outra. Ao lado é o layout de revistas
    que põem a legenda numa coluna estreita junto da figura.
    """
    comum_x = min(fig[2], cap[2]) - max(fig[0], cap[0])
    estreita = min(fig[2] - fig[0], cap[2] - cap[0])
    if estreita > 0 and comum_x / estreita >= MIN_SOBREPOSICAO:
        abaixo = cap[1] - fig[3]
        acima = fig[1] - cap[3]
        if abaixo >= -0.03:                      # a caixa da figura costuma invadir um pouco
            d = max(abaixo, 0.0)
        elif acima >= -0.03:
            d = max(acima, 0.0) + 0.02
        else:
            return None                          # sobreposta à figura: é texto dentro dela
        return d if d <= MAX_DISTANCIA else None
    comum_y = min(fig[3], cap[3]) - max(fig[1], cap[1])
    altura = cap[3] - cap[1]
    vao = max(cap[0] - fig[2], fig[0] - cap[2])
    if altura > 0 and comum_y / altura >= 0.8 and -0.02 <= vao <= MAX_VAO_LATERAL:
        # alinhada ao topo da figura é mais provável que alinhada ao meio dela
        return 0.03 + max(vao, 0.0) + abs(cap[1] - fig[1]) / 4
    return None


def _iou(a: list[float], b: list[float]) -> float:
    x = min(a[2], b[2]) - max(a[0], b[0])
    y = min(a[3], b[3]) - max(a[1], b[1])
    if x <= 0 or y <= 0:
        return 0.0
    inter = x * y
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def _contem(fora: list[float], dentro: list[float]) -> bool:
    """`fora` é bem maior e `dentro` cabe nela."""
    x = min(fora[2], dentro[2]) - max(fora[0], dentro[0])
    y = min(fora[3], dentro[3]) - max(fora[1], dentro[1])
    area_dentro = (dentro[2] - dentro[0]) * (dentro[3] - dentro[1])
    area_fora = (fora[2] - fora[0]) * (fora[3] - fora[1])
    return x > 0 and y > 0 and area_dentro > 0 and x * y / area_dentro >= 0.9 and area_fora >= 1.3 * area_dentro


def associar(blocos: list[dict]) -> list[tuple[dict, str, list[str]]]:
    """Blocos de UMA página -> [(figura, legenda, ids dos blocos de legenda)].

    Decide pela posição, ignorando a legenda que o extrator tenha posto. Cada legenda
    serve a uma figura só, com duas exceções em que é o mesmo conteúdo: o documento
    bilíngue, em que uma figura tem "Figure 2" e "Figura 2", e a figura detectada
    duas vezes no mesmo lugar, que recebe a mesma legenda nas duas cópias.
    """
    figuras = [b for b in blocos if b.get("tipo") in TIPOS_FIGURA and _bbox(b)]
    if not figuras:
        return []
    # o detector às vezes marca uma caixa grande em volta das figuras: as de dentro
    # escolhem primeiro, e a de fora só fica com a legenda que sobrar
    externas = {id(f) for f in figuras if any(g is not f and _contem(_bbox(f), _bbox(g)) for g in figuras)}
    legendas = []
    for b in blocos:
        if b.get("tipo") == "texto" and _bbox(b):
            n = inicio_de_legenda(b.get("md") or "")
            if n is not None:
                legendas.append((b, n))
    pares = []
    for f in figuras:
        for c, n in legendas:
            d = _distancia(_bbox(f), _bbox(c))
            if d is not None:
                pares.append((id(f) in externas, d, id(f), f, c, n))
    pares.sort(key=lambda p: p[:3])

    ligadas: dict[int, tuple[dict, list[dict], int]] = {}
    numero_usado: dict[int, int] = {}
    cap_usada: set[int] = set()
    for _, _, _, f, c, n in pares:
        if id(f) in ligadas or id(c) in cap_usada:
            continue
        # o mesmo número já ligado a outra figura é a mesma legenda repetida pelo extrator
        if n in numero_usado and numero_usado[n] != id(f):
            continue
        ligadas[id(f)] = (f, [c], n)
        numero_usado[n] = id(f)
        cap_usada.add(id(c))
    # a mesma figura em outro idioma: mesmo número, também encostada, texto diferente
    for _, _, _, f, c, n in pares:
        if id(f) not in ligadas or id(c) in cap_usada or ligadas[id(f)][2] != n:
            continue
        cap_usada.add(id(c))
        ja = [_sem_espaco(x.get("md")) for x in ligadas[id(f)][1]]
        novo = _sem_espaco(c.get("md"))
        if not any(novo in x or x in novo or SequenceMatcher(None, novo, x).ratio() > 0.8 for x in ja):
            ligadas[id(f)][1].append(c)

    fora = [(f, "\n\n".join((c.get("md") or "").strip() for c in cs), [c.get("id") for c in cs])
            for f, cs, _ in ligadas.values()]
    # a mesma figura detectada duas vezes
    for f in figuras:
        if id(f) in ligadas:
            continue
        for g, leg, ids in list(fora):
            if _iou(_bbox(f), _bbox(g)) >= 0.7:
                fora.append((f, leg, ids))
                break
    return fora


def aplicar_na_pagina(blocos: list[dict]) -> list[dict]:
    """Grava a legenda nos blocos, do mesmo jeito que o extrator: `caption` com a
    legenda e `md` começando por ela. Devolve as figuras que mudaram.

    A posição manda: se o extrator pôs na figura a legenda da vizinha, é corrigido.
    Quando a posição não acha nada, a legenda do extrator fica. O bloco de texto
    continua onde está, para não mudar o fatiamento do texto corrido.
    """
    achadas = {id(f): leg for f, leg, _ in associar(blocos)}
    dadas = {_sem_espaco(x) for x in achadas.values()}
    mudou = []
    for f in blocos:
        if f.get("tipo") not in TIPOS_FIGURA:
            continue
        antiga = f.get("caption") or ""
        nova = achadas.get(id(f))
        if nova is None:
            # a legenda dela foi para outra figura: melhor sem legenda do que com a errada
            if not antiga or _sem_espaco(antiga) not in dadas:
                continue
            nova = ""
        if _sem_espaco(nova) == _sem_espaco(antiga):
            continue
        md = (f.get("md") or "").strip()
        if antiga and md.startswith(antiga.strip()):
            md = md[len(antiga.strip()):].strip()
        f["caption"] = nova or None
        f["md"] = (nova + "\n\n" + md).strip()
        mudou.append(f)
    return mudou
