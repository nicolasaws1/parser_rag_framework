"""
Manda o resultado de uma extração para o Supabase, ATUALIZANDO o documento.

    python scripts/ingerir_extracao.py                  # $SB100_DIR/export
    python scripts/ingerir_extracao.py /content/export
    python scripts/ingerir_extracao.py --substituir     # regrava o já extraído

A lógica mora em api/extracao.py e é a mesma do upload da aba Extração do site:
o pacote é conferido inteiro antes de gravar, documento já extraído é pulado
(regravar apaga páginas e blocos que podem ter sido curados à mão), e os
recortes de figura são gerados na sequência.

Diferente do antigo `ingest_supabase.py`, que apagava a linha de `pdfs` e
recriava: o cascade levaria `article_metadata` junto, e quem extrai não tem de
onde repor título, autores e DOI.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
load_dotenv(RAIZ / ".env")

from api import extracao  # noqa: E402

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    substituir = "--substituir" in sys.argv
    padrao = Path(os.environ.get("SB100_DIR", "/content")) / "export"
    pasta = Path(args[0]) if args else padrao

    try:
        rel = extracao.processar(sb, pasta, substituir=substituir)
    except ValueError as e:
        sys.exit(f"pacote inválido: {e}")

    print(f"{rel['total']} documento(s) em {pasta}\n")
    for g in rel["gravados"]:
        extra = f", {g['figuras']} figuras" if g.get("figuras") else ""
        print(f"  ok      {g['slug'][:50]:<52} {g['paginas']} pág, {g['blocos']} blocos{extra}")
    for p in rel["pulados"]:
        print(f"  pulado  {p['slug'][:50]:<52} {p['motivo']}")
    for f in rel["falhas"]:
        print(f"  FALHA   {(f.get('slug') or '?')[:50]:<52} {f['motivo']}")

    print(f"\ngravados {len(rel['gravados'])}, pulados {len(rel['pulados'])}, "
          f"falhas {len(rel['falhas'])}")
    if rel["pulados"] and not substituir:
        print("Para regravar os já extraídos: --substituir")
    sys.exit(1 if rel["falhas"] else 0)


if __name__ == "__main__":
    main()
