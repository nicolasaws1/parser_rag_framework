"""
Fatia e vetoriza um pacote baixado do site; devolve o zip para subir de volta.

    python vetorizador/rodar_vetorizacao.py pacote_20261027-1530-ab12cd.zip
    python vetorizador/rodar_vetorizacao.py pacote_....zip --lote 64

Não precisa de .env nem de chave do Qdrant. O caminho inteiro:

    site (aba Vetorização) --pacote.zip--> aqui --vetores_<pacote>.zip--> site --> Qdrant

O fatiamento é o de api/chunker.py, o mesmo que o site usaria: 1024 caracteres,
20% de sobreposição, tabela e figura inteiras. Os modelos estão em
vetorizador/embeddings.py.

Cada documento é gravado num arquivo à parte assim que termina. Se a sessão
cair, rode de novo com o mesmo zip: o que já terminou é pulado.

SB100_EMBEDDER aponta para outro arquivo com uma classe Embedder no mesmo
formato; é como o caminho inteiro é testado numa máquina sem GPU.
"""
import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from api import chunker  # noqa: E402


def pasta_base() -> Path:
    if os.environ.get("SB100_DIR"):
        return Path(os.environ["SB100_DIR"])
    return Path("/content") if Path("/content").is_dir() else Path.cwd() / "trabalho"


def carregar_embedder(lote: int):
    alt = os.environ.get("SB100_EMBEDDER")
    if alt:
        spec = importlib.util.spec_from_file_location("embedder_alt", alt)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.Embedder(lote=lote)
    from vetorizador.embeddings import Embedder
    return Embedder(lote=lote)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pacote", help="o pacote_*.zip baixado na aba Vetorização")
    p.add_argument("--lote", type=int, default=32, help="trechos por chamada ao modelo")
    a = p.parse_args()

    with zipfile.ZipFile(a.pacote) as z:
        try:
            man = json.loads(z.read("pacote.json"))
        except KeyError:
            sys.exit("o zip não tem pacote.json: baixe o pacote pela aba Vetorização do site")
        trab = pasta_base() / f"vet_{man['pacote']}"
        trab.mkdir(parents=True, exist_ok=True)
        z.extractall(trab)

    col = man["colecao"]
    ck = man["chunk"]
    if ck.get("versao") != chunker.VERSAO:
        print(f"aviso: pacote feito com {ck.get('versao')}, este código é {chunker.VERSAO}")
    docs = man["documentos"]
    print(f"pacote {man['pacote']}: {len(docs)} documento(s) para a coleção {col}")
    print(f"fatiamento: {ck['tamanho']} caracteres, sobreposição {ck['sobreposicao']}\n")

    partes = trab / "partes"
    partes.mkdir(exist_ok=True)
    pendentes = [d for d in docs if not (partes / f"{d['pdf_id']}.jsonl").exists()]
    if len(pendentes) < len(docs):
        print(f"{len(docs) - len(pendentes)} já feito(s) numa sessão anterior\n")

    emb = carregar_embedder(a.lote) if pendentes else None
    ingestao = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t_total = time.time()
    for i, d in enumerate(pendentes, 1):
        t0 = time.time()
        paginas = json.loads((trab / "docs" / d["slug"] / "blocos.json").read_text(encoding="utf-8"))
        trechos = chunker.gerar_chunks(paginas, ck["tamanho"], ck["sobreposicao"])
        if not trechos:
            print(f"  [{i}/{len(pendentes)}] {d['pdf_file'][:50]}: sem texto, pulado")
            continue
        textos = [c["texto"] for c in trechos]
        densos = emb.denso(textos)
        esparsos = emb.esparso(textos)
        tmp = partes / f"{d['pdf_id']}.jsonl.parcial"
        with open(tmp, "w", encoding="utf-8") as f:
            for c, dn, es in zip(trechos, densos, esparsos):
                f.write(json.dumps({
                    "id": chunker.id_do_ponto(col, d["pdf_id"], c["indice"]),
                    "pdf_id": d["pdf_id"],
                    "payload": chunker.montar_payload(c, d, col, man["modelos"], ingestao,
                                                      ck["tamanho"], ck["sobreposicao"]),
                    "denso": dn, "esparso": es}, ensure_ascii=False) + "\n")
        tmp.replace(partes / f"{d['pdf_id']}.jsonl")
        tipos = {}
        for c in trechos:
            tipos[c["tipo"]] = tipos.get(c["tipo"], 0) + 1
        print(f"  [{i}/{len(pendentes)}] {d['pdf_file'][:46]:<48} {len(trechos):>4} trechos "
              f"{tipos}  {time.time() - t0:.0f}s")

    saida = trab / "saida"
    shutil.rmtree(saida, ignore_errors=True)
    saida.mkdir()
    n = 0
    with open(saida / "vetores.jsonl", "w", encoding="utf-8") as f:
        for d in docs:
            parte = partes / f"{d['pdf_id']}.jsonl"
            if parte.exists():
                for linha in open(parte, encoding="utf-8"):
                    f.write(linha); n += 1
    shutil.copy(trab / "pacote.json", saida / "pacote.json")
    zipado = Path(shutil.make_archive(str(pasta_base() / f"vetores_{man['pacote']}"), "zip", saida))
    print(f"\n{n} trechos em {time.time() - t_total:.0f}s")
    print(f"pronto para subir na aba Vetorização do site:\n   {zipado}  "
          f"({zipado.stat().st_size / 2**20:.1f} MB)")


if __name__ == "__main__":
    main()
