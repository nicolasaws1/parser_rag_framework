# -*- coding: utf-8 -*-
"""Vetorização: o lado do site.

Mesmo desenho da extração. O computador que calcula os embeddings (GPU no Colab
ou na nuvem) não recebe chave nenhuma:

    site --pacote_....zip--> GPU --rodar_vetorizacao.py--> vetores_....zip --upload--> site --> Qdrant

O pacote leva os blocos de cada documento (já com as edições manuais aplicadas),
o .md legível e os metadados da curadoria. Quem fatia e calcula os vetores é o
script; quem grava no Qdrant é o site, que tem a chave.

Escolher a coleção é parte do pedido, não detalhe: um documento pode estar em
uma coleção e não em outra, e a pergunta "já foi vetorizado?" só faz sentido
dentro de uma coleção. A resposta vem do próprio Qdrant, por dois caminhos:

    pontos novos   payload.pdf_id
    pontos antigos payload.file  (os scripts de antes gravavam só o nome do
                                  arquivo da curadoria, sem id)

Sem o segundo, o site mandaria revetorizar os 17 mil pontos que já existem.

Só HTTP, sem qdrant-client nem numpy: a CPU do servidor anuncia apenas SSE2, e
um import que exige instrução que a máquina não tem derrubaria o site inteiro.
"""
from __future__ import annotations

import array
import base64
import io
import json
import os
import re
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from api import chunker

MODELO_DENSO = "Qwen/Qwen3-Embedding-0.6B"
MODELO_ESPARSO = "opensearch-project/opensearch-neural-sparse-encoding-doc-v2-distill"
DIM = 1024
NOME_DENSO = "vetor_denso"
NOME_ESPARSO = "vetor_esparso"
MODELOS = {"denso": MODELO_DENSO, "esparso": MODELO_ESPARSO}

MAX_PACOTE = 100            # documentos por pacote
TAMANHO_MAXIMO = 600 * 2**20
LOTE_UPSERT = 64
_NOME_OK = re.compile(r"^[a-z0-9][a-z0-9_]{2,62}$")


# ─────────────────────────────── Qdrant ─────────────────────────────────────

def _qd() -> tuple[str, dict]:
    # dentro do contêiner, localhost é o próprio contêiner: o QDRANT_URL antigo
    # (http://localhost:6333) não alcança o Qdrant. O externo funciona dos dois lados.
    url = (os.environ.get("QDRANT_URL_EXTERNO") or os.environ.get("QDRANT_URL") or "").rstrip("/")
    if not url:
        raise RuntimeError("QDRANT_URL_EXTERNO não está no .env")
    return url, {"api-key": os.environ.get("QDRANT_API_KEY", "")}


