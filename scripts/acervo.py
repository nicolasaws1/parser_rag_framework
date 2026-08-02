"""
Reconcilia as três fontes de verdade dos PDFs aprovados e fecha as lacunas.

    curadoria (API)  ->  o que DEVE existir
    pasta local      ->  o binário que já temos
    Supabase         ->  o que o site enxerga

    python scripts/acervo.py                    # situação (não escreve nada)
    python scripts/acervo.py --baixar           # busca na API só o que falta no disco
    python scripts/acervo.py --registrar        # cria no banco o que já está no disco
    python scripts/acervo.py --binarios         # sobe ao bucket o PDF que falta
    python scripts/acervo.py --tudo             # os três, na ordem
    python scripts/acervo.py --verificar        # relê cada PDF do disco (lento)

A curadoria manda: só entra no banco o que ela aprova. PDF solto na pasta é
listado como extra e ignorado — nenhuma ação aqui registra fora dessa lista.

Baixar e registrar são separados de propósito: quase todo PDF aprovado já está
no disco, e re-baixar 180 arquivos que já temos é desperdício. O download é só
para o buraco que sobra.

A pasta de destino é a mesma de onde já se lê (PDF_DIR ou 'PDFS aprovados'), para
não criar um segundo acervo que precise ser reconciliado depois.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import fitz
import requests
from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    # line_buffering: sem isso o progresso some no buffer quando se redireciona
    # para arquivo, e um lote de 200 fica horas sem dar sinal de vida
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from api import curadoria  # noqa: E402  (precisa do .env carregado antes)

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
API_BASE = os.environ["CURATION_API_URL"]

PASTA = Path(os.environ.get(
    "PDF_DIR", Path.home() / "OneDrive" / "Área de Trabalho" / "SB100" / "PDFS aprovados"))


# ─────────────────────────── leitura das três fontes ────────────────────────

def varrer_disco() -> dict[str, Path]:
    """slug -> arquivo. Nomes iguais a menos de '(1)' colidem no mesmo slug;
    fica o mais curto, que é o original."""
    por_slug: dict[str, Path] = {}
    for p in sorted(PASTA.glob("*.pdf")):
        s = curadoria.slug_de(p.name)
        if s not in por_slug or len(p.name) < len(por_slug[s].name):
            por_slug[s] = p
    return por_slug


def ler_banco() -> set[str]:
    """Slugs já registrados. Inclui os de `document_url` porque um PDF pode ter
    entrado com nome próprio antes de a curadoria existir: é o mesmo documento,
    e sem isso ele voltaria à fila e entraria duplicado."""
    linhas = sb.table("pdfs").select("pdf_file,document_url").execute().data
    slugs = {p["pdf_file"] for p in linhas}
    slugs |= {curadoria.slug_de(p["document_url"]) for p in linhas if p.get("document_url")}
    return slugs


def ler_storage() -> set[str]:
    """Nomes no bucket `pdfs`. Pagina: list() devolve 100 por vez em silêncio,
    e sem paginar 130 arquivos existentes passam por ausentes."""
    nomes: list[str] = []
    passo = 100
    while True:
        lote = sb.storage.from_("pdfs").list(options={"limit": passo, "offset": len(nomes)})
        if not lote:
            break
        nomes += [o["name"] for o in lote]
    return {n for n in nomes if n.lower().endswith(".pdf")}


def situacao() -> dict:
    aprov = curadoria.aprovados(curadoria.baixar())
    por_slug = {curadoria.slug_de(n): n for n in aprov}
    disco = varrer_disco()
    banco = ler_banco()

    est = {"aprovados": aprov, "slug_para_nome": por_slug, "disco": disco, "banco": banco,
           "baixar": [], "registrar": [], "completos": []}
    for s, nome in sorted(por_slug.items()):
        if s not in disco:
            est["baixar"].append((s, nome))
        elif s not in banco:
            est["registrar"].append((s, nome))
        else:
            est["completos"].append((s, nome))
    est["extras"] = sorted(s for s in disco if s not in por_slug)
    # registro sem binário: o site abre o documento e não acha o PDF. Já
    # aconteceu com 6 dos primeiros, subidos por um script que só gravava a linha
    guardados = ler_storage()
    est["sem_binario"] = sorted(s for s in por_slug if s in banco and s not in guardados)
    est["storage"] = guardados
    return est


def imprimir(est: dict) -> None:
    n = len(est["aprovados"])
    print(f"curadoria .... {n} aprovados")
    print(f"disco ........ {len(est['disco'])} PDFs em {PASTA}")
    print(f"banco ........ {len(est['banco'])} registros no Supabase")
    print(f"storage ...... {len(est['storage'])} PDFs no bucket")
    print()
    print(f"  completos (disco + banco) .......... {len(est['completos']):>4}")
    print(f"  no disco, falta registrar .......... {len(est['registrar']):>4}")
    print(f"  falta baixar da API ................ {len(est['baixar']):>4}")
    print(f"  {'':->38} {n:>4}")
    if est["sem_binario"]:
        print(f"\n  registrados SEM o PDF no storage ... {len(est['sem_binario']):>4}  <- --binarios")
        for s in est["sem_binario"][:6]:
            print(f"      {s[:66]}")
    if est["extras"]:
        print(f"\n  no disco, fora da curadoria ....... {len(est['extras']):>4}  (ignorados)")
        for s in est["extras"][:6]:
            print(f"      {est['disco'][s].name[:66]}")
    sobra = est["banco"] - set(est["slug_para_nome"])
    if sobra:
        print(f"\n  no banco sem par na curadoria ...... {len(sobra):>4}")
        for s in sorted(sobra)[:6]:
            print(f"      {s[:66]}")


# ──────────────────────────────── ações ─────────────────────────────────────

def token() -> str:
    r = requests.post(f"{API_BASE}/api/login",
                      json={"username": os.environ["CURATION_API_USER"],
                            "password": os.environ["CURATION_API_PASSWORD"]}, timeout=30)
    r.raise_for_status()
    t = next((v for v in r.json().values() if isinstance(v, str) and v.startswith("eyJ")), None)
    if not t:
        sys.exit("a API respondeu sem token JWT")
    return t


def baixar(est: dict) -> None:
    """Busca na API só o que não existe no disco e grava em PASTA."""
    fila = est["baixar"]
    if not fila:
        print("nada a baixar — todo aprovado já tem arquivo no disco")
        return
    PASTA.mkdir(parents=True, exist_ok=True)
    tok = token()
    ok = erros = 0
    print(f"baixando {len(fila)} de {API_BASE} -> {PASTA}")
    for i, (slug, nome) in enumerate(fila, 1):
        destino = PASTA / nome
        try:
            r = requests.get(f"{API_BASE}/api/documents/{requests.utils.quote(nome)}",
                             headers={"Authorization": f"Bearer {tok}"}, timeout=180)
            r.raise_for_status()
            if not r.content.startswith(b"%PDF"):
                raise ValueError(f"resposta não é PDF ({r.content[:8]!r})")
            # grava ao lado e só então renomeia: um Ctrl-C no meio não deixa
            # um .pdf truncado que a próxima varredura contaria como pronto
            tmp = destino.with_suffix(".pdf.parcial")
            tmp.write_bytes(r.content)
            tmp.replace(destino)
            est["disco"][slug] = destino
            print(f"  [{i:>3}/{len(fila)}] {len(r.content)/1024:>7.0f} KB  {nome[:56]}")
            ok += 1
        except Exception as e:
            print(f"  [{i:>3}/{len(fila)}] ERRO {nome[:46]}: {str(e)[:70]}")
            erros += 1
    print(f"\nbaixados {ok}, erros {erros}")


def registrar_um(slug: str, nome: str, arquivo: Path, artigo: dict) -> str:
    dados = arquivo.read_bytes()
    doc = fitz.open(stream=dados, filetype="pdf")
    n_paginas = doc.page_count
    doc.close()

    sb.storage.from_("pdfs").upload(
        f"{slug}.pdf" if not slug.endswith(".pdf") else slug,
        dados, {"content-type": "application/pdf", "upsert": "true"})

    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    linha = {"pdf_file": slug, "total_pages": n_paginas, "approved": True,
             "approved_at": agora, "extracted": False, "vectorized": False,
             "curation_status": curadoria.texto(artigo.get("status")) or "approved",
             "document_url": nome}
    try:
        pdf_id = sb.table("pdfs").insert(linha).execute().data[0]["id"]
    except Exception as e:
        print(f"      (insert reduzido: {str(e)[:60]})")
        pdf_id = sb.table("pdfs").insert(
            {"pdf_file": slug, "total_pages": n_paginas, "extracted": False}
        ).execute().data[0]["id"]

    meta = {k: curadoria.texto(artigo.get(orig)) for k, orig in curadoria.CAMPOS.items()}
    try:
        meta["year"] = int(curadoria.texto(artigo.get("year"))[:4])
    except ValueError:
        meta["year"] = None
    meta.update({"pdf_id": pdf_id, "raw": artigo,
                 "api_id": curadoria.texto(artigo.get("_id")), "synced_at": agora})
    sb.table("article_metadata").insert(meta).execute()
    try:
        sb.table("audit_log").insert({
            "evento": "pdf_ingerido", "ator": "acervo.py", "alvo": pdf_id,
            "detalhe": {"slug": slug, "paginas_pdf": n_paginas, "arquivo": arquivo.name},
        }).execute()
    except Exception:
        pass
    return pdf_id


def registrar(est: dict) -> None:
    # recalcula: o --baixar acabou de encher o disco
    banco = ler_banco()
    fila = [(s, n) for s, n in est["slug_para_nome"].items()
            if s in est["disco"] and s not in banco]
    if not fila:
        print("nada a registrar — todo PDF do disco já está no banco")
        return
    print(f"registrando {len(fila)} no Supabase")
    ok = erros = 0
    for i, (slug, nome) in enumerate(sorted(fila), 1):
        try:
            pdf_id = registrar_um(slug, nome, est["disco"][slug], est["aprovados"][nome])
            print(f"  [{i:>3}/{len(fila)}] {slug[:56]} -> {pdf_id[:8]}")
            ok += 1
        except Exception as e:
            print(f"  [{i:>3}/{len(fila)}] ERRO {slug[:46]}: {str(e)[:70]}")
            erros += 1
    print(f"\nregistrados {ok}, erros {erros}")


def binarios(est: dict) -> None:
    """Sobe ao bucket o PDF de quem tem registro mas não tem arquivo."""
    fila = [s for s in est["sem_binario"] if s in est["disco"]]
    orfaos = [s for s in est["sem_binario"] if s not in est["disco"]]
    if orfaos:
        print(f"{len(orfaos)} sem arquivo no disco — rode --baixar antes")
    if not fila:
        print("nada a subir")
        return
    for i, s in enumerate(fila, 1):
        d = est["disco"][s].read_bytes()
        sb.storage.from_("pdfs").upload(s, d, {"content-type": "application/pdf",
                                               "upsert": "true"})
        print(f"  [{i:>3}/{len(fila)}] {len(d)/1024:>7.0f} KB  {s[:56]}")


def verificar(est: dict) -> None:
    """Abre cada PDF do disco. Trunca/corrompido conta como ausente."""
    ruins = []
    for slug, p in sorted(est["disco"].items()):
        try:
            d = fitz.open(p)
            n = d.page_count
            d.close()
            if n == 0:
                ruins.append((p.name, "0 páginas"))
        except Exception as e:
            ruins.append((p.name, str(e)[:50]))
    print(f"lidos {len(est['disco'])} PDFs | ilegíveis: {len(ruins)}")
    for nome, por in ruins:
        print(f"   {nome[:60]}  ({por})")


def main() -> None:
    est = situacao()
    imprimir(est)
    arg = set(sys.argv[1:])
    if arg & {"--baixar", "--tudo"}:
        print()
        baixar(est)
    if arg & {"--registrar", "--tudo"}:
        print()
        registrar(est)
    if arg & {"--binarios", "--tudo"}:
        print()
        binarios(est)
    if "--verificar" in arg:
        print()
        verificar(est)
    if not arg:
        print("\n(--baixar, --registrar, --tudo ou --verificar para agir)")


if __name__ == "__main__":
    main()
