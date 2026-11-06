# Extração e vetorização na VM do GCP: passo a passo

Para a máquina com GPU que acabou de ser ligada. Cada comando vai no terminal da
VM, um por vez. Nenhuma senha ou chave fica salva na máquina: tudo é digitado na
hora.

O que a máquina precisa ter (GPU, disco, imagem, rede) e por quê está em
[infra-gcp.md](infra-gcp.md), que é o documento para quem cria a VM.

## Antes de começar (no seu computador)

- **Coleção de teste:** abra <https://sb100extracao.optin.com.br>, vá na aba
  **Vetorização**, clique no **+** ao lado das coleções e crie a coleção
  `teste_gcp`, com uma descrição. Precisa de conta **admin**.
- **Tenha à mão:** o e-mail e a senha do site (conta admin ou curador) e a chave
  do Qdrant.

## 1. Entrar na VM

No console do GCP: **Compute Engine > Instâncias de VM > botão SSH** na linha da
máquina. Abre um terminal no navegador. Pelo terminal do seu computador, com o
`gcloud` instalado:

```bash
gcloud compute ssh NOME_DA_VM --zone ZONA_DA_VM
```

## 2. Conferir a GPU

```bash
nvidia-smi
```

Tem que aparecer uma tabela com a placa (L4 ou A100). Se a máquina perguntar, ao
entrar, se quer instalar o driver NVIDIA, responda `y` e espere terminar.

Se der "command not found", falta o driver. O instalador oficial do Google
resolve; ele reinicia a máquina, então entre de novo e rode o mesmo comando até
dizer que terminou:

```bash
curl -fsSL https://raw.githubusercontent.com/GoogleCloudPlatform/compute-gpu-installation/main/linux/install_gpu_driver.py -o install_gpu_driver.py
```
```bash
sudo python3 install_gpu_driver.py
```

## 3. Conferir o Python

```bash
python3 --version
```

Precisa ser 3.10 ou mais novo (o Chandra exige). Ubuntu 22.04 vem com 3.10.

## 4. Instalar tudo (uma vez, de 15 a 30 minutos)

O [startup.sh](../deploy/gcp/startup.sh) instala os pacotes do sistema, baixa o
código, instala as dependências Python e baixa os 4 modelos (12 GB). Os dois
comandos abaixo o colocam para rodar em segundo plano, então ele continua mesmo
se o SSH cair:

```bash
curl -fsSL https://raw.githubusercontent.com/CCDSB100/squad-2/main/2026/parser_rag_framework/deploy/gcp/startup.sh -o startup.sh
```
```bash
sudo nohup bash startup.sh > /dev/null 2>&1 &
```

Para acompanhar (Ctrl+C sai do acompanhamento, não para a instalação):

```bash
tail -F /var/log/sb100-preparo.log
```

Terminou quando aparecer `== pronto`. Se aparecer `AVISO` ou `ERRO`, guarde o
log inteiro.

> Numa VM criada com o mesmo script no campo **startup-script** (ver
> [infra-gcp.md](infra-gcp.md)), isso já roda sozinho no boot: ao entrar por SSH
> é só esperar o `== pronto`.

## 5. Abrir uma sessão que não cai

```bash
tmux new -s sb100
```

Tudo daqui em diante roda dentro dela. Se o SSH cair, entre de novo e volte com:

```bash
tmux attach -t sb100
```

## 6. Digitar as credenciais uma vez só (opcional)

Sem isso, cada comando pergunta e-mail, senha e chave. Com isso, valem até
fechar a sessão. A senha e a chave não aparecem na tela:

```bash
export SB100_EMAIL=seu.email@exemplo.com
```
```bash
read -s -p "senha do site: " SB100_SENHA; export SB100_SENHA; echo
```
```bash
read -s -p "chave do Qdrant: " QDRANT_API_KEY; export QDRANT_API_KEY; echo
```

## 7. Teste de extração com 2 PDFs

```bash
sb100-extrair --quantos 2
```

Ele baixa 2 PDFs do site, extrai com o Chandra e devolve o resultado ao site
sozinho. A primeira página demora mais (o modelo carrega na GPU). No fim deve
aparecer `2 gravado(s)`. Confira na Home do site: os 2 aparecem como extraídos.

Para escolher os documentos: no site, aba **Extração**, marque os que quiser e
clique em **Copiar comando para a VM**. Cole no terminal; o comando fica assim:

```bash
sb100-extrair --ids <id>,<id>,...
```

## 8. Teste de vetorização com 2 documentos

```bash
sb100-vetorizar --colecao teste_gcp --quantos 2
```

Ele calcula os vetores, grava direto no Qdrant e confirma no site. Confira na aba
**Vetorização**, com a coleção `teste_gcp` escolhida: deve mostrar 2 PDFs
vetorizados.

## 9. Lotes maiores

Se os passos 7 e 8 deram certo, siga em lotes de até 10 PDFs. Cada lote vai para
o site assim que termina:

```bash
sb100-extrair --quantos 10
```

Para testar se fica mais rápido mandando 4 páginas por vez ao Chandra (no fim de
cada PDF aparece uma linha com o uso da GPU, para comparar):

```bash
LOTE_PAGINAS=4 sb100-extrair --quantos 10
```

Depois, para vetorizar o que foi extraído:

```bash
sb100-vetorizar --colecao teste_gcp --quantos 50
```

Outras opções da extração:

| comando | pega |
|---|---|
| `sb100-extrair --quantos 10` | os pedidos em **Solicitar extração** na Home primeiro, depois os menores |
| `sb100-extrair --ids <id>,<id>,...` | exatamente esses (o botão **Copiar comando para a VM** monta) |
| `sb100-extrair --solicitados --quantos 10` | só os pedidos em **Solicitar extração** |
| `sb100-extrair --maiores --quantos 10` | os maiores primeiro |

## Antes de a máquina ser apagada

Deixe o lote que estiver rodando terminar (até aparecer `N gravado(s)`). Só o
lote que estiver no meio se perde; tudo que terminou já está no site e no Qdrant.
Não duplica nada: documento já extraído não entra no lote, e se chegar de novo ao
site é pulado.

Na L4, a extração faz cerca de 200 páginas por hora. Numa janela de 4 horas,
depois da instalação e dos testes, sobram umas 2 horas de extração.

## Se der erro

Mandar:

1. o comando que rodou e a saída inteira do terminal (o erro costuma estar no meio);
2. a saída de `nvidia-smi` e de `df -h`;
3. o arquivo `/var/log/sb100-preparo.log`, se o erro for na instalação.

| sintoma | o que fazer |
|---|---|
| `GPU: CPU` no começo da extração | a VM está sem placa ou sem driver: volte ao passo 2 |
| `sb100-extrair: command not found` | a instalação ainda não terminou, ou falhou: veja o log do passo 4 |
| `login recusado` | e-mail ou senha do site |
| `a coleção ... não existe no Qdrant` | crie a coleção no site antes (ver "Antes de começar") |
| `o Qdrant recusou a chave` | a chave do Qdrant está errada |
| `CUDA out of memory` | rode o lote com `LIMPAR_VRAM=1 CHANDRA_MAX_LADO=1800` na frente do comando |
| `o site perdeu a tarefa`, `não conseguiu gravar` ou `sem resposta do site` | a extração terminou, só o envio falhou: mande de novo com `sb100-reenviar` e o caminho do zip que a mensagem mostra. O que já foi gravado é pulado |
