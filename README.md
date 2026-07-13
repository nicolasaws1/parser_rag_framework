# SB100 — Squad 2 · Vetorização Cientométrica

Pipeline de extração multimodal de PDFs científicos (agronomia/citros) + front de visualização.
O fluxo: **PDF -> extração (Python) -> `layout.json` + `document.md` + imagens -> front + vetor (Qdrant)**.

## Estrutura

```
front/                 código do site (SPA, um único index.html)
  index.html           HTML + CSS + JS tudo junto (sem build, sem dependências)
  data/                dados extraídos que o site consome
    index.json         catálogo dos PDFs
    <slug>/
      layout.json      páginas -> blocos (tipo, bbox, md, id, tabela, caption, fig)
      document.md      o MARKDOWN que vai para o vetor (LLM-friendly)
      pages/pNNN.jpg   imagem de cada página
      figures/         recortes dos gráficos/figuras (p/ busca por imagem)
extractor/
  F04_018_extrator_hibrido.ipynb   pipeline de extração (Colab + GPU)
```

## Como rodar o front

Precisa servir a pasta (as imagens carregam de `data/`):

```
cd front
python -m http.server 8000
# abre http://localhost:8000/index.html
```

Login: **protótipo de front** — hoje aceita qualquer usuário/senha só para gatear a Home.
A autenticação real é responsabilidade do backend (senha com hash: bcrypt/argon2), nunca no front.

## Formato dos dados

- **`layout.json`** é a fonte estruturada por documento. Cada página tem `blocos`, e cada bloco:
  `tipo` (texto|tabela|grafico|foto|formula), `bbox` (0-1, normalizada), `md`, `id`,
  e p/ tabela: `tabela_html`/`tabela_json`; p/ gráfico: `caption` (legenda PT) e `fig` (recorte salvo).
- **`document.md`** é o texto que vai para o vetor. Ciência em **LaTeX** (`^{15}N`, `(NH_{4})_{2}SO_{4}`)
  para não perder informação e ser lido sem ambiguidade pelo LLM. Tabelas em Markdown table.

## O extrator (Python)

`F04_018` roda em Colab com GPU. Stack **100% Python** (não há equivalente Java):
Docling (ordem/tabelas) + Chandra OCR 2 (OCR com bbox) + DocLayout-YOLO (detecção de região) + PyMuPDF (rede de segurança).
Regra de classe: o **YOLO** decide tabela-vs-figura; o **Chandra** dá a subclasse e o conteúdo.
Saída: `SITE_EXPORT_HIBRIDO/` com a mesma estrutura de `front/data/`.

## O que falta para produção (backend / infra)

O front é um protótipo. Para produção é preciso definir (tarefa de banco/infra):
- Onde salvar PDFs, markdowns, imagens de página e `figures/` (filesystem x object storage).
- Índice de estado (aprovado/extraído/vetorizado) — onde vive e como o front lê rápido.
- Segredos no backend (chave Qdrant, API da Squad 01) + login de usuário com hash.
- Se cabe cache (ex.: Redis) para as imagens dado o volume (~1000 PDFs/semana, ~10 usuários simultâneos).

## Features já implementadas no front

- Home lista/grade com thumbnail da 1a página e nº de páginas; filtro por categoria; status (aprovado/extraído/vetorizado).
- Extração: PDF à esquerda (zoom), abas **Legível / Markdown / Metadados / Código**; destaque "imã" (hover bbox <-> texto).
- Legível: tabelas em HTML, fórmulas/reações legíveis, recortes de figura.
- Adicionar bloco (botão direito no Markdown) / excluir bloco (x) / editar por 2x clique (sincroniza Legível<->Markdown).
- Campo de página editável (com limite), Diagnostics, Baixar Markdown/PDF.
