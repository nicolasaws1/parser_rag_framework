# ═══════════════════════════════════════════════════════════════════════════
# OPÇÃO A — rápida: sobe o transformers depois do MinerU
#
# O mineru[core] rebaixa o transformers; o Chandra OCR 2 (qwen3_5) precisa de
# uma versão nova. Rode em UMA célula, depois "Runtime -> Restart session" e
# siga a partir da célula de config (NÃO rode o install de novo).
#
# Risco: o MinerU pode quebrar com o transformers novo. Se isso acontecer,
# use a Opção B abaixo, que isola de vez.
# ═══════════════════════════════════════════════════════════════════════════
"""
!pip install -q "mineru[core]"
!pip install -q "chandra-ocr[hf]" doclayout-yolo PyMuPDF beautifulsoup4 Pillow pandas huggingface_hub
!pip install -q --upgrade transformers
import transformers; print("transformers:", transformers.__version__)
"""

# ═══════════════════════════════════════════════════════════════════════════
# OPÇÃO B — definitiva: MinerU num ambiente virtual separado
#
# O MinerU já é chamado por linha de comando, então basta instalá-lo num venv
# próprio e apontar para o executável de lá. Os dois deixam de disputar a mesma
# versão de transformers — o conflito some.
#
# Célula 1 (instalação, roda uma vez; leva alguns minutos):
# ═══════════════════════════════════════════════════════════════════════════
"""
!python -m venv /content/venv_mineru
!/content/venv_mineru/bin/pip install -q --upgrade pip
!/content/venv_mineru/bin/pip install -q "mineru[core]"
!/content/venv_mineru/bin/mineru --version
"""

# ═══════════════════════════════════════════════════════════════════════════
# Célula 2 — substitui a função rodar_mineru (usa o venv isolado)
# ═══════════════════════════════════════════════════════════════════════════
import json
import subprocess
import tempfile
from pathlib import Path

MINERU_BIN = "/content/venv_mineru/bin/mineru"   # None = usa o 'mineru' do ambiente principal
MINERU_TIMEOUT = 3600


def rodar_mineru(pdf_path):
    """Executa o MinerU e devolve {n_pagina: [blocos]} com bbox normalizada 0-1.
    Roda num venv separado para não disputar a versão de transformers com o Chandra."""
    exe = MINERU_BIN if (MINERU_BIN and Path(MINERU_BIN).exists()) else "mineru"
    saida = Path(tempfile.mkdtemp())
    p = subprocess.run([exe, "-p", str(pdf_path), "-o", str(saida)],
                       capture_output=True, text=True, timeout=MINERU_TIMEOUT)
    if p.returncode != 0:
        raise RuntimeError("MinerU falhou: " + (p.stderr or p.stdout)[-400:])

    mids = list(saida.rglob("*middle*.json"))
    if not mids:
        raise RuntimeError("MinerU não gerou o middle.json (bboxes) em " + str(saida))
    dados = json.loads(mids[0].read_text(encoding="utf-8"))

    por_pagina = {}
    for pg in dados.get("pdf_info", []):
        pno = pg.get("page_idx", 0) + 1
        W, H = (pg.get("page_size") or [1, 1])[:2]
        W, H = (W or 1), (H or 1)
        blocos = []
        for b in pg.get("para_blocks", []) or pg.get("preproc_blocks", []):
            tipo = _MAP_MINERU.get(b.get("type"), "texto")
            if tipo == "figura":
                continue                                   # figura fica com YOLO + Chandra
            texto = _txt_do_bloco(b)
            if tipo == "tabela":
                html = ""
                for sub in (b.get("blocks") or [b]):
                    for ln in (sub.get("lines") or []):
                        for sp in (ln.get("spans") or []):
                            if sp.get("html"):
                                html = sp["html"]
                if html:
                    bb = b.get("bbox") or [0, 0, 0, 0]
                    blocos.append(bloco_tabela(html, [bb[0]/W, bb[1]/H, bb[2]/W, bb[3]/H]))
                    continue
            if not texto:
                continue
            bb = b.get("bbox") or [0, 0, 0, 0]
            blocos.append({"tipo": tipo,
                           "bbox": [round(bb[0]/W, 4), round(bb[1]/H, 4),
                                    round(bb[2]/W, 4), round(bb[3]/H, 4)],
                           "md": limpar_sup_sub(texto)})
        por_pagina[pno] = blocos
    return por_pagina


print("✅ rodar_mineru agora usa o venv isolado:", MINERU_BIN)
