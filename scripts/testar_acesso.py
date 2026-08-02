"""
Exercita o caminho autenticado de ponta a ponta: login, leitura, escrita e cargo.

    python scripts/testar_acesso.py                      # pergunta e-mail e senha
    python scripts/testar_acesso.py --api https://...    # contra outro servidor

A senha é lida por `getpass` — não aparece na tela, não vai para o histórico do
shell e não é guardada em lugar nenhum. Não passe senha por argumento: a linha
de comando fica visível para qualquer processo da máquina.

Complementa `checar_rls.py`, que só olha a porta dos fundos (chave anon). Este
olha a porta da frente: se um `leitor` consegue editar, o `checar_rls` passa e o
sistema está aberto do mesmo jeito.
"""
import argparse
import sys
from getpass import getpass

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK, FALHA = "  ok  ", " FALHA"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8000")
    p.add_argument("--email")
    args = p.parse_args()

    base = args.api.rstrip("/")
    email = args.email or input("e-mail: ").strip()
    senha = getpass("senha (não aparece): ")

    falhas = []

    def checar(nome: str, condicao: bool, detalhe: str = "") -> None:
        print(f"{OK if condicao else FALHA}  {nome}{('  — ' + detalhe) if detalhe else ''}")
        if not condicao:
            falhas.append(nome)

    # ── login ───────────────────────────────────────────────────────────────
    r = requests.post(f"{base}/api/auth/login", json={"email": email, "senha": senha}, timeout=60)
    if r.status_code != 200:
        print(f"{FALHA}  login  — {r.status_code} {r.text[:80]}")
        sys.exit(1)
    tok = r.json()["token"]
    del senha
    h = {"Authorization": f"Bearer {tok}"}
    eu = requests.get(f"{base}/api/auth/eu", headers=h, timeout=60).json()
    cargo = eu.get("cargo") or eu.get("usuario", {}).get("cargo")
    print(f"\nentrou como {email}  cargo={cargo}\n")

    # ── leitura ─────────────────────────────────────────────────────────────
    r = requests.get(f"{base}/api/documents", headers=h, timeout=180)
    docs = r.json() if r.status_code == 200 else []
    checar("lista documentos", r.status_code == 200 and len(docs) > 0, f"{len(docs)} documentos")

    extraidos = [d for d in docs if d.get("extracted")]
    checar("há documento extraído", bool(extraidos), f"{len(extraidos)} extraídos")
    if not extraidos:
        print("\nsem documento extraído — não dá para testar edição")
        sys.exit(1 if falhas else 0)

    alvo = max(extraidos, key=lambda d: d.get("total_pages") or 0)
    d = requests.get(f"{base}/api/document/{alvo['id']}", headers=h, timeout=280)
    checar("abre documento", d.status_code == 200, alvo["pdf_file"][:44])
    paginas = (d.json().get("paginas") or []) if d.status_code == 200 else []
    checar("páginas com imagem assinada",
           bool(paginas) and all(p.get("img_url") for p in paginas[:20]),
           f"{len(paginas)} páginas")

    pg = requests.get(f"{base}/api/document/{alvo['id']}/pagina/1", headers=h, timeout=120)
    checar("abre página 1", pg.status_code == 200,
           f"{len(pg.json().get('blocos', []))} blocos" if pg.status_code == 200 else "")

    # ── escrita: grava e desfaz ─────────────────────────────────────────────
    # Uma página inventada, número 999999: some no descarte e nunca colide com
    # página real. Não sobrescreve nada que exista.
    antes = requests.get(f"{base}/api/document/{alvo['id']}/edicao", headers=h, timeout=60).json()
    ja_editado = antes.get("editado")

    corpo = {"layout": [{"n": 999999, "blocos": [], "teste": True}]}
    w = requests.put(f"{base}/api/document/{alvo['id']}/edicao", headers=h, json=corpo, timeout=120)
    pode_escrever = cargo in ("admin", "curador")
    if pode_escrever:
        checar("salva edição", w.status_code == 200, f"HTTP {w.status_code}")
        depois = requests.get(f"{base}/api/document/{alvo['id']}/edicao",
                              headers=h, timeout=60).json()
        checar("edição fica registrada", bool(depois.get("editado")),
               f"por {depois.get('edited_by')}")
        checar("autoria vem do token, não do corpo",
               (depois.get("edited_by") or "") == email, str(depois.get("edited_by")))
        if not ja_editado:
            requests.delete(f"{base}/api/document/{alvo['id']}/edicao", headers=h, timeout=60)
            sobrou = requests.get(f"{base}/api/document/{alvo['id']}/edicao",
                                  headers=h, timeout=60).json()
            checar("descarta edição", not sobrou.get("editado"))
        else:
            print("  (documento já tinha edição — descarte não testado, para não apagar a sua)")
    else:
        checar("leitor barrado na edição", w.status_code == 403, f"HTTP {w.status_code}")

    # ── limite de taxa ──────────────────────────────────────────────────────
    # 5 por minuto por documento. A 6ª tem de voltar 429.
    codigos = [requests.get(f"{base}/api/document/{alvo['id']}", headers=h, timeout=280).status_code
               for _ in range(6)]
    checar("limite de leitura corta na 6ª", 429 in codigos, f"códigos {codigos}")

    print()
    if falhas:
        print(f"FALHOU em {len(falhas)}: {', '.join(falhas)}")
        sys.exit(1)
    print("tudo passou")


if __name__ == "__main__":
    main()
