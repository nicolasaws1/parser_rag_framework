# parser_rag_framework

> Pipeline híbrido de extração de PDFs científicos para **RAG** (Retrieval-Augmented Generation), com front de visualização e QA. Desenvolvido para o **SB100 — Squad 2** (vetorização cientométrica).

![status](https://img.shields.io/badge/status-prot%C3%B3tipo-orange)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![front](https://img.shields.io/badge/front-HTML%2FCSS%2FJS-yellow)

Converte artigos acadêmicos (texto, tabelas, figuras, gráficos e fórmulas) em **Markdown estruturado + bounding boxes**, prontos para *chunking* e vetorização (Qdrant). O front permite inspecionar cada extração página a página antes de vetorizar.

---

## Sumário

- [Arquitetura](#arquitetura)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Como rodar](#como-rodar)
- [Formato dos dados](#formato-dos-dados)
- [Stack](#stack)
- [Status e roadmap](#status-e-roadmap)

## Arquitetura

Extração **por página**, roteada de forma híbrida:

```
             ┌─ DocLayout-YOLO ── detecta regiões e ROTEIA ("tem tabela?")
             │
 PDF ─ página┤─ SE tem tabela ─→ Chandra OCR 2 (página inteira: bbox + tipo + conteúdo)
             │
             └─ SENÃO ─────────→ Docling (ordem de leitura + tabelas)
                                  + PyMuPDF (rede de segurança: recupera texto dropado)

 Figuras/gráficos: YOLO decide que é figura → Chandra descreve o recorte
                   (subclasse gráfico/foto + captura equações em LaTeX)

 Saída por doc:  layout.json  +  document.md (LaTeX, LLM-friendly)  +  pages/  +  figures/
```

- **YOLO** manda na classe (tabela × figura); **Chandra** dá a subclasse e o conteúdo; **Docling** garante a ordem; **PyMuPDF** é a rede de segurança.
- `document.md` usa notação **LaTeX** para ciência (`^{15}N`, `(NH_{4})_{2}SO_{4}`) — *lossless* e sem ambiguidade para o LLM. Tabelas viram Markdown tables.

## Estrutura do repositório

```
front/                       site de visualização (SPA, sem build)
  index.html                 HTML + CSS + JS num arquivo só
  data/                      dados extraídos que o site consome
    index.json               catálogo dos PDFs
    <slug>/
      layout.json            páginas → blocos (tipo, bbox, md, tabela, caption, fig)
      document.md            o Markdown que vai para o vetor (LaTeX)
      pages/pNNN.jpg          imagem de cada página
      figures/                recortes de gráficos/figuras (busca por imagem)
extractor/
  hybrid_extractor.ipynb     pipeline de extração (Colab + GPU) — origem: board F04.018
  requirements.txt           dependências do pipeline
```

## Como rodar

### Front (local, segundos)

Precisa **servir** a pasta — as imagens carregam de `data/` (não abra o HTML por duplo-clique):

```bash
cd front
python -m http.server 8000
# abre http://localhost:8000/index.html
```

Login: **protótipo** — aceita qualquer usuário/senha só para gatear a Home. A autenticação real é responsabilidade do backend (senha com hash: bcrypt/argon2).

### Pipeline de extração (Google Colab + GPU)

Precisa de GPU e baixa modelos de ML (Chandra, Docling, YOLO) — **não roda em CPU comum**.

1. Suba `extractor/hybrid_extractor.ipynb` no [Colab](https://colab.research.google.com) e selecione **Runtime → GPU**.
2. Ajuste os caminhos do Drive no primeiro cell (onde estão os PDFs de entrada).
3. **Run all**. Saída: `SITE_EXPORT_HIBRIDO/` com a mesma estrutura de `front/data/`.
4. Para o site mostrar a extração nova, copie o conteúdo para `front/data/`.

Dependências: veja `extractor/requirements.txt` (`pip install -r requirements.txt`).

## Formato dos dados

`layout.json` — fonte estruturada por documento. Cada página tem `blocos`; cada bloco:

| Campo | Descrição |
|---|---|
| `tipo` | `texto` \| `tabela` \| `grafico` \| `foto` \| `formula` |
| `bbox` | `[x0,y0,x1,y1]` normalizado (0–1) |
| `md` | conteúdo do bloco (vai para o `document.md`) |
| `id` | identificador `pN-bM` |
| `tabela_html` / `tabela_json` | tabela estruturada (quando `tipo=tabela`) |
| `caption` / `fig` | legenda PT e recorte salvo (quando `tipo=grafico`/`foto`) |

`document.md` — o texto que vira vetor: ciência em LaTeX, tabelas em Markdown table.

## Stack

- **Extração:** Python — Docling, Chandra OCR 2, DocLayout-YOLO, PyMuPDF (todos ML/Python; sem equivalente Java).
- **Front:** HTML + CSS + JavaScript puro (sem framework, sem build).
- **Vetor:** Qdrant (collection `sb100`).

## Status e roadmap

Protótipo funcional. **Falta para produção** (backend / infra):

- [ ] Armazenamento de PDFs, markdowns, imagens e `figures/` (filesystem × object storage).
- [ ] Índice de estado (aprovado/extraído/vetorizado) com leitura rápida para o front.
- [ ] Segredos no backend (chave Qdrant, API da Squad 01) + login real com hash.
- [ ] Avaliar cache (ex.: Redis) para imagens dado o volume (~1000 PDFs/semana, ~10 usuários simultâneos).