def _req(metodo: str, caminho: str, **kw) -> dict:
    url, h = _qd()
    r = httpx.request(metodo, url + caminho, headers=h, timeout=kw.pop("timeout", 60), **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Qdrant {r.status_code}: {r.text[:200]}")
    return r.json()


def compativel(info: dict) -> tuple[bool, str]:
    p = (info.get("config") or {}).get("params") or {}
    v = (p.get("vectors") or {}).get(NOME_DENSO) or {}
    if v.get("size") != DIM:
        return False, f"sem {NOME_DENSO} de {DIM} dimensões"
    if str(v.get("distance", "")).lower() != "cosine":
        return False, "distância não é cosseno"
    if NOME_ESPARSO not in (p.get("sparse_vectors") or {}):
        return False, f"sem {NOME_ESPARSO}"
    return True, ""


def listar_colecoes() -> list[dict]:
    nomes = [c["name"] for c in _req("GET", "/collections")["result"]["collections"]]
    fora = []
    for n in sorted(nomes):
        info = _req("GET", f"/collections/{n}")["result"]
        ok, motivo = compativel(info)
        fora.append({"nome": n, "pontos": info.get("points_count") or 0,
                     "compativel": ok, "motivo": motivo,
                     "teste": n.startswith("teste_")})
    return fora


def criar_colecao(nome: str) -> dict:
    if not _NOME_OK.match(nome or ""):
        raise ValueError("nome: 3 a 63 caracteres, só minúsculas, números e _")
    existentes = {c["name"] for c in _req("GET", "/collections")["result"]["collections"]}
    if nome in existentes:
        raise ValueError(f"a coleção {nome} já existe")
    _req("PUT", f"/collections/{nome}", json={
        "vectors": {NOME_DENSO: {"size": DIM, "distance": "Cosine"}},
        "sparse_vectors": {NOME_ESPARSO: {}},
    })
    _indices(nome)
    return {"nome": nome, "pontos": 0, "compativel": True, "motivo": "",
            "teste": nome.startswith("teste_")}


def _indices(colecao: str) -> None:
    """Índice de payload em pdf_id e file: filtrar e apagar por documento fica
    instantâneo. Idempotente; nas coleções antigas é criado na primeira vez."""
    for campo in ("pdf_id", "file"):
        try:
            _req("PUT", f"/collections/{colecao}/index", params={"wait": "true"},
                 json={"field_name": campo, "field_schema": "keyword"})
        except RuntimeError:
            pass


def remover_colecao(nome: str) -> None:
    _req("DELETE", f"/collections/{nome}")


# ─────────────────── quem já está em cada coleção ──────────────────────────

_CACHE: dict[str, tuple[float, dict]] = {}
# coleção -> pdf_id -> os valores de `file` exatamente como estão nos pontos
# antigos. Apagar precisa do valor exato (o filtro do Qdrant não normaliza), e
# os scripts de antes gravaram ora "X.pdf", ora "X.md".
_ARQUIVOS: dict[str, dict[str, set[str]]] = {}


def _chave_arquivo(nome: str) -> str:
    """Normaliza o `file` dos pontos antigos e o `document_url` do banco para a
    mesma forma: os scripts antigos gravavam ora o .pdf, ora o .md."""
    base = re.sub(r"\.(pdf|md)$", "", (nome or "").strip(), flags=re.I)
    return re.sub(r"\s+", " ", base).lower()


def pontos_por_documento(sb, colecao: str, usar_cache: bool = True) -> dict[str, int]:
    """pdf_id -> quantos pontos ele tem nesta coleção (novos e antigos)."""
    agora = time.time()
    if usar_cache and colecao in _CACHE and agora - _CACHE[colecao][0] < 60:
        return _CACHE[colecao][1]

    docs = sb.table("pdfs").select("id,pdf_file,document_url").execute().data
    por_arquivo: dict[str, str] = {}
    for d in docs:
        for nome in (d.get("document_url"), d.get("pdf_file")):
            if nome:
                por_arquivo[_chave_arquivo(nome)] = d["id"]

    contagem: dict[str, int] = {}
    arquivos: dict[str, set[str]] = {}
    oferta = None
    while True:
        corpo = {"limit": 1000, "with_payload": ["pdf_id", "file"], "with_vector": False}
        if oferta is not None:
            corpo["offset"] = oferta
        r = _req("POST", f"/collections/{colecao}/points/scroll", json=corpo, timeout=120)["result"]
        for p in r["points"]:
            pl = p.get("payload") or {}
            pid = pl.get("pdf_id") or por_arquivo.get(_chave_arquivo(pl.get("file") or ""))
            if pid:
                contagem[pid] = contagem.get(pid, 0) + 1
                if not pl.get("pdf_id") and pl.get("file"):
                    arquivos.setdefault(pid, set()).add(pl["file"])
        oferta = r.get("next_page_offset")
        if oferta is None:
            break
    _CACHE[colecao] = (agora, contagem)
    _ARQUIVOS[colecao] = arquivos
    return contagem


def config_da_colecao(colecao: str) -> dict | None:
    """Fatiamento dos pontos que já estão lá, para avisar quando misturar."""
    r = _req("POST", f"/collections/{colecao}/points/scroll",
             json={"limit": 1, "with_payload": ["config_chunk", "config_overlap",
                                                 "chunk_type", "versao_chunker"],
                   "with_vector": False})["result"]["points"]
    return (r[0].get("payload") or {}) if r else None


def status_documentos(sb, colecao: str) -> list[dict]:
    """Documentos extraídos e a situação de cada um NESTA coleção."""
    pontos = pontos_por_documento(sb, colecao)
    pdfs = (sb.table("pdfs").select("id,pdf_file,total_pages,extracted,document_url")
            .eq("extracted", True).execute().data)
    ids = [p["id"] for p in pdfs]
    metas = {m["pdf_id"]: m for m in sb.table("article_metadata")
             .select("pdf_id,title,year,category").in_("pdf_id", ids).execute().data}
    try:
        reg = {v["pdf_id"]: v for v in sb.table("vectorizations").select("*")
               .eq("collection", colecao).execute().data}
    except Exception:
        reg = {}
    try:
        editados = {e["pdf_id"] for e in sb.table("document_edits").select("pdf_id").execute().data}
    except Exception:
        editados = set()
    fora = []
    for p in pdfs:
        n = pontos.get(p["id"], 0)
        r = reg.get(p["id"])
        fora.append({
            "pdf_id": p["id"], "arquivo": p["pdf_file"], "paginas": p["total_pages"],
            "titulo": (metas.get(p["id"]) or {}).get("title"),
            "ano": (metas.get(p["id"]) or {}).get("year"),
            "vetorizado": n > 0, "pontos": n,
            "quando": (r or {}).get("finished_at"),
            "como": ({"chunk": r.get("chunk_size"), "overlap": r.get("chunk_overlap"),
                      "denso": r.get("dense_model"), "origem": r.get("origem_texto")}
                     if r else ({"legado": True} if n else None)),
            "editado": p["id"] in editados,
        })
    fora.sort(key=lambda d: (d["vetorizado"], (d["titulo"] or d["arquivo"]).lower()))
    return fora


# ─────────────────────────── pacote de ida ──────────────────────────────────

def paginas_do_documento(sb, pdf_id: str) -> tuple[list[dict], str]:
    """Blocos na forma do layout.json, com a edição manual aplicada por cima.

    A curadoria corrige o texto na tela de Analisar; vetorizar a extração crua
    jogaria fora essa correção. Devolve também de onde veio o texto.
    """
    bl, off = [], 0
    while True:
        lote = (sb.table("page_blocks").select("page_number,block_type,markdown_text,bbox,layout")
                .eq("pdf_id", pdf_id).range(off, off + 999).execute().data)
        if not lote:
            break
        bl += lote
        off += 1000
    por_pag: dict[int, list] = {}
    for b in bl:
        lay = dict(b.get("layout") or {})
        lay.setdefault("tipo", b.get("block_type"))
        lay.setdefault("md", b.get("markdown_text"))
        lay.setdefault("bbox", b.get("bbox"))
        por_pag.setdefault(b["page_number"], []).append(lay)

    origem = "extracao"
    try:
        ed = (sb.table("document_edits").select("layout").eq("pdf_id", pdf_id)
              .execute().data)
        for pg in ((ed[0].get("layout") if ed else None) or []):
            if isinstance(pg, dict) and pg.get("n") is not None and pg.get("blocos") is not None:
                por_pag[pg["n"]] = pg["blocos"]
                origem = "edicao"
    except Exception:
        pass
    return [{"n": n, "blocos": v} for n, v in sorted(por_pag.items())], origem


def _md(paginas: list[dict], titulo: str) -> str:
    linhas = [f"# {titulo}\n"]
    for pg in paginas:
        linhas.append(f"\n---\n\n## Página {pg['n']}\n")
        for b in sorted(pg["blocos"], key=chunker._ordem):
            if b.get("md"):
                linhas.append(b["md"].strip() + "\n")
    return "\n".join(linhas)


def montar_pacote(sb, colecao: str, ids: list[str], quem: str) -> tuple[bytes, dict]:
    if not ids:
        raise ValueError("escolha ao menos um documento")
    if len(ids) > MAX_PACOTE:
        raise ValueError(f"no máximo {MAX_PACOTE} documentos por pacote")
    pdfs = (sb.table("pdfs").select("id,pdf_file,total_pages,extracted,document_url")
            .in_("id", ids).execute().data)
    nao = [p["pdf_file"] for p in pdfs if not p["extracted"]]
    if nao:
        raise ValueError(f"{len(nao)} ainda não extraído(s): {nao[0]}")
    metas = {m["pdf_id"]: m for m in sb.table("article_metadata").select("*")
             .in_("pdf_id", ids).execute().data}

    pacote = datetime.now().strftime("%Y%m%d-%H%M") + "-" + uuid.uuid4().hex[:6]
    man = {"pacote": pacote, "colecao": colecao, "criado_por": quem,
           "criado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "chunk": {"tamanho": chunker.TAMANHO, "sobreposicao": chunker.SOBREPOSICAO,
                     "versao": chunker.VERSAO},
           "modelos": MODELOS, "documentos": []}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in pdfs:
            paginas, origem = paginas_do_documento(sb, p["id"])
            m = {k: v for k, v in (metas.get(p["id"]) or {}).items()
                 if k not in ("raw", "pdf_id", "id")}
            slug = p["pdf_file"].removesuffix(".pdf")
            z.writestr(f"docs/{slug}/blocos.json", json.dumps(paginas, ensure_ascii=False))
            z.writestr(f"docs/{slug}/{slug}.md", _md(paginas, m.get("title") or slug))
            man["documentos"].append({
                "pdf_id": p["id"], "pdf_file": p["pdf_file"], "slug": slug,
                "file": p.get("document_url") or p["pdf_file"],
                "paginas": p["total_pages"], "origem_texto": origem, "metadados": m})
        z.writestr("pacote.json", json.dumps(man, ensure_ascii=False, indent=1))
    return buf.getvalue(), {"pacote": pacote, "documentos": len(man["documentos"]),
                            "colecao": colecao}


