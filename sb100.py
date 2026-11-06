"""
Extração e vetorização pela linha de comando, num servidor com GPU que só tem
terminal. Faz o mesmo que as abas Extração e Vetorização do site, de ponta a ponta:

    python sb100.py extrair --quantos 10
    python sb100.py extrair --ids <id>,<id>     (os marcados na aba Extração)
    python sb100.py vetorizar --colecao sb100 --quantos 50
    python sb100.py reenviar trabalho/extracao_<lote>.zip

extrair     baixa um lote de PDFs ainda não extraídos (com os metadados), roda o
            extrator e envia o resultado de volta ao site
vetorizar   pega os documentos extraídos que ainda não estão na coleção, fatia,
            calcula os embeddings, grava direto no Qdrant e confirma no site
reenviar    manda de novo um resultado de extração que não chegou a gravar
            (o que já está no site é pulado)

O endereço do site vem de --site ou de SB100_SITE. O e-mail, a senha e a chave
do Qdrant são perguntados na hora (sem aparecer na tela), ou lidos de
SB100_EMAIL, SB100_SENHA e QDRANT_API_KEY. Nada disso é gravado em arquivo.

Funciona igual no bash e no cmd do Windows: só precisa de Python e das
dependências do passo que for rodar (extractor/requirements.txt para extrair,
requirements-vetorizador.txt para vetorizar).
"""
import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

RAIZ = Path(__file__).resolve().parent


class Site:
    """Conversa com a API do site. O login vence em cerca de uma hora, e uma
    extração pode passar disso: num 401, entra de novo e repete o pedido."""

    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.email = os.environ.get("SB100_EMAIL") or input("e-mail do site: ").strip()
        self._senha = os.environ.get("SB100_SENHA") or getpass.getpass("senha do site: ")
        self.token = None
        self.entrar()

    def entrar(self) -> None:
        r = requests.post(self.url + "/api/auth/login", timeout=60,
                          json={"email": self.email, "senha": self._senha})
        if r.status_code != 200:
            sys.exit(f"login recusado: {_detalhe(r)}")
        self.token = r.json()["token"]

    def pedir(self, metodo: str, caminho: str, **kw) -> requests.Response:
        kw.setdefault("timeout", 600)
        for tentativa in (1, 2):
            r = requests.request(metodo, self.url + caminho,
                                 headers={"Authorization": f"Bearer {self.token}"}, **kw)
            if r.status_code == 401 and tentativa == 1:
                self.entrar()
                continue
            return r

    def baixar(self, metodo: str, caminho: str, pasta: Path, **kw) -> Path:
        r = self.pedir(metodo, caminho, **kw)
        if r.status_code != 200:
            sys.exit(f"{caminho}: {_detalhe(r)}")
        nome = re.search(r'filename="([^"]+)"', r.headers.get("Content-Disposition", ""))
        arq = pasta / (nome.group(1) if nome else "download.zip")
        arq.write_bytes(r.content)
        return arq

    def enviar(self, caminho: str, arquivo: Path, **campos) -> dict:
        tamanho = arquivo.stat().st_size / 2**20
        if tamanho > 95:
            print(f"aviso: {arquivo.name} tem {tamanho:.0f} MB; se o site passa pelo Cloudflare "
                  "gratuito, acima de 100 MB é recusado. Use lotes menores se der erro 413.")
        # bytes, e não o arquivo aberto: se o login vencer e o pedido for
        # repetido, um arquivo já lido iria vazio na segunda vez
        r = self.pedir("POST", caminho, data=campos,
                       files={"arquivo": (arquivo.name, arquivo.read_bytes(), "application/zip")})
        if r.status_code != 200:
            sys.exit(f"{caminho}: {_detalhe(r)}")
        return r.json()


def _detalhe(r: requests.Response) -> str:
    try:
        return f"{r.status_code} {r.json().get('detail')}"
    except Exception:
        return f"{r.status_code} {r.text[:200]}"


def _rodar(script: str, *args, env=None) -> None:
    r = subprocess.run([sys.executable, str(RAIZ / script), *map(str, args)], env=env)
    if r.returncode != 0:
        sys.exit(f"{script} saiu com código {r.returncode}")


def _resumo(rel: dict, chave: str = "arquivo") -> None:
    for g in rel.get("gravados", []) + rel.get("confirmados", []):
        print(f"  ok        {g.get(chave) or g.get('slug') or g.get('pdf_id')}")
    for g in rel.get("pulados", []) + rel.get("falhas", []) + rel.get("recusados", []):
        print(f"  {g.get('situacao', 'recusado'):<9} {g.get(chave) or g.get('slug') or g.get('pdf_id')}"
              f": {g.get('motivo', '')}")


