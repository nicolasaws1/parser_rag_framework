"""
Roda todas as conferências de uma vez e devolve um veredito.

    python scripts/checar_tudo.py                     # contra 127.0.0.1:8000
    python scripts/checar_tudo.py --api https://...   # contra o servidor

Cobre o que dá para verificar SEM senha de usuário:

    1. guardas de acesso, direto nas funções (sem rede)
    2. endpoints recusando quem não tem token
    3. X-Worker-Token protegendo fila e heartbeat
    4. banco fechado para a chave anon
    5. acervo batendo com a curadoria
    6. bucket sem imagem órfã

O que ele NÃO cobre: o caminho com login de verdade. Para isso é o
`testar_acesso.py`, que pede a senha a quem está rodando.

Sai com código 1 se qualquer bloco falhar.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
load_dotenv(RAIZ / ".env")

falhas: list[str] = []


def titulo(t: str) -> None:
    print(f"\n{'─' * 66}\n{t}\n{'─' * 66}")


def diz(bom: bool, nome: str, detalhe: str = "") -> None:
    print(f"  {'ok  ' if bom else 'FALHA'} {nome}{('  — ' + detalhe) if detalhe else ''}")
    if not bom:
        falhas.append(nome)


# ── 1. guardas de acesso, sem rede ──────────────────────────────────────────
def bloco_guardas() -> None:
    titulo("1. guardas de acesso (direto nas funções)")
    from fastapi import HTTPException

    from api import auth

    class FalsoPedido:
        def __init__(self, cab=None):
            self.headers = cab or {}

    def recusa(fn, *a) -> bool:
        try:
            fn(*a)
            return False
        except HTTPException:
            return True

    tok_real = auth.WORKER_TOKEN
    try:
        auth.WORKER_TOKEN = "token-de-teste-123"
        certo = FalsoPedido({"X-Worker-Token": "token-de-teste-123"})
        errado = FalsoPedido({"X-Worker-Token": "outro"})
        vazio = FalsoPedido()

        diz(not recusa(auth.exigir_worker, certo), "exigir_worker aceita token certo")
        diz(recusa(auth.exigir_worker, errado), "exigir_worker recusa token errado")
        diz(recusa(auth.exigir_worker, vazio), "exigir_worker recusa sem token")

        # a rota da fila: token de worker OU usuário. Sem nenhum dos dois, recusa.
        diz(not recusa(auth.exigir_worker_ou_usuario, certo, None),
            "fila aceita token de worker")
        diz(recusa(auth.exigir_worker_ou_usuario, vazio, None),
            "fila recusa quem não tem token nenhum")
        diz(recusa(auth.exigir_worker_ou_usuario, errado, None),
            "fila recusa token de worker errado")

        auth.WORKER_TOKEN = ""
        diz(not recusa(auth.exigir_worker, vazio),
            "sem WORKER_TOKEN o worker fica aberto (esperado, /api/health avisa)")
    finally:
        auth.WORKER_TOKEN = tok_real


# ── 2. endpoints sem token ──────────────────────────────────────────────────
ABERTOS = {"/api/health"}
ROTAS = [
    ("GET", "/api/documents"), ("GET", "/api/log"), ("GET", "/api/curadoria/ultima"),
    ("GET", "/api/worker"), ("GET", "/api/equipe"), ("GET", "/api/document/x/edicao"),
    ("PUT", "/api/document/x/edicao"), ("DELETE", "/api/document/x/edicao"),
    ("POST", "/api/curadoria/sincronizar"), ("POST", "/api/log"),
    ("POST", "/api/document/x/extrair"), ("GET", "/api/fila"),
    ("POST", "/api/worker/heartbeat"), ("GET", "/api/health"),
]


def bloco_endpoints(base: str, protegido: bool) -> None:
    titulo("2. endpoints recusando quem não tem token")
    for metodo, rota in ROTAS:
        corpo = {"evento": "login"} if metodo in ("POST", "PUT") else None
        try:
            r = requests.request(metodo, base + rota, json=corpo, timeout=60)
        except Exception as e:
            diz(False, f"{metodo} {rota}", str(e)[:50])
            continue
        if rota in ABERTOS:
            diz(r.status_code == 200, f"{metodo} {rota} aberto de propósito", str(r.status_code))
        elif rota in ("/api/fila", "/api/worker/heartbeat") and not protegido:
            diz(r.status_code == 200, f"{metodo} {rota} (WORKER_TOKEN vazio)", str(r.status_code))
        else:
            diz(r.status_code == 401, f"{metodo} {rota}", f"HTTP {r.status_code}")


# ── 3. worker token ─────────────────────────────────────────────────────────
def bloco_worker(base: str, protegido: bool) -> None:
    titulo("3. X-Worker-Token")
    if not protegido:
        print("  WORKER_TOKEN vazio no servidor — fila e heartbeat ABERTOS.")
        print("  Defina WORKER_TOKEN no .env antes de subir. Ver docs/deploy.md.")
        falhas.append("WORKER_TOKEN não definido")
        return
    tok = os.environ.get("WORKER_TOKEN", "")
    if not tok:
        print("  servidor protegido, mas o .env local não tem o token — pulando")
        return
    casos = [
        ("GET", "/api/fila", {"X-Worker-Token": tok}, 200, "fila com token certo"),
        ("GET", "/api/fila", {"X-Worker-Token": "errado"}, 401, "fila com token errado"),
        ("GET", "/api/fila", {}, 401, "fila sem token"),
        ("POST", "/api/worker/heartbeat", {"X-Worker-Token": tok}, 200, "heartbeat com token"),
        ("POST", "/api/worker/heartbeat", {}, 401, "heartbeat sem token"),
    ]
    for metodo, rota, cab, esperado, nome in casos:
        r = requests.request(metodo, base + rota, headers=cab,
                             json={"rodando": False} if metodo == "POST" else None, timeout=60)
        diz(r.status_code == esperado, nome, f"HTTP {r.status_code} (esperado {esperado})")


# ── 4-6. os scripts que já existem ──────────────────────────────────────────
def bloco_script(nome: str, arquivo: str, args: list[str] | None = None) -> None:
    titulo(nome)
    r = subprocess.run([sys.executable, str(RAIZ / "scripts" / arquivo)] + (args or []),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    saida = (r.stdout or "") + (r.stderr or "")
    for linha in saida.strip().splitlines()[-14:]:
        print("  " + linha)
    diz(r.returncode == 0, arquivo, f"código {r.returncode}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8000")
    args = p.parse_args()
    base = args.api.rstrip("/")

    print(f"conferindo {base}")
    try:
        saude = requests.get(base + "/api/health", timeout=30).json()
    except Exception as e:
        print(f"\nservidor fora do ar: {str(e)[:70]}")
        sys.exit(1)
    protegido = bool(saude.get("worker_protegido"))
    print(f"  health: {saude}")

    bloco_guardas()
    bloco_endpoints(base, protegido)
    bloco_worker(base, protegido)
    bloco_script("4. banco fechado para a chave anon", "checar_rls.py")
    bloco_script("5. acervo x curadoria", "acervo.py")
    bloco_script("6. imagens órfãs no bucket", "limpar_orfaos.py")

    print(f"\n{'═' * 66}")
    if falhas:
        print(f"FALHOU em {len(falhas)}:")
        for f in falhas:
            print(f"   - {f}")
        sys.exit(1)
    print("tudo passou")
    print("\nfalta o caminho com login (pede senha): python scripts/testar_acesso.py")


if __name__ == "__main__":
    main()