# ───────────────────────── vetores de volta ─────────────────────────────────

def decodificar_denso(b64: str) -> list[float]:
    a = array.array("f")
    a.frombytes(base64.b64decode(b64))
    return a.tolist()


def abrir_vetores(dados: bytes, destino: Path) -> Path:
    if len(dados) > TAMANHO_MAXIMO:
        raise ValueError("pacote acima de 600 MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(dados))
    except zipfile.BadZipFile:
        raise ValueError("não é um arquivo .zip válido")
    for nome in z.namelist():
        alvo = (destino / nome).resolve()
        if not str(alvo).startswith(str(destino.resolve())):
            raise ValueError(f"caminho suspeito no zip: {nome}")
    z.extractall(destino)
    for cand in [destino] + sorted(p.parent for p in destino.glob("*/vetores.jsonl")):
        if (cand / "vetores.jsonl").exists() and (cand / "pacote.json").exists():
            return cand
    raise ValueError("faltam vetores.jsonl e pacote.json: é a saída do rodar_vetorizacao?")


def _ler(pasta: Path) -> tuple[dict, dict[str, list[dict]]]:
    man = json.loads((pasta / "pacote.json").read_text(encoding="utf-8"))
    por_doc: dict[str, list[dict]] = {}
    with open(pasta / "vetores.jsonl", encoding="utf-8") as f:
        for n, linha in enumerate(f, 1):
            if not linha.strip():
                continue
            try:
                p = json.loads(linha)
            except Exception:
                raise ValueError(f"vetores.jsonl, linha {n}: JSON inválido")
            por_doc.setdefault(p.get("pdf_id"), []).append(p)
    return man, por_doc


def conferir_vetores(sb, pasta: Path, colecao: str | None = None) -> dict:
    man, por_doc = _ler(pasta)
    col = colecao or man.get("colecao")
    cols = {c["nome"]: c for c in listar_colecoes()}
    if col not in cols:
        raise ValueError(f"a coleção {col} não existe no Qdrant")
    if not cols[col]["compativel"]:
        raise ValueError(f"a coleção {col} não aceita estes vetores: {cols[col]['motivo']}")

    avisos = []
    atual = config_da_colecao(col)
    # coleções antigas sem config_chunk no payload não dizem como foram fatiadas:
    # nesse caso não há com o que comparar
    if atual and atual.get("config_chunk") is not None and (
            str(atual.get("config_chunk")) != str(man["chunk"]["tamanho"])
            or str(atual.get("config_overlap")) != str(man["chunk"]["sobreposicao"])):
        avisos.append(f"a coleção já tem trechos de {atual.get('config_chunk')} com sobreposição "
                      f"{atual.get('config_overlap')}; este pacote usa {man['chunk']['tamanho']}/"
                      f"{man['chunk']['sobreposicao']}. Misturar fatiamentos atrapalha a busca.")

    pontos = pontos_por_documento(sb, col, usar_cache=False)
    itens = []
    for d in man.get("documentos", []):
        pts = por_doc.get(d["pdf_id"], [])
        it = {"pdf_id": d["pdf_id"], "arquivo": d["pdf_file"], "trechos": len(pts),
              "titulo": (d.get("metadados") or {}).get("title")}
        existe = sb.table("pdfs").select("id").eq("id", d["pdf_id"]).execute().data
        if not existe:
            it.update(situacao="desconhecido", motivo="o documento saiu do acervo")
        elif not pts:
            it.update(situacao="invalido", motivo="nenhum vetor para este documento")
        elif any(len(decodificar_denso(p["denso"])) != DIM for p in pts[:3]):
            it.update(situacao="invalido", motivo=f"vetor denso sem {DIM} dimensões")
        elif pontos.get(d["pdf_id"]):
            it.update(situacao="ja_vetorizado",
                      motivo=f"já tem {pontos[d['pdf_id']]} pontos em {col}")
        else:
            it.update(situacao="novo")
        itens.append(it)
    return {"colecao": col, "pacote": man.get("pacote"), "avisos": avisos, "itens": itens,
            "chunk": man.get("chunk"), "modelos": man.get("modelos")}


def importar_vetores(sb, pasta: Path, substituir: bool = False,
                     colecao: str | None = None) -> dict:
    rel = conferir_vetores(sb, pasta, colecao)
    col = rel["colecao"]
    man, por_doc = _ler(pasta)
    docs = {d["pdf_id"]: d for d in man["documentos"]}
    _indices(col)
    gravados, pulados, falhas = [], [], []
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for it in rel["itens"]:
        if it["situacao"] == "ja_vetorizado" and not substituir:
            pulados.append(it); continue
        if it["situacao"] in ("invalido", "desconhecido"):
            falhas.append(it); continue
        d = docs[it["pdf_id"]]
        inicio = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            if it["situacao"] == "ja_vetorizado":
                # tira os pontos dele, novos e antigos, antes de gravar os de agora
                filtro = [{"key": "pdf_id", "match": {"value": d["pdf_id"]}}]
                antigos = sorted(_ARQUIVOS.get(col, {}).get(d["pdf_id"], ()))
                if antigos:
                    filtro.append({"key": "file", "match": {"any": antigos}})
                _req("POST", f"/collections/{col}/points/delete", params={"wait": "true"},
                     json={"filter": {"should": filtro}})
            pts = por_doc[d["pdf_id"]]
            for i in range(0, len(pts), LOTE_UPSERT):
                corpo = [{"id": p["id"], "payload": p["payload"],
                          "vector": {NOME_DENSO: decodificar_denso(p["denso"]),
                                     NOME_ESPARSO: p["esparso"]}}
                         for p in pts[i:i + LOTE_UPSERT]]
                _req("PUT", f"/collections/{col}/points", params={"wait": "true"},
                     json={"points": corpo}, timeout=180)
            linha = {"pdf_id": d["pdf_id"], "collection": col, "dense_model": man["modelos"]["denso"],
                     "dense_dim": DIM, "sparse_model": man["modelos"]["esparso"],
                     "chunk_size": man["chunk"]["tamanho"], "chunk_overlap": man["chunk"]["sobreposicao"],
                     "chunks": len(pts), "pontos": len(pts), "origem_texto": d.get("origem_texto"),
                     "started_at": inicio, "finished_at": agora, "ok": True, "erro": None}
            sb.table("vectorizations").upsert(linha, on_conflict="pdf_id,collection").execute()
            sb.table("pdfs").update({"vectorized": True, "vectorized_at": agora[:19]}) \
                .eq("id", d["pdf_id"]).execute()
            gravados.append({**it, "pontos": len(pts)})
        except Exception as e:
            falhas.append({**it, "situacao": "erro", "motivo": str(e)[:160]})
            try:
                sb.table("vectorizations").upsert(
                    {"pdf_id": d["pdf_id"], "collection": col, "ok": False, "erro": str(e)[:300],
                     "started_at": inicio, "finished_at": agora},
                    on_conflict="pdf_id,collection").execute()
            except Exception:
                pass
    _CACHE.pop(col, None)
    return {"colecao": col, "gravados": gravados, "pulados": pulados, "falhas": falhas,
            "avisos": rel["avisos"]}


# ──────────────────────── vetorizar no próprio site ──────────────────────────

def capacidade() -> dict:
    """O servidor consegue calcular embeddings? Sondado num processo à parte.

    Importar torch numa CPU sem as instruções que ele exige termina em
    "Illegal instruction" e mata o processo que importou. Se fosse a API, o site
    cairia. Num subprocesso, o pior que acontece é este relatório dizer não.
    """
    import subprocess
    import sys
    flags = set()
    try:
        for linha in open("/proc/cpuinfo"):
            if linha.startswith("flags"):
                flags = set(linha.split(":", 1)[1].split())
                break
    except Exception:
        pass
    cpu = {"avx": "avx" in flags, "avx2": "avx2" in flags, "fma": "fma" in flags,
           "conhecida": bool(flags)}
    if flags and not cpu["avx"]:
        return {"pode": False, "cpu": cpu,
                "motivo": "a CPU deste servidor não tem AVX (máquina virtual com modelo de "
                          "CPU genérico). Calcular embeddings aqui seria inviável. Use o pacote "
                          "para GPU, ou peça a quem administra para expor a CPU real à VM."}
    try:
        r = subprocess.run(
            [sys.executable, "-c",
             "import torch, sentence_transformers, transformers;"
             "print(torch.backends.cpu.get_cpu_capability())"],
            capture_output=True, text=True, timeout=180)
    except Exception as e:
        return {"pode": False, "cpu": cpu, "motivo": f"sondagem falhou: {e}"}
    if r.returncode != 0:
        morte = "instrução ilegal" if r.returncode in (-4, 132) else (r.stderr.strip().splitlines() or ["?"])[-1][:140]
        return {"pode": False, "cpu": cpu,
                "motivo": f"as bibliotecas de embedding não carregam aqui ({morte}). "
                          f"Instale requirements-vetorizador.txt num servidor com AVX."}
    return {"pode": True, "cpu": cpu, "torch_cpu": r.stdout.strip(), "motivo": ""}
