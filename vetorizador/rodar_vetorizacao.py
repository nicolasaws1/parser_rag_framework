"""
Fatia e vetoriza um pacote baixado do site, e grava direto no Qdrant.

    QDRANT_API_KEY=... python vetorizador/rodar_vetorizacao.py pacote_....zip --enviar
    python vetorizador/rodar_vetorizacao.py pacote_....zip --enviar --colecao sb100_clone
    python vetorizador/rodar_vetorizacao.py pacote_....zip          # só gera o zip

Dois jeitos de devolver o resultado:

    --enviar   grava no Qdrant daqui mesmo (precisa de QDRANT_API_KEY; a URL vem
               do pacote ou de QDRANT_URL). Depois, na aba Vetorização do site,
               confirme: o site confere os pontos e registra quem foi vetorizado.
    sem ele    gera vetores_<pacote>.zip para enviar na aba Vetorização.

O zip é gerado nos dois casos, como cópia de segurança.

A chave nunca vai no pacote nem no repositório: o notebook pede na hora.

O fatiamento é o de api/chunker.py, o mesmo que o site usaria: 1024 caracteres,
20% de sobreposição, tabela e figura inteiras. Os modelos estão em
vetorizador/embeddings.py.

Documento que já tem pontos na coleção (gravados por aqui ou pelos scripts
antigos) é pulado antes de calcular, a menos que se passe --substituir: aí os
pontos antigos dele saem e os novos entram.

Cada documento é gravado num arquivo à parte assim que termina. Se a sessão
cair, rode de novo com o mesmo zip: o que já terminou é pulado.

SB100_EMBEDDER aponta para outro arquivo com uma classe Embedder no mesmo
formato; é como o caminho inteiro é testado numa máquina sem GPU.
"""
import argparse
import array
import base64
import importlib.util
import json
import os
import re
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

DIM = 1024
NOME_DENSO, NOME_ESPARSO = "vetor_denso", "vetor_esparso"
LOTE_UPSERT = 64


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


# ───────────────────────────── Qdrant ───────────────────────────────────────

