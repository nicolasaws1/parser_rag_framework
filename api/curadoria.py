# -*- coding: utf-8 -*-
"""Ponte com a API de curadoria da Squad 1.

Usado tanto pelo endpoint do site (botão "Buscar novos") quanto pelo
scripts/sync_metadados.py, para não existirem duas versões da mesma regra.

O schema da API já mudou uma vez — as chaves eram em português com espaços
("URL DO DOCUMENTO", "APROVAÇÃO CURADOR (marcar)") e hoje são camelCase em inglês.
Por isso o registro cru vai inteiro para `article_metadata.raw` e o mapeamento
fica só em CAMPOS: quando ela mudar de novo, é um lugar para ajustar.
"""
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

API_BASE = os.environ.get("CURATION_API_URL", "https://sb100cientometria.optin.com.br")

# nome da coluna no Supabase -> chave na API
CAMPOS = {
    "title": "title", "authors": "authors", "journal": "journalTitle",
    "doi": "doi", "abstract": "abstract", "keywords": "keywords",
    "publisher": "publisher", "institution": "institution", "location": "location",
    "volume": "volume", "issue": "issue", "pages": "pages",
    "category": "category", "document_type": "documentType",
    "nutrients": "nutrients", "crops": "cropsPresent", "tools": "toolsAndTechniques",
}


def texto(v) -> str:
    """A API mistura string, lista e dict no mesmo campo."""
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(texto(x) for x in v if x)
    if isinstance(v, dict):
        return ", ".join(f"{k}: {texto(x)}" for k, x in v.items() if x)
    return str(v).strip()


def slug_de(nome: str) -> str:
    """Mesmo slug que a ingestão usa: a API traz o nome original, o banco o slug."""
    return re.sub(r"[^a-z0-9]+", "-", Path(nome).stem.lower()).strip("-")[:60] + ".pdf"


def _token() -> str:
    usuario = os.environ.get("CURATION_API_USER", "")
    senha = os.environ.get("CURATION_API_PASSWORD", "")
    if not usuario or not senha:
        raise RuntimeError("defina CURATION_API_USER e CURATION_API_PASSWORD no .env")
    r = requests.post(f"{API_BASE}/api/login",
                      json={"username": usuario, "password": senha}, timeout=30)
    r.raise_for_status()
    token = next((v for v in r.json().values()
                  if isinstance(v, str) and v.startswith("eyJ")), None)
    if not token:
        raise RuntimeError("a API de curadoria respondeu sem token JWT")
    return token


def baixar(token: str | None = None) -> list[dict]:
    """Lista de artigos da curadoria. Levanta se a API não responder."""
    r = requests.get(f"{API_BASE}/api/curation",
                     headers={"Authorization": f"Bearer {token or _token()}"}, timeout=180)
    r.raise_for_status()
    artigos = r.json()
    if not isinstance(artigos, list):
        raise RuntimeError(f"resposta inesperada da API: {type(artigos).__name__}")
    return artigos


def baixar_pdf(nome: str, token: str | None = None) -> bytes:
    """O PDF que a curadoria guarda com esse nome (o mesmo caminho do scripts/acervo.py)."""
    r = requests.get(f"{API_BASE}/api/documents/{requests.utils.quote(nome)}",
                     headers={"Authorization": f"Bearer {token or _token()}"}, timeout=300)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise RuntimeError(f"a curadoria não devolveu um PDF para {nome}")
    return r.content


def aprovado(a: dict) -> bool:
    """Só o que a curadoria aprovou ("Aprovado por IA", "Aprovado"...). Antes
    valia tudo que não fosse rejeitado, e pendente aparecia como novo."""
    return texto(a.get("status")).lower().startswith("aprovad")


def _titulo(a: dict) -> str:
    return re.sub(r"\W+", " ", texto(a.get("title")).lower()).strip()


