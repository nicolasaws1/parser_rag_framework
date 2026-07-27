# ═══════════════════════════════════════════════════════════════════════════
# Marker no Colab sem Docker
#
# O Marker 2 usa o Surya como motor, e o Surya escolhe o backend assim:
#     SURYA_INFERENCE_BACKEND = vllm | llamacpp | (vazio = automático)
#     automático -> vllm se a GPU for NVIDIA, senão llamacpp
#     vllm  -> exige Docker + NVIDIA Container Toolkit  (o Colab NÃO tem Docker)
#     llamacpp -> exige o binário llama-server
#
# Ou seja: em GPU NVIDIA ele vai de vllm sozinho e quebra no Colab.
# ═══════════════════════════════════════════════════════════════════════════

# ───────────────────────────────────────────────────────────────────────────
# OPÇÃO A (recomendada p/ testar no Colab) — Marker 1.x
#
# A linha 1.x roda os modelos direto no PyTorch, sem servidor e sem Docker.
# A API PdfConverter é a mesma, então o notebook não muda.
# Rode e depois "Runtime -> Restart session".
# ───────────────────────────────────────────────────────────────────────────
"""
!pip install -q "marker-pdf<2" "chandra-ocr[hf]" doclayout-yolo PyMuPDF beautifulsoup4 Pillow pandas huggingface_hub
import marker, torch
print("marker:", marker.__version__ if hasattr(marker,'__version__') else '(1.x)')
print("GPU:", torch.cuda.is_available())
"""

# ───────────────────────────────────────────────────────────────────────────
# OPÇÃO B — Marker 2 com backend llama.cpp
#
# Mantém a versão nova, trocando o backend. Baixa o llama-server com CUDA e
# aponta o Surya para ele. Mais peças para dar errado, mas preserva o Marker 2.
# ───────────────────────────────────────────────────────────────────────────
"""
!apt-get install -y -qq curl
!curl -sL https://github.com/ggml-org/llama.cpp/releases/latest/download/llama-b-bin-ubuntu-x64.zip -o /tmp/llama.zip || echo "ajuste a URL para a release atual"
!cd /tmp && unzip -oq llama.zip -d llamacpp && ls llamacpp

import os
os.environ["SURYA_INFERENCE_BACKEND"] = "llamacpp"
os.environ["PATH"] = "/tmp/llamacpp/build/bin:" + os.environ["PATH"]
!which llama-server || echo "llama-server não encontrado — confira o caminho acima"
"""

# ───────────────────────────────────────────────────────────────────────────
# Célula de verificação — rode antes de carregar o Marker
# ───────────────────────────────────────────────────────────────────────────
import os
import shutil
import subprocess

print("backend do Surya :", os.environ.get("SURYA_INFERENCE_BACKEND") or "(automático)")
print("docker disponível:", bool(shutil.which("docker")))
print("llama-server      :", shutil.which("llama-server") or "ausente")

try:
    import marker
    v = getattr(marker, "__version__", "?")
except Exception as e:
    v = f"(não instalado: {e})"
print("marker           :", v)

if not shutil.which("docker") and not shutil.which("llama-server") \
        and not os.environ.get("SURYA_INFERENCE_BACKEND"):
    print("\n⚠️  Sem Docker e sem llama-server: o Marker 2 vai tentar vllm e falhar.")
    print("   Use a OPÇÃO A (marker-pdf<2) ou a OPÇÃO B (llama.cpp).")