class Qdrant:
    """Só o necessário, por HTTP: criar índice, contar, apagar e gravar."""

    def __init__(self, url: str, chave: str):
        import requests
        self.url = url.rstrip("/")
        self.s = requests.Session()
        self.s.headers["api-key"] = chave

    def req(self, metodo, caminho, **kw):
        r = self.s.request(metodo, self.url + caminho, timeout=kw.pop("timeout", 120), **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Qdrant {r.status_code}: {r.text[:200]}")
        return r.json()

    def conferir_colecao(self, col: str) -> None:
        try:
            info = self.req("GET", f"/collections/{col}")["result"]
        except RuntimeError as e:
            if "404" in str(e):
                sys.exit(f"a coleção {col} não existe no Qdrant: crie pela aba Vetorização do site")
            if "401" in str(e) or "403" in str(e):
                sys.exit("o Qdrant recusou a chave (QDRANT_API_KEY)")
            raise
        p = info.get("config", {}).get("params", {})
        v = p.get("vectors") or {}
        if not (isinstance(v, dict) and (v.get(NOME_DENSO) or {}).get("size") == DIM
                and NOME_ESPARSO in (p.get("sparse_vectors") or {})):
            sys.exit(f"a coleção {col} não tem {NOME_DENSO} ({DIM}) e {NOME_ESPARSO}: "
                     "estes vetores não cabem nela")
        for campo in ("pdf_id", "file"):        # deixa filtrar e apagar por documento
            try:
                self.req("PUT", f"/collections/{col}/index", params={"wait": "true"},
                         json={"field_name": campo, "field_schema": "keyword"})
            except RuntimeError:
                pass

    @staticmethod
    def filtro_do_documento(d: dict) -> dict:
        """Pontos novos têm pdf_id; os dos scripts antigos só o nome do arquivo
        da curadoria, às vezes com .md no lugar de .pdf."""
        nomes = set()
        for n in (d.get("file"), d.get("pdf_file")):
            if n:
                base = re.sub(r"\.(pdf|md)$", "", n, flags=re.I)
                nomes |= {n, base + ".pdf", base + ".md", base + ".PDF"}
        cond = [{"key": "pdf_id", "match": {"value": d["pdf_id"]}}]
        if nomes:
            cond.append({"key": "file", "match": {"any": sorted(nomes)}})
        return {"should": cond}

    def pontos_do_documento(self, col: str, d: dict) -> int:
        return self.req("POST", f"/collections/{col}/points/count",
                        json={"filter": self.filtro_do_documento(d), "exact": True})["result"]["count"]

    def apagar_documento(self, col: str, d: dict) -> None:
        self.req("POST", f"/collections/{col}/points/delete", params={"wait": "true"},
                 json={"filter": self.filtro_do_documento(d)})

    def gravar(self, col: str, linhas: list[dict]) -> None:
        for i in range(0, len(linhas), LOTE_UPSERT):
            pontos = [{"id": x["id"], "payload": x["payload"],
                       "vector": {NOME_DENSO: list(array.array("f", base64.b64decode(x["denso"]))),
                                  NOME_ESPARSO: x["esparso"]}}
                      for x in linhas[i:i + LOTE_UPSERT]]
            self.req("PUT", f"/collections/{col}/points", params={"wait": "true"},
                     json={"points": pontos}, timeout=300)


# ───────────────────────────── principal ────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pacote", help="o pacote_*.zip baixado na aba Vetorização")
    p.add_argument("--lote", type=int, default=32, help="trechos por chamada ao modelo")
    p.add_argument("--enviar", action="store_true", help="grava direto no Qdrant")
    p.add_argument("--colecao", default="", help="outra coleção no lugar da do pacote")
    p.add_argument("--substituir", action="store_true",
                   help="com --enviar: regrava quem já está na coleção")
    a = p.parse_args()

    with zipfile.ZipFile(a.pacote) as z:
        try:
            man = json.loads(z.read("pacote.json"))
        except KeyError:
            sys.exit("o zip não tem pacote.json: baixe o pacote pela aba Vetorização do site")
        trab = pasta_base() / f"vet_{man['pacote']}"
        trab.mkdir(parents=True, exist_ok=True)
        z.extractall(trab)

    col = a.colecao.strip() or man["colecao"]
    if col != man["colecao"]:
        print(f"coleção trocada: o pacote pedia {man['colecao']}, vai para {col}")
        man["colecao"] = col
    ck = man["chunk"]
    if ck.get("versao") != chunker.VERSAO:
        print(f"aviso: pacote feito com {ck.get('versao')}, este código é {chunker.VERSAO}")
    docs = man["documentos"]
    print(f"pacote {man['pacote']}: {len(docs)} documento(s) para a coleção {col}")
    print(f"fatiamento: {ck['tamanho']} caracteres, sobreposição {ck['sobreposicao']}\n")

    qd = None
    if a.enviar:
        url = os.environ.get("QDRANT_URL") or man.get("qdrant_url") or ""
        chave = os.environ.get("QDRANT_API_KEY") or ""
        if not url or not chave:
            sys.exit("--enviar precisa de QDRANT_API_KEY (e da URL, que vem no pacote ou em QDRANT_URL)")
        qd = Qdrant(url, chave)
        qd.conferir_colecao(col)
        ja = {d["pdf_id"]: qd.pontos_do_documento(col, d) for d in docs}
        tem = [d for d in docs if ja[d["pdf_id"]]]
        if tem and not a.substituir:
            for d in tem:
                print(f"  já está em {col} ({ja[d['pdf_id']]} pontos), pulado: {d['pdf_file'][:60]}")
            docs = [d for d in docs if not ja[d["pdf_id"]]]
            print(f"  (use --substituir para regravar esses)\n")

    # as partes dependem da coleção: o id do ponto é derivado dela
    partes = trab / f"partes_{col}"
    partes.mkdir(exist_ok=True)
    feitos = partes / "_enviados.json"
    enviados = set(json.loads(feitos.read_text())) if feitos.exists() else set()
    pendentes = [d for d in docs if not (partes / f"{d['pdf_id']}.jsonl").exists()]
    if len(pendentes) < len(docs):
        print(f"{len(docs) - len(pendentes)} já calculado(s) numa sessão anterior\n")

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

    if qd:
        print(f"\ngravando em {col}…")
        for d in docs:
            parte = partes / f"{d['pdf_id']}.jsonl"
            if not parte.exists() or d["pdf_id"] in enviados:
                continue
            linhas = [json.loads(x) for x in open(parte, encoding="utf-8")]
            if a.substituir:
                qd.apagar_documento(col, d)
            qd.gravar(col, linhas)
            enviados.add(d["pdf_id"])
            feitos.write_text(json.dumps(sorted(enviados)))
            print(f"  {len(linhas):>4} pontos  {d['pdf_file'][:60]}")

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
    (saida / "pacote.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    zipado = Path(shutil.make_archive(str(pasta_base() / f"vetores_{man['pacote']}"), "zip", saida))
    print(f"\n{n} trechos em {time.time() - t_total:.0f}s")
    if qd:
        print(f"gravados direto em {col}. Falta confirmar: aba Vetorização do site, "
              f"coleção {col}, botão Confirmar.")
        print(f"cópia de segurança: {zipado}")
    else:
        print(f"pronto para subir na aba Vetorização do site:\n   {zipado}  "
              f"({zipado.stat().st_size / 2**20:.1f} MB)")


if __name__ == "__main__":
    main()
