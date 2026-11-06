#!/bin/bash
# Subida automática da VM de GPU (GCP) para extrair e vetorizar o acervo SB100.
#
# Vai no campo "startup-script" da VM. Faz sozinho o que docs/infra-gcp.md
# descreve em "Preparar a VM": pacotes do sistema, código, dependências Python
# (requirements-gcp.txt) e os quatro modelos (baixar_modelos.py, ~12 GB). Leva
# de 15 a 30 minutos a partir do boot; o andamento fica em
# /var/log/sb100-preparo.log. Num boot seguinte, sai logo (marca em
# /opt/sb100/.pronto).
#
# Não leva senha nem chave: o e-mail e a senha do site e a chave do Qdrant são
# digitados na hora por quem usa a máquina (o sb100.py pergunta).
#
# Pede a imagem Deep Learning VM (driver NVIDIA e CUDA prontos), Python 3.10+.

set -euo pipefail
exec > >(tee -a /var/log/sb100-preparo.log) 2>&1
echo "== preparo SB100 $(date -Is)"

RAIZ=/opt/sb100
MARCA=$RAIZ/.pronto
if [ -f "$MARCA" ]; then
    echo "já preparado em $(cat "$MARCA"); nada a fazer"
    exit 0
fi

if nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader; then
    :
else
    echo "AVISO: nvidia-smi falhou: VM sem GPU ou sem driver. Sem isso a extração não roda."
fi

# libgl1/libglib2.0-0: o opencv do doclayout-yolo não abre sem eles;
# tmux: o trabalho continua se o SSH cair
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git tmux python3-venv python3-pip libgl1 libglib2.0-0

PY=$(command -v python3)
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "ERRO: chandra-ocr pede Python 3.10 ou mais novo; esta imagem tem $("$PY" --version)"
    exit 1
fi

# só a pasta do site, do repositório público da organização
mkdir -p "$RAIZ"
cd "$RAIZ"
rm -rf squad-2
git clone -q --depth 1 --filter=blob:none --sparse https://github.com/CCDSB100/squad-2.git squad-2
git -C squad-2 sparse-checkout set 2026/parser_rag_framework
ln -sfn "$RAIZ/squad-2/2026/parser_rag_framework" "$RAIZ/codigo"

# --system-site-packages aproveita o PyTorch da imagem; se ele for anterior
# ao 2.8 que o chandra-ocr pede, o pip atualiza dentro do venv
"$PY" -m venv --system-site-packages "$RAIZ/venv"
"$RAIZ/venv/bin/pip" install -q -U pip
"$RAIZ/venv/bin/pip" install -q -r "$RAIZ/codigo/requirements-gcp.txt"
"$RAIZ/venv/bin/python" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"

# os modelos vão para a cache agora, e não no meio da primeira extração
export HF_HOME=$RAIZ/hf
(cd "$RAIZ/codigo" && "$RAIZ/venv/bin/python" baixar_modelos.py)

# comandos para quem entra por SSH: executáveis (e não alias), para valer
# dentro do tmux e com variável na frente, como LOTE_PAGINAS=4 sb100-extrair
for passo in extrair vetorizar; do
    {
        echo '#!/bin/bash'
        echo "export HF_HOME=\${HF_HOME:-$RAIZ/hf}"
        echo "export SB100_SITE=\${SB100_SITE:-https://sb100extracao.optin.com.br}"
        echo "export SB100_DIR=\${SB100_DIR:-\$HOME/trabalho}"
        echo "exec $RAIZ/venv/bin/python $RAIZ/codigo/sb100.py $passo \"\$@\""
    } > "/usr/local/bin/sb100-$passo"
    chmod 755 "/usr/local/bin/sb100-$passo"
done
# quem entra por SSH não é root: precisa escrever na cache dos modelos
chmod -R a+rwX "$RAIZ"

date -Is > "$MARCA"
echo "== pronto $(date -Is). Entre por SSH e rode: sb100-extrair --quantos 2"
