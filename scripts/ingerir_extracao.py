"""
Manda o resultado de uma extração para o Supabase, ATUALIZANDO o documento.

    python scripts/ingerir_extracao.py /content/export

Diferente de `ingest_supabase.py`, que era do tempo em que o banco começava
vazio: aquele apaga a linha de `pdfs` e recria. Hoje os 230 documentos já estão
registrados com metadados vindos da API de curadoria, e o cascade da FK levaria
`article_metadata` junto — a extração no Colab não tem de onde repor esses
metadados, então o documento voltaria sem título, autores nem DOI.

Aqui a linha de `pdfs` é atualizada no lugar, e `article_metadata` não é tocada.
Páginas e blocos antigos são apagados antes de inserir os novos, para
re-extração não duplicar.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

PIPELINE = "hibrido: DocLayout-YOLO + Chandra OCR 2 + Docling + PyMuPDF"


def _limpar(v):
    """O Postgres recusa \u0000 em text e jsonb."""
    if isinstance(v, str):
        return v.replace("\u0000", "")
    if isinstance(v, list):
        return [_limpar(x) for x in v]
    if isinstance(v, dict):
        return {k: _limpar(x) for k, x in v.items()}
    return v


def achar_documento(slug: str) -> str:
    """id do documento a partir do slug do extrator.

    O nome no banco vem do `slug_de` da curadoria, que corta em 60 caracteres e
    as vezes deixa um traco no fim. O `slugify` do extrator faz `.strip('-')` e
    devolve um nome sem ele: cinco dos 230 documentos divergem por esse traco, e
    a busca exata nao acha nenhum deles.
    """
    exato = sb.table("pdfs").select("id").eq("pdf_file", f"{slug}.pdf").execute().data
    if exato:
        return exato[0]["id"]
    # tolera traco sobrando ou faltando no fim, dos dois lados
    cand = (sb.table("pdfs").select("id,pdf_file")
            .ilike("pdf_file", f"{slug}%").execute().data)
    alvo = slug.rstrip("-")
    iguais = [c for c in cand if c["pdf_file"].removesuffix(".pdf").rstrip("-") == alvo]
    if len(iguais) == 1:
        return iguais[0]["id"]
    if len(iguais) > 1:
        raise ValueError(f"{slug}: {len(iguais)} documentos batem, ambíguo")
    raise ValueError(f"{slug}: não existe no banco. Registre antes com acervo.py")


def um(pasta: Path, entrada: dict) -> str:
    slug = entrada["slug"]
    lay = json.loads((pasta / slug / "layout.json").read_text(encoding="utf-8"))
    md = (pasta / slug / "document.md").read_text(encoding="utf-8")
    paginas = lay.get("paginas", [])

    pdf_id = achar_documento(slug)

    agora = datetime.now().isoformat(timespec="seconds")
    sb.table("pdfs").update({
        "markdown": _limpar(md),
        "total_pages": lay.get("n_paginas"),
        "extracted": True, "extracted_at": agora,
        "extraction_time_ms": int(round((lay.get("tempo_s") or 0) * 1000)) or None,
        "pipeline": PIPELINE,
    }).eq("id", pdf_id).execute()

    # re-extração não pode acumular: fora o que havia
    sb.table("page_blocks").delete().eq("pdf_id", pdf_id).execute()
    sb.table("page_images").delete().eq("pdf_id", pdf_id).execute()

    imgs = []
    for pg in paginas:
        local = pasta / slug / pg["img"]
        remoto = f"{slug}/{pg['img']}"
        if local.exists():
            sb.storage.from_("images").upload(
                remoto, local.read_bytes(),
                {"content-type": "image/jpeg", "upsert": "true"})
        imgs.append({"pdf_id": pdf_id, "page_number": pg["n"], "image_file": remoto,
                     "route": pg.get("rota"), "page_type": pg.get("tipo"),
                     "width": pg.get("w"), "height": pg.get("h")})
    for i in range(0, len(imgs), 500):
        sb.table("page_images").insert(imgs[i:i + 500]).execute()

    blocos = [{"pdf_id": pdf_id, "page_number": pg["n"], "block_type": b.get("tipo"),
               "markdown_text": _limpar(b.get("md")), "bbox": b.get("bbox"),
               "layout": _limpar(b)}
              for pg in paginas for b in pg.get("blocos", [])]
    for i in range(0, len(blocos), 500):
        sb.table("page_blocks").insert(blocos[i:i + 500]).execute()

    try:
        sb.table("audit_log").insert({
            "evento": "documento_editado", "ator": "ingerir_extracao.py", "alvo": pdf_id,
            "detalhe": {"paginas": len(imgs), "blocos": len(blocos), "pipeline": PIPELINE},
        }).execute()
    except Exception:
        pass
    return f"{slug}: {len(imgs)} páginas, {len(blocos)} blocos"


def main() -> None:
    padrao = Path(os.environ.get("SB100_DIR", "/content")) / "export"
    pasta = Path(sys.argv[1]) if len(sys.argv) > 1 else padrao
    index = json.loads((pasta / "index.json").read_text(encoding="utf-8"))
    print(f"ingerindo {len(index)} documento(s) de {pasta}\n")
    ok = falhas = 0
    for e in index:
        try:
            print("  " + um(pasta, e))
            ok += 1
        except Exception as err:
            print(f"  ERRO em {e.get('slug')}: {str(err)[:90]}")
            falhas += 1
    print(f"\nok {ok}, falhas {falhas}")
    print("Falta os recortes: python scripts/gerar_figuras.py --aplicar")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
