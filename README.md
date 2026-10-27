# parser_rag_framework

> Pipeline híbrido de extração de PDFs científicos para **RAG** (Retrieval-Augmented Generation), com API, banco, front de inspeção e curadoria. Desenvolvido para o **SB100 "Agrônomo Virtual" (FAPESP), Squad 02: Ingestão e Vetorização**.

![status](https://img.shields.io/badge/status-em%20produ%C3%A7%C3%A3o%20interna-green)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![api](https://img.shields.io/badge/API-FastAPI-009688)
![db](https://img.shields.io/badge/banco-Supabase-3ECF8E)

Converte artigos e boletins agronômicos (texto, tabelas, figuras, gráficos e fórmulas) em **Markdown estruturado + bounding boxes**, prontos para *chunking* e vetorização. O resultado vai para o Supabase, e o site permite à equipe inspecionar e corrigir cada extração página a página antes de vetorizar.

---

## Sumário

- [Visão geral](#visão-geral)
- [Arquitetura da extração](#arquitetura-da-extração)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Como rodar](#como-rodar)
- [API](#api)
- [Formato dos dados](#formato-dos-dados)
- [Stack](#stack)
- [Documentação](#documentação)
- [Status e próximos passos](#status-e-próximos-passos)

## Visão geral

O fluxo de ponta a ponta tem duas metades, porque a extração precisa de GPU e o site não:

```
 Squad 01 (curadoria) ── PDFs aprovados + metadados
          │
          ▼
 Supabase ◄── scripts/acervo.py, registrar_pdf.py, sync_metadados.py   (registro)
          │
          ▼
 GPU (Colab ou servidor) ── baixar_lote.py → extrator_*.py             (extração)
          │
          ▼
 Supabase ◄── ingerir_extracao.py + gerar_figuras.py                    (ingestão)
          │
          ▼
 API FastAPI + front ── inspeção, edição, auditoria e fila             (servidor sem GPU)
          │
          ▼
 Qdrant ── vetorização (próxima etapa)
```

## Arquitetura da extração

A extração é feita **por página**, com roteamento híbrido:

```
             ┌─ DocLayout-YOLO ── detecta regiões e ROTEIA ("tem tabela?")
             │
 PDF ─ página┤─ SE tem tabela ou é escaneada ─→ Chandra OCR 2 (página inteira: bbox + tipo + conteúdo)
             │
             └─ SENÃO ─────────→ Docling, sem OCR (camada de texto do PDF + ordem de leitura)
                                  + PyMuPDF (rede de segurança: recupera texto que o Docling derruba)

 Figuras/gráficos: YOLO decide que é figura → Chandra lê o recorte
                   (subclasse gráfico/foto + equações em LaTeX)

 Saída por documento:  layout.json  +  document.md (LaTeX)  +  pages/  +  figures/
```

- **YOLO** decide a classe (tabela × figura); **Chandra** dá a subclasse e o conteúdo; **Docling** garante a ordem de leitura; **PyMuPDF** é a rede de segurança.
- O Docling roda com `do_ocr = False`: ele lê a camada de texto do PDF. Todo OCR é feito pelo Chandra.
- `document.md` usa notação **LaTeX** para ciência (`^{15}N`, `(NH_{4})_{2}SO_{4}`), sem perda e sem ambiguidade para o LLM. Tabelas viram Markdown tables.

Há dois modos de extração, com os mesmos formatos de saída:

| | Híbrido (`extrator_hibrido.py`) | Só Chandra (`extrator_chandra.py`) |
|---|---|---|
| Páginas de texto sem tabela | Docling lê a camada de texto | Chandra lê a imagem |
| Demais páginas | Chandra | Chandra |
| PDF com fonte quebrada | herda o problema | contorna |
| Velocidade | mais rápido (16,9 s/página em 213 páginas) | mais lento |
| Risco principal | Docling às vezes derruba parágrafo | alucinação do modelo de visão |

O Boletim 100 foi extraído no modo só Chandra, e essa foi a saída aprovada. A comparação detalhada está em [`extractor/README.md`](extractor/README.md).

## Estrutura do repositório

```
api/                         API FastAPI (servidor sem GPU)
  main.py                    rotas de documentos, edição, auditoria, fila, curadoria e equipe
  auth.py                    autenticação via Supabase Auth (cargos admin, curador, leitor)
  curadoria.py               ponte com a API de curadoria da Squad 01
  extracao.py                lote para a GPU e conferência/gravação do zip de extração devolvido
  chunker.py                 fatiamento (1024 caracteres, 20% de sobreposição, tabela e figura inteiras)
  vetorizacao.py             coleções do Qdrant, pacote para a GPU, conferência/gravação dos vetores
front/
  index.html                 site de inspeção (HTML + CSS + JS num arquivo só, sem build)
extractor/                   extração (precisa de GPU)
  colab_hibrido.ipynb        notebook do Colab, modo híbrido
  colab_chandra.ipynb        notebook do Colab, modo só Chandra
  extrator_hibrido.py        extrator híbrido, para rodar fora do Colab
  extrator_chandra.py        extrator só Chandra, para rodar fora do Colab
  baixar_lote.py             baixa do Supabase um lote de PDFs ainda não extraídos
  hybrid_extractor.ipynb     versão original do extrator (board F04.018), mantida como referência
  testar_notebook.py         simula a execução dos notebooks célula a célula
  README.md                  guia de extração no Colab
vetorizador/                 vetorização (precisa de GPU, ou CPU com AVX)
  vetorizador_lote.ipynb     notebook: recebe o pacote do site, devolve vetores_….zip
  rodar_vetorizacao.py       o mesmo, pela linha de comando
  embeddings.py              Qwen3-Embedding-0.6B (denso) e opensearch neural sparse (esparso)
  README.md                  guia da vetorização
scripts/                     operação do acervo e do banco
  acervo.py                  reconcilia curadoria, banco e storage
  registrar_pdf.py           registra um PDF aprovado ainda não extraído
  sync_metadados.py          sincroniza metadados da curadoria com o Supabase
  ingerir_extracao.py        envia uma extração ao Supabase, atualizando o documento
  gerar_figuras.py           gera os recortes de figura e os liga ao bloco
  reclassificar_figuras.py   corrige figuras marcadas como gráfico que são fotografia
  limpar_orfaos.py           remove imagens do bucket sem referência
  checar_tudo.py             roda todas as conferências e devolve um veredito
  checar_rls.py, checar_deploy.py, testar_acesso.py, diag_supabase.py, inspect_schema.py
  criar_usuario.py           cria usuário no Supabase Auth pela linha de comando
supabase/                    migrações SQL, em ordem (001 a 007, incluindo RLS)
deploy/                      Caddyfiles do HTTPS (certificado interno e de origem)
docs/                        relatório técnico, deploy e notas de pesquisa
Dockerfile, docker-compose.yml
```

## Como rodar

Copie `.env.example` para `.env` e preencha. O `.env` nunca vai para o repositório.

### API e site (local)

```bash
pip install -r requirements.txt
uvicorn api.main:app --reload
# http://localhost:8000
```

### API e site (servidor, HTTPS)

```bash
cp .env.example .env    # e preencha, incluindo WORKER_TOKEN
docker compose up -d --build
```

Sobe dois contêineres: `api` (FastAPI, usuário sem privilégio, com healthcheck) e `caddy` (HTTPS). O passo a passo está em [`docs/deploy.md`](docs/deploy.md).

### Extração (GPU)

No Colab, use `extractor/colab_hibrido.ipynb` ou `extractor/colab_chandra.ipynb` com ambiente de GPU. O guia célula a célula está em [`extractor/README.md`](extractor/README.md).

Em um servidor com GPU:

```bash
pip install -r requirements.txt -r extractor/requirements.txt
python extractor/baixar_lote.py --quantos 10
python extractor/extrator_hibrido.py      # ou extrator_chandra.py
python scripts/ingerir_extracao.py
python scripts/gerar_figuras.py --aplicar
```

Na primeira execução o Chandra OCR 2 baixa cerca de 10,6 GB de pesos.

### Vetorização (GPU)

Pela aba **Vetorização** do site: escolha a coleção, marque os documentos que ainda
não estão nela, baixe o pacote e o notebook `vetorizador_lote.ipynb`, rode na GPU e
envie o `vetores_….zip` de volta. A máquina com GPU não recebe chave do banco nem do
Qdrant. Detalhes em [`vetorizador/README.md`](vetorizador/README.md).

```bash
pip install -r requirements-vetorizador.txt
python vetorizador/rodar_vetorizacao.py pacote_….zip
```

## API

Todas as rotas exigem login, exceto `/api/health` e `/api/health/vivo`. A documentação interativa fica em `/docs` quando a API está no ar.

| Grupo | Rotas principais |
|---|---|
| Documentos | `GET /api/documents`, `GET /api/document/{id}`, `GET /api/document/{id}/pagina/{n}`, `GET /api/document/{id}/pdf` |
| Edição | `GET`, `PUT`, `DELETE /api/document/{id}/edicao` |
| Auditoria | `POST`, `GET /api/log` (o autor vem do token, não do cliente) |
| Extração | `POST /api/document/{id}/extrair`, `GET /api/fila`, `POST /api/worker/heartbeat`, `GET /api/worker` |
| Extração por pacote | `GET /api/extracao/lote`, `GET /api/extracao/notebook.ipynb`, `POST /api/extracao/conferir`, `POST /api/extracao/upload` |
| Vetorização | `GET`, `POST /api/vetorizacao/colecoes`, `GET /api/vetorizacao/documentos`, `POST /api/vetorizacao/pacote`, `GET /api/vetorizacao/notebook.ipynb`, `POST /api/vetorizacao/conferir`, `POST /api/vetorizacao/upload`, `GET /api/vetorizacao/capacidade`, `POST /api/vetorizacao/no-site` |
| Curadoria | `GET /api/curadoria/ultima`, `POST /api/curadoria/sincronizar` |
| Equipe | `POST /api/auth/login`, `GET`, `PATCH /api/auth/eu`, `GET`, `POST /api/equipe`, `PATCH`, `DELETE /api/equipe/{uid}` |

A fila e o heartbeat do worker são protegidos pelo cabeçalho `X-Worker-Token`.

## Formato dos dados

`layout.json` é a fonte estruturada de cada documento. Cada página tem `blocos`, e cada bloco tem:

| Campo | Descrição |
|---|---|
| `tipo` | `texto` \| `tabela` \| `grafico` \| `foto` \| `formula` |
| `bbox` | `[x0,y0,x1,y1]` normalizado (0 a 1) |
| `md` | conteúdo do bloco (vai para o `document.md`) |
| `id` | identificador `pN-bM` |
| `tabela_html` / `tabela_json` | tabela estruturada (quando `tipo=tabela`) |
| `caption` / `fig` | legenda em português e recorte salvo (quando `tipo=grafico`/`foto`) |

No Supabase, `document.md` vai para `pdfs.markdown`, cada bloco vira uma linha de `page_blocks` e as imagens de página vão para o bucket `images`.

## Stack

- **Extração:** Python, com DocLayout-YOLO, Chandra OCR 2, Docling e PyMuPDF (GPU).
- **API:** FastAPI + Uvicorn, em Docker, atrás do Caddy (HTTPS).
- **Banco e storage:** Supabase (Postgres com RLS, Auth e Storage).
- **Front:** HTML + CSS + JavaScript puro, sem framework e sem build.
- **Vetor:** Qdrant (collection `sb100`).

## Documentação

- [`docs/relatorio-tecnico.md`](docs/relatorio-tecnico.md): decisões técnicas da extração e por que foram tomadas.
- [`docs/pesquisa-visao-graficos.md`](docs/pesquisa-visao-graficos.md): levantamento sobre a qualidade de modelos de visão na leitura de gráficos científicos.
- [`docs/deploy.md`](docs/deploy.md): subir o site em HTTPS no servidor.
- [`docs/colab-extracao.md`](docs/colab-extracao.md): extrair um lote no Colab.
- [`docs/servidor-local.md`](docs/servidor-local.md): migrar o Supabase para servidor local.

## Status e próximos passos

Em uso interno pela equipe do SB100, com autenticação real (Supabase Auth), RLS no banco e trilha de auditoria.

- [x] Vetorização no Qdrant pela aba Vetorização (pacote para GPU e upload dos vetores).
- [ ] Vetorizar no próprio servidor: a CPU da VM não expõe AVX; depende de mudar o tipo de CPU da VM.
- [ ] Extrair o restante do acervo aprovado pela curadoria.
- [ ] Validar `supabase/001_base.sql` em um Postgres vazio (o esquema foi reconstruído por introspecção).
- [ ] Avaliar cache de imagens para o volume previsto (~1000 PDFs por semana, ~10 usuários simultâneos).
