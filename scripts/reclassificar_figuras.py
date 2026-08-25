"""
Conserta figuras marcadas como gráfico que são fotografia.

    python scripts/reclassificar_figuras.py            # relata
    python scripts/reclassificar_figuras.py --aplicar  # grava

O classificador do extrator olhava só o texto que o Chandra devolveu, e a regra
`8 dígitos + 15 letras => gráfico` engolia qualquer descrição de figura. Numa
amostra de 102, só 2 estavam como foto quando o correto eram 14: micrografia,
gel de eletroforese, foto de pomar, capa de revista e retrato de autor entravam
todos como gráfico.

Aqui a decisão vem do recorte, não do texto: gráfico e diagrama vivem sobre
fundo branco; fotografia é tom contínuo. O extrator já foi corrigido para
decidir assim na origem; este script arruma o que ficou para trás.

O limiar de 38% foi escolhido olhando as figuras: abaixo dele são fotos, e o
primeiro gráfico de verdade aparece em 40% (um de barras 3D com fundo cinza).
"""
import io
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

LIMIAR = 0.38


def fracao_branca(dados: bytes) -> float:
    im = Image.open(io.BytesIO(dados)).convert("RGB")
    im.thumbnail((260, 260))
    px = list(im.getdata())
    if not px:
        return 1.0
    return sum(1 for r, g, b in px if r > 235 and g > 235 and b > 235) / len(px)


def main() -> None:
    aplicar = "--aplicar" in sys.argv
    bl = (sb.table("page_blocks").select("id,layout,block_type,pdf_id")
          .in_("block_type", ["grafico", "foto"]).execute().data)
    alvos = [(b, (b["layout"] or {}).get("fig")) for b in bl]
    alvos = [(b, c) for b, c in alvos if c]
    print(f"{len(alvos)} figuras com recorte\n")

    urls: dict[str, str] = {}
    for i in range(0, len(alvos), 50):
        lote = [c for _, c in alvos[i:i + 50]]
        for it in sb.storage.from_("images").create_signed_urls(lote, 900):
            urls[(it.get("path") or "").lstrip("/")] = it.get("signedURL") or it.get("signedUrl")

    mudam = []
    for b, cam in alvos:
        u = urls.get(cam.lstrip("/"))
        if not u:
            continue
        try:
            br = fracao_branca(requests.get(u, timeout=60).content)
        except Exception as e:
            print(f"  erro em {cam}: {str(e)[:50]}")
            continue
        certo = "foto" if br < LIMIAR else "grafico"
        if certo != b["block_type"]:
            mudam.append((b, br, certo))

    print(f"a corrigir: {len(mudam)}")
    for b, br, certo in mudam:
        print(f"   {(b['layout'] or {}).get('id'):<10} {b['block_type']:>8} -> {certo:<8} "
              f"({br*100:.0f}% branco)")
        if aplicar:
            sb.table("page_blocks").update({"block_type": certo}).eq("id", b["id"]).execute()

    if aplicar:
        print(f"\n{len(mudam)} corrigidas")
    else:
        print("\n(--aplicar para gravar)")


if __name__ == "__main__":
    main()
