"""
Baixa de uma vez os modelos que a extração e a vetorização usam, para a primeira
rodada não parar no meio esperando download. Só os arquivos que o código carrega:

    python baixar_modelos.py            baixa os quatro (~12 GB)
    python baixar_modelos.py --listar   só mostra o que seria baixado e o tamanho

Os arquivos vão para a cache do Hugging Face (~/.cache/huggingface, ou HF_HOME),
que é onde os scripts procuram. Rodar de novo não baixa outra vez.
"""
import argparse
import sys

from huggingface_hub import HfApi, hf_hub_download, snapshot_download

from vetorizador.embeddings import MODELO_DENSO, MODELO_ESPARSO

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

# (repo, arquivo único ou None para o repositório, padrões a ignorar, quem usa)
# Chandra e YOLO repetem os nomes de extractor/extrator_chandra.py: aquele
# script carrega os modelos ao ser importado, então não dá para ler de lá.
MODELOS = [
    ("datalab-to/chandra-ocr-2", None, None, "extração: OCR das páginas"),
    ("juliozhao/DocLayout-YOLO-DocStructBench", "doclayout_yolo_docstructbench_imgsz1024.pt",
     None, "extração: layout da página"),
    (MODELO_DENSO, None, None, "vetorização: vetor denso"),
    # o repositório traz os pesos duas vezes (.bin e .safetensors) e uma pasta
    # do lado da consulta; o código só carrega o .safetensors da raiz
    (MODELO_ESPARSO, None, ["*.bin", "query_0_SparseStaticEmbedding/*"], "vetorização: vetor esparso"),
]


def _casa(nome: str, padroes) -> bool:
    from fnmatch import fnmatch
    return any(fnmatch(nome, p) for p in padroes or [])


def tamanho(repo: str, arquivo, ignorar) -> int:
    info = HfApi().model_info(repo, files_metadata=True)
    return sum(s.size or 0 for s in info.siblings
               if (arquivo is None or s.rfilename == arquivo) and not _casa(s.rfilename, ignorar))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--listar", action="store_true", help="só mostra os modelos e o tamanho")
    a = p.parse_args()

    total = 0
    for repo, arquivo, ignorar, uso in MODELOS:
        gb = tamanho(repo, arquivo, ignorar) / 1e9
        total += gb
        print(f"{gb:6.2f} GB  {repo}  ({uso})")
        if a.listar:
            continue
        if arquivo:
            hf_hub_download(repo_id=repo, filename=arquivo)
        else:
            snapshot_download(repo_id=repo, ignore_patterns=ignorar)
    print(f"{total:6.2f} GB  total")
    if not a.listar:
        print("\nmodelos na cache; a extração e a vetorização não baixam mais nada")


if __name__ == "__main__":
    main()
