"""
Baixa do Supabase um lote de PDFs que ainda não foram extraídos. Roda no Colab.

    python extractor/baixar_lote.py --quantos 10
    python extractor/baixar_lote.py --quantos 10 --menores   # começa pelos curtos

Grava em $SB100_DIR/pdfs (padrao /content, do Colab) com o MESMO nome que o documento tem no banco, porque o
extrator deriva o slug do nome do arquivo: se divergirem, a ingestão depois não
acha a linha para atualizar.

Prioriza o que está na fila (o botão "Analisar" do site marca
`extraction_requested_at`); depois, o resto, do menor para o maior.
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

DESTINO = Path(os.environ.get("PDFS_DIR",
                              Path(os.environ.get("SB100_DIR", "/content")) / "pdfs"))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--quantos", type=int, default=10)
    p.add_argument("--menores", action="store_true", help="do menor para o maior")
    p.add_argument("--doc", help="um documento específico, por pedaço do nome")
    a = p.parse_args()

    campos = "id,pdf_file,total_pages"
    try:
        pend = (sb.table("pdfs").select(campos + ",extraction_requested_at")
                .eq("extracted", False).execute().data)
    except Exception:
        pend = sb.table("pdfs").select(campos).eq("extracted", False).execute().data

    if a.doc:
        pend = [d for d in pend if a.doc.lower() in d["pdf_file"].lower()]

    # quem foi pedido no site vem primeiro; depois, os curtos, que dão retorno
    # rápido e cabem numa sessão de Colab sem risco de perder tudo no meio
    pend.sort(key=lambda d: (not d.get("extraction_requested_at"),
                             d.get("total_pages") or 10**6))
    if not a.menores and not a.doc:
        pend.sort(key=lambda d: not d.get("extraction_requested_at"))

    if a.quantos <= 0:                     # relatório, para olhar antes de baixar
        paginas = sum(d.get("total_pages") or 0 for d in pend)
        na_fila = sum(1 for d in pend if d.get("extraction_requested_at"))
        print(f"faltam extrair: {len(pend)} documentos, {paginas} páginas")
        print(f"   pedidos pelo site: {na_fila}")
        print(f"   a ~17 s/página, o acervo todo dá {paginas*17/3600:.1f} h de GPU")
        print()
        for d in pend[:8]:
            marca = "*" if d.get("extraction_requested_at") else " "
            print(f"   {marca} {(d.get('total_pages') or 0):>4} pág  {d['pdf_file'][:52]}")
        if len(pend) > 8:
            print(f"     ... e outros {len(pend)-8}")
        return

    lote = pend[: a.quantos]
    DESTINO.mkdir(parents=True, exist_ok=True)
    print(f"pendentes: {len(pend)} | baixando {len(lote)} para {DESTINO}\n")

    paginas = 0
    for i, d in enumerate(lote, 1):
        alvo = DESTINO / d["pdf_file"]
        if alvo.exists():
            print(f"  [{i:>2}] já está aqui  {d['pdf_file'][:52]}")
            continue
        dados = sb.storage.from_("pdfs").download(d["pdf_file"])
        alvo.write_bytes(dados)
        paginas += d.get("total_pages") or 0
        print(f"  [{i:>2}] {len(dados)/1024:>7.0f} KB  {(d.get('total_pages') or 0):>4} pág  "
              f"{d['pdf_file'][:46]}")

    print(f"\n{len(lote)} PDFs, {paginas} páginas")
    print(f"a ~17 s/página, estimo {paginas*17/60:.0f} min de GPU")


if __name__ == "__main__":
    main()
