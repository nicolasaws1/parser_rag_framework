"""
Confere o deploy ANTES de tocar no servidor, sem precisar de Docker.

    python scripts/checar_deploy.py

Sem Docker na máquina de desenvolvimento, o primeiro `docker compose up` seria o
primeiro teste. Este script tira a maior parte do risco antes disso:

    1. o compose aponta para arquivos que existem
    2. copia para uma pasta limpa exatamente o que o Dockerfile copia,
       e prova que `api.main` importa só com aquilo
    3. toda variável de ambiente que o código lê está no .env.example
    4. nada de segredo escapa para a imagem ou para o repositório

O que continua só o servidor provando: volumes, rede entre contêineres e o
certificado. Nenhum depende de código nosso.
"""
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

RAIZ = Path(__file__).resolve().parent.parent
falhas: list[str] = []


def diz(bom: bool, nome: str, detalhe: str = "") -> None:
    print(f"  {'ok  ' if bom else 'FALHA'} {nome}{('  — ' + detalhe) if detalhe else ''}")
    if not bom:
        falhas.append(nome)


def titulo(t: str) -> None:
    print(f"\n{'─' * 66}\n{t}\n{'─' * 66}")


def copiados() -> list[str]:
    """Os COPY do Dockerfile, na ordem em que aparecem."""
    alvos = []
    for linha in (RAIZ / "Dockerfile").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*COPY\s+(\S+)\s+\S+", linha)
        if m and not m.group(1).startswith("--"):
            alvos.append(m.group(1))
    return alvos


# ── 1. o compose aponta para o que existe ───────────────────────────────────
def bloco_compose() -> None:
    titulo("1. referências do compose")
    import yaml

    c = yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))
    diz("api" in c["services"] and "caddy" in c["services"], "serviços api e caddy")
    diz("ports" not in c["services"]["api"],
        "api não publica porta (só o caddy alcança)")

    for v in c["services"]["caddy"]["volumes"]:
        if not v.startswith("./"):
            continue
        # resolve ${VAR:-padrao} ANTES de cortar no ':', senão o próprio
        # ':-' da variável é confundido com o separador do volume
        resolvido = re.sub(r"\$\{[A-Za-z_]+:-([^}]+)\}", r"\1", v)
        origem = resolvido.split(":")[0]
        diz((RAIZ / origem).exists(), f"volume {origem}")

    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    diz("USER " in dockerfile, "imagem não roda como root")
    diz("HEALTHCHECK" in dockerfile, "tem HEALTHCHECK")


# ── 2. a aplicação sobe só com o que o Dockerfile copia ─────────────────────
def bloco_imagem() -> None:
    titulo("2. a aplicação importa só com o que vai para a imagem")
    alvos = copiados()
    print(f"  COPY no Dockerfile: {', '.join(alvos)}")
    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / "app"
        destino.mkdir()
        for a in alvos:
            o = RAIZ / a.rstrip("/")
            if not o.exists():
                diz(False, f"COPY {a}", "não existe")
                continue
            if o.is_dir():
                shutil.copytree(o, destino / o.name,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(o, destino / o.name)
        # o .env NÃO entra na imagem: no compose ele vira variável de ambiente
        # (`env_file:`). Aqui se faz o mesmo, senão o import morre com KeyError
        # e o teste mediria a ausência do arquivo, não a da aplicação.
        amb = dict(os.environ)
        for linha in (RAIZ / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.strip().startswith("#"):
                k, _, val = linha.partition("=")
                amb.setdefault(k.strip(), val.strip())
        amb["PYTHONPATH"] = str(destino)
        r = subprocess.run(
            [sys.executable, "-c",
             "import api.main as m; "
             "from pathlib import Path; "
             "assert (Path(m.__file__).resolve().parent.parent/'front'/'index.html').exists(), "
             "'front/index.html faltando na imagem'; "
             "print('rotas:', len([x for x in m.app.routes if hasattr(x,'methods')]))"],
            cwd=destino, capture_output=True, text=True, env=amb,
            encoding="utf-8", errors="replace")
        saida = (r.stdout + r.stderr).strip().splitlines()
        diz(r.returncode == 0, "api.main importa na imagem",
            saida[-1][:70] if saida else "")


# ── 3. .env.example cobre tudo que o código lê ──────────────────────────────
def bloco_env() -> None:
    titulo("3. variáveis de ambiente")
    exemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
    declaradas = {l.split("=")[0].strip().lstrip("# ")
                  for l in exemplo.splitlines() if "=" in l}

    lidas: dict[str, set[str]] = {}
    for py in list((RAIZ / "api").glob("*.py")) + list((RAIZ / "scripts").glob("*.py")):
        arv = ast.parse(py.read_text(encoding="utf-8"))
        for no in ast.walk(arv):
            # os.environ["X"]  e  os.environ.get("X")
            nome = None
            if isinstance(no, ast.Subscript) and isinstance(no.slice, ast.Constant):
                if "environ" in ast.dump(no.value):
                    nome = no.slice.value
            elif isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute) \
                    and no.func.attr == "get" and "environ" in ast.dump(no.func.value) \
                    and no.args and isinstance(no.args[0], ast.Constant):
                nome = no.args[0].value
            if isinstance(nome, str) and nome.isupper():
                lidas.setdefault(nome, set()).add(py.name)

    ignorar = {"PATH", "PYTHONPATH", "TEMP", "FORWARDED_ALLOW_IPS",
               "SURYA_INFERENCE_BACKEND", "TRANSFORMERS_NO_TF"}
    for nome in sorted(lidas):
        if nome in ignorar:
            continue
        diz(nome in declaradas, f"{nome} está no .env.example",
            "lido por " + ", ".join(sorted(lidas[nome])[:2]))


# ── 4. segredo não escapa ───────────────────────────────────────────────────
def bloco_segredo() -> None:
    titulo("4. segredos")
    di = (RAIZ / ".dockerignore").read_text(encoding="utf-8")
    diz(any(l.strip() == ".env" for l in di.splitlines()), ".env fora da imagem")
    diz("deploy/certs" in di, "chave privada fora da imagem")

    r = subprocess.run(["git", "check-ignore", ".env", "deploy/certs/origin.key"],
                       cwd=RAIZ, capture_output=True, text=True)
    diz(".env" in r.stdout and "origin.key" in r.stdout,
        ".env e chave privada fora do repositório")

    # JWT de verdade (eyJ...) ou valor longo de chave. O .env.example fica de
    # fora: os valores dele são texto de exemplo, não segredo.
    r = subprocess.run(["git", "grep", "-lE", r"eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{10,}"],
                       cwd=RAIZ, capture_output=True, text=True)
    sujos = [l for l in r.stdout.split() if l != ".env.example"]
    diz(not sujos, "nenhuma chave nos arquivos rastreados", " ".join(sujos)[:60])


def main() -> None:
    print(f"conferindo o deploy em {RAIZ}")
    bloco_compose()
    bloco_imagem()
    bloco_env()
    bloco_segredo()

    print(f"\n{'═' * 66}")
    if falhas:
        print(f"FALHOU em {len(falhas)}:")
        for f in falhas:
            print(f"   - {f}")
        sys.exit(1)
    print("pronto para subir")
    print("\nfalta, e só o servidor prova: volumes, rede entre contêineres, certificado")


if __name__ == "__main__":
    main()
