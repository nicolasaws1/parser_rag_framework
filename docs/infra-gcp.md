# Teste na VM do GCP: o que a máquina precisa

Para quem cria a instância. A VM é descartável: sobe, roda o teste, é destruída.
Nada que precise sobreviver fica nela; o resultado vai para o site e para o Qdrant.

## O que roda na VM

Só a parte de GPU do projeto, pela linha de comando ([sb100.py](../sb100.py)):

| etapa | comando | o que faz |
|---|---|---|
| extração | `python sb100.py extrair` | baixa um lote de PDFs do site, extrai com DocLayout-YOLO + Chandra OCR 2 e devolve o resultado ao site |
| vetorização | `python sb100.py vetorizar` | pega os documentos extraídos, fatia, calcula os embeddings (Qwen3 denso + esparso) e grava no Qdrant |

O site (FastAPI) e o Qdrant **não** vão para a VM. Eles continuam no servidor
`sb100`, atrás do Cloudflare. A VM só conversa com eles por HTTPS.

## Máquina

| | recomendado | observação |
|---|---|---|
| GPU | **1x NVIDIA L4** (24 GB) | é onde o extrator foi medido: pico de ~10,6 GB de VRAM, ~17 s por página. A100 também funciona. T4 (16 GB) não foi testada |
| tipo no GCP | `g2-standard-8` (8 vCPU, 32 GB RAM, 1 L4) | |
| RAM | 32 GB | o Colab onde rodamos tinha 53 GB; 32 GB deve bastar, mas é a primeira vez |
| disco | **100 GB** SSD (mínimo 60 GB) | PyTorch com CUDA ~6 GB, modelo Chandra ~10,6 GB, modelos de embedding ~1,5 GB, mais cache do pip e os lotes |
| sistema | imagem **Deep Learning VM** do Google com PyTorch + CUDA, Ubuntu 22.04 ou mais novo | já vem com driver NVIDIA, CUDA, Python e PyTorch. Marcar a opção de instalar o driver NVIDIA no primeiro boot. O driver precisa aceitar **CUDA 12.8** (versão 570 ou mais nova): o `chandra-ocr` puxa o PyTorch 2.8+, que vem compilado para ela |
| alternativa | Ubuntu 22.04 + driver NVIDIA instalado à mão | dá mais trabalho e é onde mais costuma quebrar |

**Cota de GPU:** projeto novo no GCP costuma ter cota zero de GPU. Antes do dia
do teste, confira em IAM e administrador → Cotas se há `GPUs (all regions)` e
`NVIDIA L4 GPUs` na região escolhida. O pedido de aumento pode levar um ou dois dias.

**VM Spot:** sai mais barata, mas o Google pode desligar no meio. Se cair no meio
da extração, o lote daquela rodada se perde (o site não marca nada como extraído
até receber o resultado, então é só rodar de novo).

## O que instalar

| item | precisa? |
|---|---|
| driver NVIDIA + CUDA | sim (vem na Deep Learning VM) |
| Python 3.10 a 3.12, `pip`, `venv` | sim |
| `git` | sim |
| `libgl1`, `libglib2.0-0` (apt) | sim: o opencv, que vem com o doclayout-yolo, não abre sem eles |
| Docker | **não** para este teste. Os scripts rodam direto no Python. Só entraria se quisermos uma imagem padronizada depois, e aí junto com o NVIDIA Container Toolkit |

Dependências Python: [requirements-gcp.txt](../requirements-gcp.txt), só o que
a extração (Chandra) e a vetorização usam. O `chandra-ocr` pede torch 2.8 ou
mais novo; se a imagem trouxer um PyTorch antigo, o `pip` atualiza.

## Modelos

Quatro, todos públicos no Hugging Face (sem login nem token). O
[baixar_modelos.py](../baixar_modelos.py) baixa só os arquivos que o código
carrega:

| modelo | tamanho | uso |
|---|---|---|
| `datalab-to/chandra-ocr-2` | 10,61 GB | extração: OCR das páginas |
| `juliozhao/DocLayout-YOLO-DocStructBench` (só `doclayout_yolo_docstructbench_imgsz1024.pt`) | 0,04 GB | extração: layout da página |
| `Qwen/Qwen3-Embedding-0.6B` | 1,21 GB | vetorização: vetor denso |
| `opensearch-project/opensearch-neural-sparse-encoding-doc-v2-distill` | 0,27 GB | vetorização: vetor esparso |
| **total** | **12,13 GB** | |

