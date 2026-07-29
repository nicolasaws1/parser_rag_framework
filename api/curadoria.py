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


def baixar() -> list[dict]:
    """Lista de artigos da curadoria. Levanta se a API não responder."""
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
    r = requests.get(f"{API_BASE}/api/curation",
                     headers={"Authorization": f"Bearer {token}"}, timeout=180)
    r.raise_for_status()
    artigos = r.json()
    if not isinstance(artigos, list):
        raise RuntimeError(f"resposta inesperada da API: {type(artigos).__name__}")
    return artigos


def aprovados(artigos: list[dict]) -> dict[str, dict]:
    """Aprovado = tudo que a curadoria não rejeitou. Chave: nome do arquivo."""
    fora = {}
    for a in artigos:
        url = texto(a.get("documentUrl"))
        if not url or texto(a.get("status")).lower().startswith("rejeit"):
            continue
        fora[url] = a
    return fora


def sincronizar(sb, gravar: bool = True) -> dict:
    """Compara a curadoria com o banco. Devolve o resumo; grava se `gravar`.

    Não cria PDF novo: só atualiza o metadado do que já está no banco e informa o
    que apareceu na curadoria. Registrar um PDF novo depende do binário, que vem
    por outro caminho (scripts/registrar_pdf.py).
    """
    artigos = baixar()
    aprov = aprovados(artigos)
    por_slug = {slug_de(u): (u, a) for u, a in aprov.items()}

    try:
        linhas = sb.table("pdfs").select("id,pdf_file,document_url,extracted").execute().data
    except Exception:
        linhas = sb.table("pdfs").select("id,pdf_file,extracted").execute().data
    no_banco = {p["pdf_file"]: p for p in linhas}

    novos = sorted(u for s, (u, _) in por_slug.items() if s not in no_banco)
    conhecidos = [(s, u) for s, (u, _) in por_slug.items() if s in no_banco]

    resumo = {
        "na_api": len(artigos),
        "aprovados": len(aprov),
        "no_banco": len(no_banco),
        "ja_conhecidos": len(conhecidos),
        "novos": len(novos),
        "novos_exemplos": novos[:25],
        "atualizados": 0,
        "quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not gravar:
        return resumo

    agora = resumo["quando"]
    for s, u in conhecidos:
        a = por_slug[s][1]
        pdf = no_banco[s]
        linha = {k: texto(a.get(orig)) for k, orig in CAMPOS.items()}
        try:
            linha["year"] = int(texto(a.get("year"))[:4])
        except ValueError:
            linha["year"] = None
        linha.update({"pdf_id": pdf["id"], "raw": a,
                      "api_id": texto(a.get("_id")), "synced_at": agora})
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
