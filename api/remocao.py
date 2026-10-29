# -*- coding: utf-8 -*-
"""Tirar um documento do acervo, com tudo o que depende dele.

    banco     pdfs (e, em cascata, metadados, páginas, blocos, edição e o
              registro de vetorização)
    bucket    o PDF em `pdfs` e as imagens das páginas e figuras em `images`
    Qdrant    os pontos dele em cada coleção, novos (pdf_id) e antigos (file)

A ordem é Qdrant, bucket e por último o banco: se algo falhar no meio, o
documento continua listado e dá para remover de novo. Apagar a linha primeiro
deixaria vetores e arquivos órfãos que ninguém mais acha pela tela.

O resumo vem antes, para quem confirma saber o que vai sumir. Ele percorre as
coleções (a mesma conta da aba Vetorização, com o mesmo cache de 60 s), o que
leva alguns segundos na primeira vez.
"""
from __future__ import annotations

from api import extracao
from api import vetorizacao as V


def _imagens(sb, prefixo: str) -> list[str]:
    fora = []
    for sub in ("pages", "figuras"):
        inicio = 0
        while True:
            lote = sb.storage.from_("images").list(f"{prefixo}/{sub}",
                                                   {"limit": 100, "offset": inicio})
            if not lote:
                break
            fora += [f"{prefixo}/{sub}/{x['name']}" for x in lote]
            if len(lote) < 100:
                break
            inicio += 100
    return fora


def resumo(sb, pdf_id: str) -> dict:
    linha = (sb.table("pdfs").select("id,pdf_file,document_url,extracted,vectorized,total_pages")
             .eq("id", pdf_id).execute().data)
    if not linha:
        raise LookupError("documento não encontrado")
    p = linha[0]
    meta = (sb.table("article_metadata").select("title").eq("pdf_id", pdf_id)
            .execute().data or [{}])[0]
    blocos = sb.table("page_blocks").select("id", count="exact").eq("pdf_id", pdf_id).execute().count
    prefixo = extracao.prefixo_do_documento(sb, pdf_id)
    imagens = _imagens(sb, prefixo) if prefixo else []

    vetores, sem_resposta = {}, []
    for c in V._req("GET", "/collections")["result"]["collections"]:
        try:
            n = V.pontos_por_documento(sb, c["name"]).get(pdf_id, 0)
        except Exception:
            sem_resposta.append(c["name"])
            continue
        if n:
            vetores[c["name"]] = n
    return {"pdf_id": pdf_id, "arquivo": p["pdf_file"], "titulo": meta.get("title") or "",
            "paginas": p["total_pages"], "extraido": p["extracted"], "blocos": blocos or 0,
            "imagens": len(imagens), "vetores": vetores, "colecoes_sem_resposta": sem_resposta,
            "_prefixo": prefixo, "_imagens": imagens}


def remover(sb, pdf_id: str, vetores: bool = True) -> dict:
    r = resumo(sb, pdf_id)
    if vetores and r["colecoes_sem_resposta"]:
        raise RuntimeError("não deu para conferir as coleções " + ", ".join(r["colecoes_sem_resposta"])
                           + "; nada foi apagado")
    if vetores:
        for col in r["vetores"]:
            filtro = [{"key": "pdf_id", "match": {"value": pdf_id}}]
            # pontos dos scripts antigos só têm o nome do arquivo, do jeito que estava lá
            antigos = sorted(V._ARQUIVOS.get(col, {}).get(pdf_id, ()))
            if antigos:
                filtro.append({"key": "file", "match": {"any": antigos}})
            V._req("POST", f"/collections/{col}/points/delete", params={"wait": "true"},
                   json={"filter": {"should": filtro}})
            # tira só este documento da conta guardada: removendo vários em
            # seguida, a próxima remoção não precisa varrer a coleção de novo
            if col in V._CACHE:
                V._CACHE[col][1].pop(pdf_id, None)
            V._ARQUIVOS.get(col, {}).pop(pdf_id, None)
            V._NOVOS.get(col, {}).pop(pdf_id, None)

    imgs = r.pop("_imagens")
    r.pop("_prefixo")
    for i in range(0, len(imgs), 100):
        sb.storage.from_("images").remove(imgs[i:i + 100])
    try:
        sb.storage.from_("pdfs").remove([r["arquivo"]])
    except Exception:
        pass          # sem o binário no bucket não há o que tirar

    sb.table("pdfs").delete().eq("id", pdf_id).execute()
    r["vetores_apagados"] = vetores
    return r