Vão para `~/.cache/huggingface` (ou `HF_HOME`). Se a VM for recriada a partir de
uma imagem de disco, baixar uma vez e guardar na imagem economiza esse tempo em
toda subida.

## Vetorização: o que ela precisa

Roda na mesma VM da extração, depois dela. É bem mais leve.

| | o que é |
|---|---|
| código | [vetorizador/rodar_vetorizacao.py](../vetorizador/rodar_vetorizacao.py), chamado pelo `sb100.py vetorizar`. O fatiamento vem de [api/chunker.py](../api/chunker.py), que só usa biblioteca padrão do Python |
| fatiamento | 1024 caracteres, 20% de sobreposição, tabela e figura inteiras (o mesmo do site) |
| modelos | `Qwen/Qwen3-Embedding-0.6B` (denso, 1024 dimensões) e o esparso da OpenSearch, 1,48 GB juntos. Já estão no `baixar_modelos.py` |
| pacotes Python | `sentence-transformers`, `transformers`, `torch`, `numpy`, `requests`. Já estão no `requirements-gcp.txt` |
| GPU | recomendada, mas pequena serve: os dois modelos rodam em float16 e somam ~1,5 GB. Não medi o pico. Na CPU funciona, só que muito devagar |
| disco | quase nada além dos modelos: o pacote baixado do site é texto, e os vetores saem num zip de cópia |
| rede | HTTPS (443) para o site e para o Qdrant em `https://sb100qdrant.optin.com.br`. Conferido de fora da rede da Fatec: responde, e sem chave devolve 401 |
| chave | `QDRANT_API_KEY`, pedida na hora pelo `sb100.py`. Nunca fica no disco nem no pacote |
| coleção | tem que existir antes no Qdrant, com `vetor_denso` (1024, cosseno) e `vetor_esparso`. Criar pela aba Vetorização do site (conta **admin**), que já cria nesse formato |
| conta do site | **admin** ou **curador** para baixar o pacote e confirmar |

Se a VM não puder falar com o Qdrant (firewall, ou a chave não puder sair do
servidor), existe o modo `--via-site`: a VM só calcula os vetores, e o próprio
site grava no Qdrant. Assim a VM não precisa da chave:
```bash
python sb100.py --site https://sb100extracao.optin.com.br vetorizar --colecao teste_gcp --quantos 2 --via-site
```

Se a sessão cair no meio, rodar de novo com o mesmo comando: documento que já
está na coleção é pulado.

## Rede (só saída, nenhuma porta aberta)

A VM precisa sair por HTTPS (443) para:

| destino | para quê |
|---|---|
| `github.com` | clonar o código |
| `pypi.org`, `files.pythonhosted.org` | dependências Python |
| `huggingface.co` e o CDN dele | baixar os modelos (Chandra, YOLO, Qwen3, esparso) |
| `sb100extracao.optin.com.br` | o site: baixar lote, enviar resultado |
| endereço do Qdrant (vem dentro do pacote de vetorização) | gravar os vetores |

Limite do Cloudflare: upload acima de 100 MB é recusado. Por isso os lotes são
pequenos (5 a 10 PDFs).

## Segredos

Nenhum segredo vai para a imagem, para o script de inicialização ou para o disco.
O `sb100.py` pergunta na hora, sem mostrar na tela:

- e-mail e senha do site (conta de quem está testando, com cargo **admin** ou
  **curador**: conta comum não baixa lote nem envia resultado; criar coleção
  nova pede **admin**)
- chave do Qdrant (`QDRANT_API_KEY`), só na vetorização

## Subida automática (startup script)

A VM é apagada com o disco depois de algumas horas, então o preparo roda sozinho
a cada máquina nova: [deploy/gcp/startup.sh](../deploy/gcp/startup.sh). Ele faz
tudo o que a seção seguinte descreve à mão (pacotes do sistema, código,
`requirements-gcp.txt`, os quatro modelos) e cria os comandos `sb100-extrair` e
`sb100-vetorizar`. Leva de 15 a 30 minutos a partir do boot. Não leva senha nem
chave.

Para quem cria a VM, no campo **startup-script** dos metadados:

```bash
#!/bin/bash
curl -fsSL https://raw.githubusercontent.com/CCDSB100/squad-2/main/2026/parser_rag_framework/deploy/gcp/startup.sh | bash
```

