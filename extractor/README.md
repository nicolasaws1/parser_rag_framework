# Extrair PDFs no Colab com o Chandra OCR 2

Guia do [`colab_chandra.ipynb`](colab_chandra.ipynb): toda página passa pelo
Chandra, sem Docling.

Para o caminho com Docling nas páginas de texto, use
[`colab_hibrido.ipynb`](colab_hibrido.ipynb). A escolha entre os dois está no
fim deste arquivo.

## Antes de começar

1. **Suba o notebook no Colab.** Arquivo → Fazer upload de notebook, e escolha
   `extractor/colab_chandra.ipynb`. Funciona em qualquer conta Google: o
   `git clone` puxa de um repositório público.
2. **Escolha GPU.** Ambiente de execução → Alterar tipo de ambiente → GPU.
   Sem isso a célula 1 avisa e nada mais funciona.
3. **Tenha o `.env` à mão.** Está em `SB100/squad-2/.env`. A célula 1 vai pedir
   esse arquivo; nenhuma chave é digitada, e ele fica só na sessão.

## As sete células

| | o que faz | quanto leva |
|---|---|---|
| 1 | clona o repositório, instala e recebe o `.env` | ~1 min |
| 2 | mostra a fila; não baixa nada | segundos |
| 3 | baixa `QUANTOS` PDFs ainda não extraídos | segundos |
| 4 | **extrai** | ~17 s por página |
| 5 | manda ao Supabase e gera os recortes de figura | ~1 min |
| 6 | confere o acervo | segundos |
| 7 | baixa um zip da corrida (opcional) | — |

Na primeira execução da célula 4 o Colab baixa o modelo do Chandra, que tem
**10,6 GB**. São uns 45 s de download mais 46 s de carga. Nas execuções
seguintes da mesma sessão isso não se repete.

## Os controles da célula 4

```python
CHANDRA_MAX_LADO = 2200
LOTE_PAGINAS = 1
```

**`CHANDRA_MAX_LADO`** é o lado maior da imagem entregue ao modelo. Era 1800
fixo, o que encolhia uma A4 renderizada a 230 DPI e comia justamente o detalhe
fino: rótulo de eixo, expoente, índice de tabela. O custo cresce com a área, então
dobrar o lado quadruplica o tempo da página.

**`LOTE_PAGINAS`** é quantas páginas vão numa chamada. Medição real de
26/08/2026, numa L4:

```
GPU 51% media, 100% pico | VRAM 9.7 de 22.0 GB
```

Metade da placa ociosa e 12 GB de VRAM sobrando. **Vale testar `LOTE_PAGINAS = 4`**
e comparar o tempo com o mesmo documento. Se piorar ou estourar memória, volte
para 1.

## Ritmo

Medido em 213 páginas do pipeline híbrido: **16,9 s por página**. O modo
só-Chandra tende a ser mais lento, porque roda o modelo de visão em toda página
em vez de 79% delas.

Comece com `QUANTOS = 2` ou `3` e confira o resultado no site antes de subir para
10. Descobrir um problema de formato com 3 documentos é bem melhor que com 10.

## Se a sessão cair

Nada do que já subiu se perde. A seleção é por `extracted = false`, então reabra
o notebook, rode a célula 1 e a 3 traz só o que falta.

O que se perde é a extração **em andamento** no momento da queda, e o download do
modelo, que precisa acontecer de novo.

## Erros conhecidos

**`getcwd: cannot access parent directories`** — o `%cd` de uma execução anterior
deixou o shell numa pasta que a célula 1 apagou. Ambiente de execução →
Reiniciar sessão.

**`o .env enviado nao tem SUPABASE_URL`** — arquivo errado. O certo é o da pasta
`squad-2`, não o de outro projeto.

**`não existe no banco. Registre antes com acervo.py`** — o documento não está
registrado. Rode `python scripts/acervo.py` na sua máquina para ver o que
diverge da curadoria.

**`sem página no bucket, pulando`**, no `gerar_figuras.py` — o documento está
marcado como extraído mas não tem imagens de página. Sobra de alguma corrida
interrompida; re-extrair resolve.

## O que acontece com o resultado

O Colab não guarda nada: a pasta `/content` morre com a sessão. O que importa vai
para o Supabase na célula 5:

| o extrator gera | vai para |
|---|---|
| `document.md` | coluna `pdfs.markdown` |
| `layout.json` | uma linha por bloco em `page_blocks` |
| `pages/*.jpg` | bucket `images`, em `<slug>/pages/` |
| `figures/*.jpg` | **não sobe**; os recortes são gerados do PDF pelo `gerar_figuras.py` |

Por isso o resultado aparece no site sem você copiar arquivo nenhum, tanto no
local quanto no servidor: os dois leem o mesmo banco.

**Não troque o `ingerir_extracao.py` pelo `ingest_supabase.py`.** O segundo é de
quando o banco começava vazio: ele apaga a linha de `pdfs` e recria, e o cascade
da chave estrangeira levaria junto os metadados vindos da curadoria, que o Colab
não tem de onde repor.

## Chandra ou híbrido

|  | `colab_chandra.ipynb` | `colab_hibrido.ipynb` |
|---|---|---|
| páginas de texto sem tabela | Chandra lê a imagem | Docling lê a camada de texto |
| demais páginas | Chandra | Chandra |
| instala | YOLO + Chandra | YOLO + Chandra + Docling |
| PDF com fonte quebrada | contorna | herda o problema |
| velocidade | mais lento | mais rápido |
| risco | alucinação do modelo de visão | Docling às vezes derruba parágrafo |

O Boletim 100 rodou inteiro no modo só-Chandra, e foi essa a saída aprovada.

Rodar o **mesmo documento nos dois** e comparar é o experimento que responde a
tarefa de investigação de extratores do projeto.

## Fora do Colab

Os mesmos scripts rodam num servidor com GPU. A única diferença é a pasta de
trabalho, que no Colab é `/content`:

```bash
export SB100_DIR=/dados/sb100
git clone https://github.com/nicolasaws1/parser_rag_framework.git
cd parser_rag_framework && cp /caminho/do/.env .env
pip install -r requirements.txt
python extractor/baixar_lote.py --quantos 10
python extractor/extrator_chandra.py
python scripts/ingerir_extracao.py
python scripts/gerar_figuras.py --aplicar
```

A célula que pede o `.env` é a única coisa que não existe fora do Colab; lá o
arquivo vai por `scp`.
