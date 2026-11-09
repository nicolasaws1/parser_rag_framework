"""
Liga as figuras já extraídas à legenda do autor, pela posição na página.

    python scripts/associar_legendas.py                  # relata
    python scripts/associar_legendas.py --aplicar        # grava
    python scripts/associar_legendas.py --doc <trecho>   # um documento só

A ingestão já faz isto (api/legendas.py). Este script é para o que entrou antes.
A página com edição manual fica de fora: a correção de quem editou vale mais.

Muda o texto do trecho da figura. Documento já vetorizado precisa ser regravado
na coleção para a busca enxergar a legenda.
"""
import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
load_dotenv(RAIZ / ".env")
from api import legendas  # noqa: E402

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def blocos_das_paginas(pdf_id: str, paginas: list[int]) -> list[dict]:
    fora, off = [], 0
    while True:
        lote = (sb.table("page_blocks").select("id,page_number,block_type,markdown_text,bbox,layout")
                .eq("pdf_id", pdf_id).in_("page_number", paginas).order("id")
                .range(off, off + 999).execute().data)
        fora += lote
        if len(lote) < 1000:
            return fora
        off += 1000


def main() -> None:
    aplicar = "--aplicar" in sys.argv
    alvo = sys.argv[sys.argv.index("--doc") + 1] if "--doc" in sys.argv else None
    nomes = {d["id"]: d["pdf_file"] for d in
             sb.table("pdfs").select("id,pdf_file").eq("extracted", True).execute().data}
    editadas: dict[str, set] = {}
    for e in sb.table("document_edits").select("pdf_id,layout").execute().data:
        editadas[e["pdf_id"]] = {p.get("n") for p in (e.get("layout") or [])
                                 if isinstance(p, dict) and p.get("blocos") is not None}

    por_doc: dict[str, set] = defaultdict(set)
    off = 0
    while True:
        lote = (sb.table("page_blocks").select("pdf_id,page_number").in_("block_type", list(legendas.TIPOS_FIGURA))
                .order("id").range(off, off + 999).execute().data)
        for b in lote:
            por_doc[b["pdf_id"]].add(b["page_number"])
        if len(lote) < 1000:
            break
        off += 1000

    novas = trocadas = tiradas = 0
    for pdf_id, paginas in sorted(por_doc.items(), key=lambda x: nomes.get(x[0], "")):
        nome = nomes.get(pdf_id, pdf_id)
        if alvo and alvo not in nome:
            continue
        por_pagina = defaultdict(list)
        for b in blocos_das_paginas(pdf_id, sorted(paginas - editadas.get(pdf_id, set()))):
            lay = dict(b["layout"] or {})
            lay.update(tipo=b["block_type"], md=b["markdown_text"], _linha=b["id"], _layout=b["layout"] or {})
            lay.setdefault("bbox", b["bbox"])
            por_pagina[b["page_number"]].append(lay)
        for n, blocos in sorted(por_pagina.items()):
            antes = {b["_linha"]: b.get("caption") for b in blocos}
            for f in legendas.aplicar_na_pagina(blocos):
                velha, nova = antes[f["_linha"]], f.get("caption")
                novas += bool(nova and not velha)
                trocadas += bool(nova and velha)
                tiradas += bool(velha and not nova)
                print(f"  {nome[:34]:<36} p{n:<4} {f.get('id', ''):<9} {(nova or '(sem legenda)')[:70]!r}")
                if aplicar:
                    novo = {**f["_layout"], "caption": nova, "md": f["md"]}
                    sb.table("page_blocks").update({"layout": novo, "markdown_text": f["md"]}) \
                        .eq("id", f["_linha"]).execute()

    print(f"\n{'gravadas' if aplicar else 'a gravar'}: {novas} novas, {trocadas} trocadas, {tiradas} retiradas")
    if not aplicar:
        print("(--aplicar para gravar)")


if __name__ == "__main__":
    main()