Ou colar o conteúdo inteiro do `startup.sh` no mesmo campo.

Para quem usa, depois de entrar por SSH:

```bash
tail -f /var/log/sb100-preparo.log
```
Espera aparecer `== pronto` (Ctrl+C sai do `tail`). Se aparecer `AVISO` ou
`ERRO`, mandar o log inteiro.

## Preparar a VM à mão (se o script falhar)

```bash
nvidia-smi
```
Tem que listar a L4. Se der "command not found", o driver não foi instalado.

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/CCDSB100/squad-2.git && cd squad-2 && git sparse-checkout set 2026/parser_rag_framework && cd 2026/parser_rag_framework
```
```bash
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
```
O `--system-site-packages` aproveita o PyTorch que a Deep Learning VM já traz.

```bash
sudo apt-get install -y libgl1 libglib2.0-0 tmux
```
```bash
pip install -r requirements-gcp.txt
```
```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```
Tem que imprimir `True` e o nome da placa.

```bash
python baixar_modelos.py
```
Baixa os quatro modelos (12,13 GB). `python baixar_modelos.py --listar` só mostra a lista.

Nesse caminho, no roteiro abaixo troque `sb100-extrair` por
`python sb100.py --site https://sb100extracao.optin.com.br extrair` (e o mesmo
para `vetorizar`).

## Roteiro do teste

Tudo dentro do `tmux`, para o trabalho seguir se o SSH cair
(`tmux attach -t sb100` volta para ele):

```bash
tmux new -s sb100
```

**1. Extração de 2 PDFs pequenos**
```bash
sb100-extrair --quantos 2
```
Pede o e-mail e a senha do site. Os 2 documentos devem aparecer na Home como extraídos.

**2. Vetorização numa coleção de teste** (criar `teste_gcp` antes, na aba Vetorização do site, com conta admin e a descrição)
```bash
sb100-vetorizar --colecao teste_gcp --quantos 2
```
Pede o login do site e a chave do Qdrant. Na aba Vetorização, a coleção
`teste_gcp` deve mostrar os 2 como vetorizados.

**3. Lotes maiores**, se os dois passos acima passarem
```bash
sb100-extrair --quantos 10
```
Para comparar a velocidade, `LOTE_PAGINAS=4 sb100-extrair --quantos 10` manda 4
páginas por vez ao Chandra; no fim de cada documento sai uma linha com o uso da GPU.

## Tempo de máquina

| etapa | estimativa |
|---|---|
| subida automática (pacotes e modelos) | 15 a 30 min |
| extrair 5 PDFs pequenos | 15 a 20 min (medido no Colab, L4) |
| vetorizar 5 documentos | poucos minutos |
| folga para erro e ajuste | 1 h |

Com a VM ligada por **4 horas**, sobram ~2 horas de extração depois do teste:
na L4, ~17 s por página, ~200 páginas por hora. Faltam ~1.800 páginas, então
não dá para extrair tudo numa janela só. Usar lotes de até 10 documentos: cada
lote sobe para o site assim que termina, e só o lote que estiver no meio quando
a VM for apagada se perde.

## Quando der erro

Mandar:

1. o comando que rodou
2. a saída inteira do terminal (o erro costuma estar no meio, não no fim)
3. a saída de `nvidia-smi` e de `df -h`

Erros que já sabemos onde olhar:

| sintoma | causa provável |
|---|---|
| `GPU: CPU` no começo da extração | VM sem placa ou sem driver |
| `ModuleNotFoundError: chandra` ou `doclayout_yolo` | o extrator tenta instalar sozinho e, se o `pip` falhar, segue em frente. Ver o `pip` em `/var/log/sb100-preparo.log` |
| `ImportError: libGL.so.1` | faltou o `apt-get install` do libgl1 |
| `CUDA out of memory` | placa menor que a L4. Rodar com `LIMPAR_VRAM=1` e `CHANDRA_MAX_LADO=1800` antes do comando |
| `login recusado` | e-mail ou senha do site |
| `a coleção ... não existe no Qdrant` | criar a coleção na aba Vetorização antes |
| `o Qdrant recusou a chave` | `QDRANT_API_KEY` errada |
| `a coleção ... não tem vetor_denso (1024) e vetor_esparso` | coleção criada fora do site, em outro formato: usar uma criada pela aba Vetorização |
| `413` ou erro no envio | lote grande demais para o Cloudflare: diminuir `--quantos` |
