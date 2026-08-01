# Metadados por página (complemento ao schema atual)

**Responsável:** Melissa
**Solicitante:** Nicolas (Squad 2 — extração)

**EU COMO** desenvolvedor da Squad 2
**GOSTARIA DE** poder salvar os metadados que a extração produz **por página** (hoje o schema
guarda por documento e por bloco, mas não por página)
**PARA QUE** o site mostre a rota usada em cada página e, principalmente, para que o texto
extraído não se perca — hoje ele é descartado na ingestão e só voltaria re-extraindo o PDF
(processo caro, roda em GPU).

## Contexto

O extrator produz, para **cada página**, informações que hoje não têm onde ser gravadas:

| Campo | O que é | Por que importa |
|---|---|---|
| `raw_text` | camada de texto pura da página (PyMuPDF) | permite testar novas estratégias de chunking **sem re-extrair** o PDF |
| `route` | qual motor extraiu (`docling` / `chandra`) | o site exibe como selo na tela de Extração; serve de auditoria |
| `page_type` | `organica` (tem texto) ou `escaneada` (precisou de OCR) | métrica de qualidade do corpus |
| `width` / `height` | dimensões da imagem da página | posicionamento e recorte de figuras |

Sem isso, o `raw_text` **é perdido na ingestão** — e ele é justamente o que evita re-rodar a
extração (que leva ~250 s por documento em GPU) a cada teste de segmentação.

## Sugestão de implementação

Qualquer uma das duas resolve — a escolha é sua:

**(a) Colunas novas em `page_images`** — mais simples, já existe uma linha por página:
```sql
alter table page_images
  add column raw_text  text,
  add column route     text,
  add column page_type text,
  add column width     integer,
  add column height    integer;
```

**(b) Tabela `page_metadata`** — mais limpo semanticamente (`page_images` fica só com imagem):
```sql
create table page_metadata (
  id          uuid primary key default gen_random_uuid(),
  pdf_id      uuid references pdfs(id) on delete cascade,
  page_number integer,
  raw_text    text,
  route       text,
  page_type   text,
  width       integer,
  height      integer
);
```

## Critérios de avaliação

- Campos disponíveis para gravação por página (via uma das opções acima).
- `raw_text` aceita textos longos (uma página cheia pode passar de 5 mil caracteres).
- Relação com `pdfs` com **cascade delete**, como nas demais tabelas.
- Nomenclatura em inglês, consistente com o schema atual.

## Observação

Assim que estiver criado, eu ajusto `scripts/ingest_supabase.py` para popular esses campos —
os dados já são gerados pelo extrator, só falta o destino.
