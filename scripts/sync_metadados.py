"""
Sincroniza os metadados da API de curadoria (Squad 1) com o Supabase.

É o PRIMEIRO passo do ciclo: descobrir o que existe na curadoria, comparar com o
que já está no banco e dizer o que é novo. Só depois vem extração e vetorização.

    python scripts/sync_metadados.py            # mostra o diagnóstico, não grava
    python scripts/sync_metadados.py --aplicar  # grava no Supabase

ATENÇÃO — o schema da API já mudou uma vez. As chaves eram em português com
espaços ("URL DO DOCUMENTO", "APROVAÇÃO CURADOR (marcar)", "Título") e hoje são
camelCase em inglês (documentUrl, status, title). O script antigo do usuário,
rodado contra a API atual, encontra ZERO artigos. Por isso o registro cru vai
para `article_metadata.raw` em jsonb: quando ela mudar de novo, o dado não se
perde e o mapeamento é ajustado em um lugar só (CAMPOS, abaixo).
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

API_BASE = os.environ.get("CURATION_API_URL", "https://sb100cientometria.optin.com.br")
API_USER = os.environ.get("CURATION_API_USER", "")
API_PWD = os.environ.get("CURATION_API_PASSWORD", "")
APLICAR = "--aplicar" in sys.argv

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

# nome no Supabase -> chave na API. Único lugar a mexer quando a API mudar.
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


def baixar_curadoria() -> list[dict]:
    if not API_USER or not API_PWD:
        sys.exit("defina CURATION_API_USER e CURATION_API_PASSWORD no .env")
    r = requests.post(f"{API_BASE}/api/login",
                      json={"username": API_USER, "password": API_PWD}, timeout=30)
    r.raise_for_status()
    token = next((v for v in r.json().values()
                  if isinstance(v, str) and v.startswith("eyJ")), None)
    if not token:
        sys.exit("a API respondeu sem token JWT")
    r = requests.get(f"{API_BASE}/api/curation",
                     headers={"Authorization": f"Bearer {token}"}, timeout=180)
    r.raise_for_status()
    artigos = r.json()
    if not isinstance(artigos, list):
        sys.exit(f"resposta inesperada da API: {type(artigos)}")
    return artigos


def main() -> None:
    artigos = baixar_curadoria()
    # aprovado = tudo que a curadoria não rejeitou
    aprovados = {}
    for a in artigos:
        url = texto(a.get("documentUrl"))
        if not url:
            continue
        if texto(a.get("status")).lower().startswith("rejeit"):
            continue
        aprovados[url] = a

    # document_url só existe depois da migração 006; o diagnóstico roda sem ela
    try:
        linhas = sb.table("pdfs").select("id,pdf_file,document_url,extracted").execute().data
    except Exception:
        linhas = sb.table("pdfs").select("id,pdf_file,extracted").execute().data
    no_banco = {p["pdf_file"]: p for p in linhas}
    # o banco guarda o slug ('alva-2005-nue.pdf') e a API o nome original
    import re
    slug = lambda n: re.sub(r"[^a-z0-9]+", "-", Path(n).stem.lower()).strip("-")[:60] + ".pdf"
    por_slug = {slug(u): (u, a) for u, a in aprovados.items()}

    novos = [u for s, (u, _) in por_slug.items() if s not in no_banco]
    conhecidos = [(s, u) for s, (u, _) in por_slug.items() if s in no_banco]

    print(f"API de curadoria .......... {len(artigos)} artigos")
    print(f"   aprovados (não rejeitados) {len(aprovados)}")
    print(f"no Supabase ............... {len(no_banco)} PDFs")
    print(f"   já conhecidos ........... {len(conhecidos)}")
    print(f"   NOVOS na curadoria ...... {len(novos)}")
    for u in novos[:10]:
        print(f"      + {u[:70]}")
    if len(novos) > 10:
        print(f"      ... e outros {len(novos)-10}")

    if not APLICAR:
        print("\n(diagnóstico apenas — use --aplicar para gravar os metadados)")
        return

    agora = datetime.now(timezone.utc).isoformat()
    atualizados = 0
    for s, u in conhecidos:
        a = por_slug[s][1]
        pdf = no_banco[s]
        linha = {k: texto(a.get(orig)) for k, orig in CAMPOS.items()}
        linha["year"] = None
        try:
            linha["year"] = int(texto(a.get("year"))[:4])
        except ValueError:
            pass
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
        except Exception as e:
            print(f"   (colunas de curadoria ausentes — aplique 006: {str(e)[:60]})")
        atualizados += 1
    print(f"\nmetadados atualizados: {atualizados}")
    try:
        sb.table("audit_log").insert({
            "evento": "metadados_sincronizados", "ator": "sync_metadados.py",
            "detalhe": {"na_api": len(artigos), "aprovados": len(aprovados),
                        "atualizados": atualizados, "novos_na_curadoria": len(novos)},
        }).execute()
    except Exception as e:
        print(f"(auditoria não gravou: {e})")


if __name__ == "__main__":
    main()
