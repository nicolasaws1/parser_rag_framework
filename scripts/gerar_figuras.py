"""
Gera os recortes de cada figura e sobe ao bucket, ligando-os ao bloco.

    python scripts/gerar_figuras.py                 # relata
    python scripts/gerar_figuras.py --aplicar       # gera e sobe
    python scripts/gerar_figuras.py --doc <slug>    # um documento só
    python scripts/gerar_figuras.py --refazer       # inclusive os que já têm

Rode depois de cada ingestão: pula o que já tem recorte, então custa uma consulta
por documento e nada mais.

O recorte NÃO vem da pasta local da extração. Vem do PDF do bucket, renderizado
em alta, cortado pela bbox que está no próprio `page_blocks`. Duas razões:

1. **Não pode descasar.** As pastas locais são de corridas antigas e a numeração
   dos blocos mudou entre elas: no Bernardi, o disco tem `p2-b15` onde o banco
   espera `p2-b6`. Subir por nome de arquivo colaria o recorte de uma figura no
   bloco de outra, e nada denunciaria.
2. **Resolução.** As páginas guardadas têm ~1113 px de largura, o que dá ~519 px
   num gráfico típico: pouco para ler rótulo de eixo. Renderizando do PDF dá para
   escolher a escala.

Serve para o que já foi extraído e para o que vier depois, sem depender de a
pasta da extração ainda existir na máquina de alguém.
"""
import io
import os
import sys
from pathlib import Path

import fitz
from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

ESCALA = 2.4       # ~2600 px de largura na página A4; recorte típico passa de 1200 px
MARGEM = 0.012     # fração da página, para não cortar rótulo de eixo nem legenda colada
TIPOS = ("grafico", "foto")


def prefixo_do_documento(pdf_id: str) -> str | None:
    """A pasta que as páginas já usam no bucket. As figuras vão ao lado delas,
    e não pelo pdf_file: o Boletim foi renomeado e os dois divergem."""
    r = (sb.table("page_images").select("image_file")
         .eq("pdf_id", pdf_id).limit(1).execute().data)
    if not r or not r[0].get("image_file"):
        return None
    return r[0]["image_file"].split("/")[0]


def recortar(pagina: fitz.Page, bbox: list[float]) -> bytes:
    x0, y0, x1, y1 = bbox
    r = pagina.rect
    caixa = fitz.Rect(
        max(r.x0, r.x0 + (x0 - MARGEM) * r.width),
        max(r.y0, r.y0 + (y0 - MARGEM) * r.height),
        min(r.x1, r.x0 + (x1 + MARGEM) * r.width),
        min(r.y1, r.y0 + (y1 + MARGEM) * r.height),
    )
    pix = pagina.get_pixmap(matrix=fitz.Matrix(ESCALA, ESCALA), clip=caixa)
    buf = io.BytesIO()
    pix.pil_save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def um_documento(doc: dict, aplicar: bool) -> tuple[int, int]:
    pdf_file = doc["pdf_file"]
    prefixo = prefixo_do_documento(doc["id"])
    if not prefixo:
        print(f"  {pdf_file[:46]:<48} sem página no bucket, pulando")
        return 0, 0

    blocos = (sb.table("page_blocks").select("id,page_number,bbox,layout")
              .eq("pdf_id", doc["id"]).in_("block_type", list(TIPOS)).execute().data)
    blocos = [b for b in blocos if b.get("bbox") and len(b["bbox"]) == 4]
    if "--refazer" not in sys.argv:
        # idempotente de propósito: assim dá para rodar depois de cada ingestão
        # sem refazer o acervo inteiro
        blocos = [b for b in blocos
                  if "/figuras/" not in ((b["layout"] or {}).get("fig") or "")]
    if not blocos:
        return 0, 0
    if not aplicar:
        print(f"  {pdf_file[:46]:<48} {len(blocos):>3} figuras")
        return len(blocos), 0

    pdf = fitz.open(stream=sb.storage.from_("pdfs").download(pdf_file), filetype="pdf")
    feitos = erros = 0
    for b in blocos:
        try:
            n = b["page_number"]
            if not (1 <= n <= pdf.page_count):
                erros += 1
                continue
            lay = b["layout"] or {}
            nome = (lay.get("id") or f"p{n}-{b['id'][:6]}") + ".jpg"
            destino = f"{prefixo}/figuras/{nome}"
            dados = recortar(pdf[n - 1], b["bbox"])
            sb.storage.from_("images").upload(
                destino, dados, {"content-type": "image/jpeg", "upsert": "true"})
            sb.table("page_blocks").update({"layout": {**lay, "fig": destino}}) \
                .eq("id", b["id"]).execute()
            feitos += 1
        except Exception as e:
            print(f"      erro em {b.get('page_number')}: {str(e)[:60]}")
            erros += 1
    pdf.close()
    print(f"  {pdf_file[:46]:<48} {feitos:>3} figuras, {erros} erros")
    return feitos, erros


def main() -> None:
    aplicar = "--aplicar" in sys.argv
    alvo = None
    if "--doc" in sys.argv:
        alvo = sys.argv[sys.argv.index("--doc") + 1]

    docs = sb.table("pdfs").select("id,pdf_file").eq("extracted", True).execute().data
    if alvo:
        docs = [d for d in docs if alvo in d["pdf_file"]]
    docs.sort(key=lambda d: d["pdf_file"])
    print(f"{len(docs)} documentos extraídos"
          f"{'' if aplicar else '  (modo relatório)'}\n")

    tot = err = 0
    for d in docs:
        f, e = um_documento(d, aplicar)
        tot += f; err += e
    print(f"\n{'geradas' if aplicar else 'a gerar'}: {tot} figuras" + (f", {err} erros" if err else ""))
    if not aplicar:
        print("\n(--aplicar para gerar e subir)")


if __name__ == "__main__":
    main()
