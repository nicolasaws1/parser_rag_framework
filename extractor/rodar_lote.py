"""
Extrai um lote baixado do site e devolve o zip para subir de volta.

    python extractor/rodar_lote.py lote_20261023-1530-ab12cd.zip
    python extractor/rodar_lote.py lote_....zip --hibrido      # com Docling

Não precisa de .env nem de chave do banco. O caminho inteiro é:

    site (aba Extração) --baixa lote.zip--> aqui --gera extracao_<lote>.zip--> site

O lote traz `pdfs/`, `metadados_api.json` e `lote.json`. Este script extrai os
PDFs e devolve o resultado junto com o mesmo `lote.json`, que tem o id de cada
documento no banco: é por ele que o upload casa o resultado com o documento, e
não pelo nome do arquivo, que o extrator reescreve.

Cada lote extrai na sua própria pasta. Se a sessão cair, rode de novo com o
mesmo zip: o extrator pula o que já terminou.

Roda igual no Colab e num servidor com GPU. A pasta de trabalho é $SB100_DIR,
que no Colab é /content.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

AQUI = Path(__file__).resolve().parent


def pasta_base() -> Path:
    if os.environ.get("SB100_DIR"):
        return Path(os.environ["SB100_DIR"])
    return Path("/content") if Path("/content").is_dir() else Path.cwd() / "trabalho"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("lote", help="o lote_*.zip baixado na aba Extração")
    p.add_argument("--hibrido", action="store_true", help="Docling nas páginas de texto")
    a = p.parse_args()

    origem = Path(a.lote)
    if not origem.exists():
        sys.exit(f"não achei {origem}")
    with zipfile.ZipFile(origem) as z:
        try:
            man = json.loads(z.read("lote.json"))
        except KeyError:
            sys.exit("o zip não tem lote.json: baixe o lote pela aba Extração do site")
        lote = man["lote"]
        trab = pasta_base() / f"lote_{lote}"
        trab.mkdir(parents=True, exist_ok=True)
        z.extractall(trab)

    docs = man.get("documentos", [])
    pags = sum(d.get("paginas") or 0 for d in docs)
    print(f"lote {lote}: {len(docs)} documento(s), {pags} páginas, criado por {man.get('criado_por')}")
    print(f"pasta de trabalho: {trab}\n")

    # SB100_EXTRATOR troca o extrator: serve para testar o caminho inteiro sem GPU,
    # e para quem quiser comparar outro extrator no mesmo formato de saida
    extrator = Path(os.environ.get("SB100_EXTRATOR") or
                    AQUI / ("extrator_hibrido.py" if a.hibrido else "extrator_chandra.py"))
    amb = dict(os.environ, SB100_DIR=str(trab))
    r = subprocess.run([sys.executable, str(extrator)], env=amb)
    if r.returncode != 0:
        print(f"\no extrator saiu com código {r.returncode}; empacoto o que terminou")

    export = trab / "export"
    if not (export / "index.json").exists():
        sys.exit("nada foi extraído: sem export/index.json")
    shutil.copy(trab / "lote.json", export / "lote.json")

    feitos = {e["slug"] for e in json.loads((export / "index.json").read_text(encoding="utf-8"))}
    faltam = [d["arquivo"] for d in docs if d["slug"] not in feitos]

    saida = pasta_base() / f"extracao_{lote}"
    zipado = Path(shutil.make_archive(str(saida), "zip", export))
    print(f"\n{len(feitos)} de {len(docs)} extraído(s)")
    for f in faltam:
        print(f"   faltou: {f}")
    print(f"\npronto para subir na aba Extração do site:\n   {zipado}  "
          f"({zipado.stat().st_size / 2**20:.1f} MB)")


if __name__ == "__main__":
    main()