_REENVIAR = ("Mandar de novo é seguro (o que já entrou é pulado):\n"
             "   python sb100.py reenviar {0}\n"
             "   (na VM do GCP: sb100-reenviar {0})")


def gravar_extracao(site: Site, arquivo: Path, substituir: bool) -> dict:
    """Envia o resultado e espera o site gravar.

    Gravar 10 documentos leva minutos (cada página vira imagem no bucket), e o
    Cloudflare corta em 100 s um pedido sem resposta: o terminal dava erro 524
    mesmo com o site gravando tudo. Com acompanhar=true o site responde na hora
    com o id de uma tarefa e grava em segundo plano; aqui só se pergunta como vai.
    """
    r = site.enviar("/api/extracao/upload", arquivo,
                    substituir=str(substituir).lower(), acompanhar="true")
    if "tarefa" not in r:
        return r              # site anterior a este modo: o relatório já veio
    tid, visto, falhas = r["tarefa"], None, 0
    while True:
        time.sleep(3)
        try:
            resp = site.pedir("GET", f"/api/extracao/tarefa/{tid}", timeout=60)
        except requests.RequestException as e:
            resp = None
            erro = str(e)
        if resp is not None and resp.status_code == 200:
            falhas = 0
            t = resp.json()
            if t["estado"] == "concluído":
                return t["resultado"]
            if t["estado"] == "falhou":
                sys.exit(f"o site não conseguiu gravar: {t.get('erro')}\n{_REENVIAR.format(arquivo)}")
            if t.get("documentos"):
                linha = (f"  gravando {t['documento']} de {t['documentos']}: "
                         f"{str(t.get('arquivo'))[:48]} ({t.get('etapa')})")
                if linha != visto:
                    print(linha)
                    visto = linha
            continue
        if resp is not None and resp.status_code == 404:
            sys.exit(f"o site perdeu a tarefa (reiniciou?).\n{_REENVIAR.format(arquivo)}")
        falhas += 1
        if falhas >= 20:
            sys.exit(f"sem resposta do site há um minuto ({_detalhe(resp) if resp is not None else erro})."
                     f"\n{_REENVIAR.format(arquivo)}")


def reenviar(site: Site, a, pasta: Path) -> None:
    arquivo = Path(a.arquivo).resolve()
    if not arquivo.is_file():
        sys.exit(f"não achei {arquivo}")
    print(f"enviando {arquivo.name} ao site…")
    rel = gravar_extracao(site, arquivo, a.substituir)
    print(f"{len(rel['gravados'])} gravado(s), {len(rel['pulados'])} pulado(s), "
          f"{len(rel['falhas'])} com problema")
    _resumo(rel, "slug")


def extrair(site: Site, a, pasta: Path) -> None:
    ids = [x.strip() for x in (a.ids or "").split(",") if x.strip()]
    params = {"quantos": len(ids) if ids else a.quantos, "menores": str(not a.maiores).lower(),
              "solicitados": str(a.solicitados).lower()}
    if ids:
        params["ids"] = ",".join(ids)
    lote = site.baixar("GET", "/api/extracao/lote", pasta, params=params)
    print(f"lote baixado: {lote}\n")
    _rodar("extractor/rodar_lote.py", lote, *(["--hibrido"] if a.hibrido else []))
    resultado = pasta / lote.name.replace("lote_", "extracao_", 1)
    if not resultado.exists():
        sys.exit(f"não achei {resultado}")
    print(f"\nenviando {resultado.name} ao site…")
    rel = gravar_extracao(site, resultado, a.substituir)
    print(f"{len(rel['gravados'])} gravado(s), {len(rel['pulados'])} pulado(s), "
          f"{len(rel['falhas'])} com problema")
    _resumo(rel, "slug")


