"""
Acha e remove imagens no bucket que nenhuma linha de `page_images` referencia.

    python scripts/limpar_orfaos.py            # só lista (padrão)
    python scripts/limpar_orfaos.py --apagar   # remove

Órfão aparece quando um documento é re-extraído sob outro slug, ou renomeado: as
imagens antigas continuam no bucket sem ninguém apontando para elas. Foi o caso do
Boletim 100, que tinha duas pastas de 511 páginas — 116 MB, 13% do bucket.

A conferência é por caminho exato, não por prefixo: um prefixo pode estar
referenciado e ainda assim ter arquivos soltos dentro dele.
"""
import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
BUCKET = "images"


def referenciados() -> set[str]:
    """Todo image_file de page_images. Pagina: o PostgREST corta em 1000."""
    vistos: set[str] = set()
    passo, off = 1000, 0
    while True:
        lote = sb.table("page_images").select("image_file").range(off, off + passo - 1).execute().data
        if not lote:
            break
        vistos |= {(r["image_file"] or "").lstrip("/") for r in lote if r.get("image_file")}
        if len(lote) < passo:
            break
        off += passo
    return vistos


def no_bucket() -> dict[str, int]:
    """caminho -> bytes. Desce em toda subpasta."""
    fora: dict[str, int] = {}
    pendentes = [""]
    while pendentes:
        pre = pendentes.pop()
        off = 0
        while True:
            lote = sb.storage.from_(BUCKET).list(pre, {"limit": 100, "offset": off})
            if not lote:
                break
            for o in lote:
                caminho = f"{pre}/{o['name']}".lstrip("/")
                meta = o.get("metadata") or {}
                if meta.get("size") is not None:
                    fora[caminho] = meta["size"]
                else:
                    pendentes.append(caminho)
            off += 100
    return fora


def main() -> None:
    print("lendo page_images...")
    refs = referenciados()
    print(f"  {len(refs)} caminhos referenciados")
    print("varrendo o bucket...")
    arquivos = no_bucket()
    print(f"  {len(arquivos)} objetos, {sum(arquivos.values())/1024/1024:.0f} MB\n")

    orfaos = {c: b for c, b in arquivos.items() if c not in refs}
    if not orfaos:
        print("nenhum órfão")
        return

    por_pasta: dict[str, list[int]] = defaultdict(list)
    for c, b in orfaos.items():
        por_pasta[c.split("/")[0]].append(b)
    print(f"órfãos: {len(orfaos)} objetos, {sum(orfaos.values())/1024/1024:.0f} MB")
    for pasta, tamanhos in sorted(por_pasta.items(), key=lambda kv: -sum(kv[1])):
        vivos = sum(1 for c in arquivos if c.split("/")[0] == pasta and c in refs)
        nota = f"  (atenção: {vivos} arquivos VIVOS na mesma pasta)" if vivos else ""
        print(f"   {pasta[:52]:<54} {len(tamanhos):>4} obj  {sum(tamanhos)/1024/1024:>6.1f} MB{nota}")

    if "--apagar" not in sys.argv:
        print("\n(--apagar para remover)")
        return

    alvos = sorted(orfaos)
    print(f"\nremovendo {len(alvos)}...")
    apagados = 0
    for i in range(0, len(alvos), 100):          # a API do Storage aceita lote
        lote = alvos[i:i + 100]
        try:
            sb.storage.from_(BUCKET).remove(lote)
            apagados += len(lote)
            print(f"  {apagados}/{len(alvos)}")
        except Exception as e:
            print(f"  ERRO no lote {i}: {str(e)[:70]}")
    print(f"\nremovidos {apagados}, {sum(orfaos.values())/1024/1024:.0f} MB liberados")


if __name__ == "__main__":
    main()
