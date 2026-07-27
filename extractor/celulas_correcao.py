# ═══════════════════════════════════════════════════════════════════════════
# CÉLULA A — MARKER 2 corrigido
# Causa da falha: no modo padrão em GPU NVIDIA o Marker sobe um servidor vLLM
# via Docker, que não existe no Colab. O modo "fast" roda só os modelos locais.
# ═══════════════════════════════════════════════════════════════════════════
import subprocess, sys, time, json
from pathlib import Path

dest = OUT / "_marker"
dest.mkdir(exist_ok=True)


def _tentar(desc, fn):
    print(f"→ {desc}")
    try:
        return fn()
    except Exception as e:
        print(f"   falhou: {str(e)[:220]}")
        return None


def _via_cli():
    for flag in (["--mode", "fast"], ["--disable_llm"], []):
        p = subprocess.run(["marker_single", str(PDF), "--output_dir", str(dest), *flag],
                           capture_output=True, text=True, timeout=3600)
        if p.returncode == 0:
            return True
        print(f"   flag {flag or '(sem flag)'}: {(p.stderr or p.stdout)[-160:]}")
    raise RuntimeError("nenhuma variante do CLI funcionou")


def _via_api():
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered
    conv = PdfConverter(artifact_dict=create_model_dict(),
                        config={"mode": "fast", "use_llm": False, "disable_multiprocessing": True})
    r = conv(str(PDF))
    txt, _, _ = text_from_rendered(r)
    (dest / "marker_api.md").write_text(txt, encoding="utf-8")
    return True


t0 = time.time()
ok = _tentar("CLI marker_single", _via_cli) or _tentar("API PdfConverter (modo fast)", _via_api)
dt = time.time() - t0

if ok:
    mds = sorted(dest.rglob("*.md"), key=lambda f: f.stat().st_size, reverse=True)
    md_txt = mds[0].read_text(encoding="utf-8") if mds else ""
    salvar("marker2", bool(md_txt), dt, md_txt, None,
           md_txt.count("<table") + md_txt.count("|---"), tem_bbox=True)
else:
    salvar("marker2", False, dt, erro="Marker exige Docker/vLLM no modo padrão; "
                                      "nem CLI nem API em modo fast funcionaram")
limpar_memoria()


# ═══════════════════════════════════════════════════════════════════════════
# CÉLULA B — olmOCR 2 corrigido
# Causa da falha: faltava o binário pdftoppm (pacote poppler-utils).
# Rode a instalação abaixo em uma célula separada, ANTES desta.
#     !apt-get install -y -qq poppler-utils
# ═══════════════════════════════════════════════════════════════════════════
import shutil

if not shutil.which("pdftoppm"):
    print("⚠️  pdftoppm ainda ausente — rode antes:  !apt-get install -y -qq poppler-utils")
else:
    ws = OUT / "_olmocr"
    ws.mkdir(exist_ok=True)
    t0 = time.time()
    p = subprocess.run(
        [sys.executable, "-m", "olmocr.pipeline", str(ws), "--markdown", "--pdfs", str(PDF),
         "--gpu_memory_utilization", "0.80", "--max_model_len", "16384"],
        capture_output=True, text=True, timeout=7200)
    dt = time.time() - t0

    if p.returncode != 0:
        salvar("olmocr2", False, dt, erro=(p.stderr or p.stdout)[-400:])
    else:
        mds = sorted(ws.rglob("*.md"), key=lambda f: f.stat().st_size, reverse=True)
        md_txt = mds[0].read_text(encoding="utf-8") if mds else ""
        if not md_txt:                                   # fallback: saída em JSONL
            partes = []
            for jf in ws.rglob("*.jsonl"):
                for linha in jf.read_text(encoding="utf-8").splitlines():
                    try:
                        partes.append(json.loads(linha).get("text", ""))
                    except Exception:
                        pass
            md_txt = "\n\n".join(partes)
        salvar("olmocr2", bool(md_txt), dt, md_txt, None,
               md_txt.count("<table") + md_txt.count("|---"), tem_bbox=False)
    limpar_memoria()