def vetorizar(site: Site, a, pasta: Path) -> None:
    r = site.pedir("GET", "/api/vetorizacao/documentos", params={"colecao": a.colecao})
    if r.status_code != 200:
        sys.exit(f"documentos: {_detalhe(r)}")
    docs = {d["pdf_id"]: d for d in r.json()}
    faltam = [i for i, d in docs.items() if not d["vetorizado"]]
    pedidos = [x.strip() for x in (a.ids or "").split(",") if x.strip()]
    if pedidos:
        for i in pedidos:
            if i not in docs:
                print(f"  ignorado  {i}: não está extraído (ou o id está errado)")
            elif docs[i]["vetorizado"]:
                print(f"  ignorado  {i}: já está em {a.colecao}")
        faltam = [i for i in pedidos if i in docs and not docs[i]["vetorizado"]]
        if not faltam:
            sys.exit("nenhum dos documentos pedidos falta nesta coleção")
    elif not faltam:
        print(f"todos os documentos extraídos já estão em {a.colecao}")
        return
    ids = faltam if pedidos else faltam[:a.quantos]
    print(f"{len(faltam)} documento(s) fora de {a.colecao}; vetorizando {len(ids)}")
    pacote = site.baixar("POST", "/api/vetorizacao/pacote", pasta,
                         json={"colecao": a.colecao, "ids": ids})
    print(f"pacote baixado: {pacote}\n")

    if a.via_site:
        # a máquina não alcança o Qdrant: o site grava a partir do zip
        _rodar("vetorizador/rodar_vetorizacao.py", pacote, "--lote", a.lote)
        vetores = pasta / pacote.name.replace("pacote_", "vetores_", 1)
        rel = site.enviar("/api/vetorizacao/upload", vetores, substituir="false")
        print(f"\n{len(rel['gravados'])} gravado(s) em {rel['colecao']}")
        _resumo(rel)
        return

    env = dict(os.environ)
    env["QDRANT_API_KEY"] = env.get("QDRANT_API_KEY") or getpass.getpass("chave do Qdrant: ")
    _rodar("vetorizador/rodar_vetorizacao.py", pacote, "--enviar", "--lote", a.lote, env=env)
    print("\nconfirmando no site…")
    r = site.pedir("POST", "/api/vetorizacao/confirmar", json={"colecao": a.colecao, "ids": ids})
    if r.status_code != 200:
        sys.exit(f"confirmar: {_detalhe(r)}")
    rel = r.json()
    print(f"{len(rel['confirmados'])} confirmado(s) em {rel['colecao']}")
    _resumo(rel)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    p.add_argument("--site", default=os.environ.get("SB100_SITE"),
                   help="endereço do site (ou SB100_SITE)")
    p.add_argument("--pasta", default=os.environ.get("SB100_DIR") or "trabalho",
                   help="onde ficam lotes, pacotes e resultados (padrão: ./trabalho)")
    sub = p.add_subparsers(dest="passo", required=True)

    e = sub.add_parser("extrair", help="lote -> extrator -> site")
    e.add_argument("--quantos", type=int, default=5, help="documentos no lote (até 50)")
    e.add_argument("--ids", help="só estes documentos, separados por vírgula (o botão "
                                 "'Copiar comando para a VM' da aba Extração monta a lista)")
    e.add_argument("--solicitados", action="store_true",
                   help="só os pedidos em 'Solicitar extração' na Home")
    e.add_argument("--maiores", action="store_true", help="começa pelos maiores, não pelos menores")
    e.add_argument("--hibrido", action="store_true", help="Docling nas páginas de texto")
    e.add_argument("--substituir", action="store_true", help="regrava documento já extraído")

    v = sub.add_parser("vetorizar", help="pacote -> embeddings -> Qdrant -> confirmar")
    v.add_argument("--colecao", required=True, help="coleção do Qdrant (ex.: sb100)")
    v.add_argument("--quantos", type=int, default=50, help="documentos no pacote (até 200)")
    v.add_argument("--ids", help="só estes documentos, separados por vírgula (os mesmos ids do sb100-extrair)")
    v.add_argument("--lote", type=int, default=32, help="trechos por chamada ao modelo")
    v.add_argument("--via-site", action="store_true",
                   help="sem acesso ao Qdrant daqui: manda o zip para o site gravar")
    r = sub.add_parser("reenviar", help="manda de novo um extracao_….zip que não gravou")
    r.add_argument("arquivo", help="o extracao_….zip (fica na pasta de trabalho)")
    r.add_argument("--substituir", action="store_true", help="regrava documento já extraído")
    a = p.parse_args()

    if not a.site:
        sys.exit("diga o endereço do site: --site https://... ou SB100_SITE")
    pasta = Path(a.pasta).resolve()
    pasta.mkdir(parents=True, exist_ok=True)
    os.environ["SB100_DIR"] = str(pasta)     # os dois scripts trabalham na mesma pasta
    site = Site(a.site)
    {"extrair": extrair, "vetorizar": vetorizar, "reenviar": reenviar}[a.passo](site, a, pasta)


if __name__ == "__main__":
    main()