def agrupar(artigos: list[dict]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Aprovados por nome de arquivo, e os nomes com mais de um artigo.

    O nome do arquivo não é único na curadoria: um PDF de livro ou anais aparece
    em vários registros, cada um com o título de um capítulo. O banco guarda um
    conjunto de metadados por PDF, então nesses casos não há como escolher sem
    chutar; eles voltam em `conflitos` para alguém corrigir na curadoria.
    """
    por_url: dict[str, list[dict]] = {}
    for a in artigos:
        url = texto(a.get("documentUrl"))
        if url and aprovado(a):
            por_url.setdefault(url, []).append(a)
    unicos, conflitos = {}, {}
    for url, regs in por_url.items():
        if len({_titulo(a) for a in regs}) > 1:
            conflitos[url] = regs
        else:
            # o mesmo artigo cadastrado duas vezes: fica o mais recente
            unicos[url] = max(regs, key=lambda a: texto(a.get("updatedAt")))
    return unicos, conflitos


def aprovados(artigos: list[dict]) -> dict[str, dict]:
    """Nome do arquivo -> registro, para cada PDF aprovado (com ou sem conflito)."""
    unicos, conflitos = agrupar(artigos)
    return {**unicos, **{u: regs[-1] for u, regs in conflitos.items()}}


def linha_metadados(a: dict, pdf_id: str, agora: str) -> dict:
    linha = {k: texto(a.get(orig)) for k, orig in CAMPOS.items()}
    try:
        linha["year"] = int(texto(a.get("year"))[:4])
    except ValueError:
        linha["year"] = None
    linha.update({"pdf_id": pdf_id, "raw": a, "api_id": texto(a.get("_id")), "synced_at": agora})
    return linha


def _no_banco(sb) -> dict[str, dict]:
    """slug -> linha de pdfs. Conta também o `document_url`: um PDF pode ter
    entrado com outro nome antes da curadoria existir (mesma regra do acervo.py)."""
    try:
        linhas = sb.table("pdfs").select("id,pdf_file,document_url,extracted").execute().data
    except Exception:
        linhas = sb.table("pdfs").select("id,pdf_file,extracted").execute().data
    fora = {p["pdf_file"]: p for p in linhas}
    for p in linhas:
        if p.get("document_url"):
            fora.setdefault(slug_de(p["document_url"]), p)
    return fora


def sincronizar(sb, gravar: bool = True) -> dict:
    """Compara a curadoria com o banco. Devolve o resumo; grava se `gravar`.

    Não cria documento: atualiza o metadado do que já está no banco e lista os
    aprovados que faltam, que se cadastram um a um com `registrar_novo`. PDF
    com mais de um artigo aprovado fica de fora da atualização (ver `agrupar`).
    """
    artigos = baixar()
    unicos, conflitos = agrupar(artigos)
    no_banco = _no_banco(sb)
    status: dict[str, int] = {}
    for a in artigos:
        st = texto(a.get("status")) or "sem status"
        status[st] = status.get(st, 0) + 1

    def item(url: str, a: dict) -> dict:
        return {"arquivo": url, "titulo": texto(a.get("title")), "ano": texto(a.get("year")),
                "status": texto(a.get("status")), "id": texto(a.get("_id"))}

    novos = [item(u, a) for u, a in sorted(unicos.items()) if slug_de(u) not in no_banco]
    conhecidos = [(u, a) for u, a in unicos.items() if slug_de(u) in no_banco]
    lista_conflitos = [{"arquivo": u, "no_banco": slug_de(u) in no_banco,
                        "artigos": [item(u, a) for a in regs]}
                       for u, regs in sorted(conflitos.items())]

    resumo = {
        "na_api": len(artigos),
        "por_status": status,
        "aprovados": len(unicos) + len(conflitos),
        "no_banco": len({p["id"] for p in no_banco.values()}),
        "ja_conhecidos": len(conhecidos),
        "novos": len(novos),
        "novos_itens": novos,
        "conflitos": lista_conflitos,
        "atualizados": 0,
        "quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not gravar:
        return resumo

    agora = resumo["quando"]
    for u, a in conhecidos:
        pdf = no_banco[slug_de(u)]
        linha = linha_metadados(a, pdf["id"], agora)
        existe = sb.table("article_metadata").select("id").eq("pdf_id", pdf["id"]).execute().data
        if existe:
            sb.table("article_metadata").update(linha).eq("pdf_id", pdf["id"]).execute()
        else:
            sb.table("article_metadata").insert(linha).execute()
        try:
            sb.table("pdfs").update({"curation_status": texto(a.get("status")),
                                     "document_url": u}).eq("id", pdf["id"]).execute()
        except Exception:
            pass          # colunas de curadoria ausentes (migração 006)
        resumo["atualizados"] += 1
    return resumo


def registrar_novo(sb, nome: str) -> dict:
    """Cadastra um aprovado que ainda não está no banco: baixa o PDF da própria
    curadoria, guarda no bucket e cria o documento como "a extrair", com os
    metadados dela. Recusa o que não está aprovado, o que já está no banco e o
    nome com mais de um artigo."""
    import fitz

    token = _token()
    unicos, conflitos = agrupar(baixar(token))
    if nome in conflitos:
        raise ValueError("esse PDF tem mais de um artigo aprovado na curadoria; "
                         "corrija lá antes de cadastrar")
    if nome not in unicos:
        raise ValueError("esse arquivo não está aprovado na curadoria")
    slug = slug_de(nome)
    if slug in _no_banco(sb):
        raise ValueError("esse documento já está no banco")
    a = unicos[nome]

    dados = baixar_pdf(nome, token)
    try:
        doc = fitz.open(stream=dados, filetype="pdf")
        n_paginas = doc.page_count
        doc.close()
    except Exception as e:
        raise ValueError(f"o PDF da curadoria não abre: {e}")

    sb.storage.from_("pdfs").upload(slug, dados,
                                    {"content-type": "application/pdf", "upsert": "true"})
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pdf_id = sb.table("pdfs").insert({
        "pdf_file": slug, "total_pages": n_paginas, "approved": True, "approved_at": agora,
        "extracted": False, "vectorized": False,
        "curation_status": texto(a.get("status")), "document_url": nome,
    }).execute().data[0]["id"]
    sb.table("article_metadata").insert(linha_metadados(a, pdf_id, agora)).execute()
    return {"pdf_id": pdf_id, "pdf_file": slug, "paginas": n_paginas,
            "titulo": texto(a.get("title")), "arquivo": nome, "mb": round(len(dados) / 2**20, 1)}
