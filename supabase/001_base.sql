-- 001 — esquema base
--
-- Estas tabelas nasceram à mão no painel do Supabase, antes de as migrações
-- existirem, e por isso nunca estiveram no repositório: as migrações começavam
-- em 002 e pressupunham um banco que ninguém sabia recriar. Um Supabase novo
-- (servidor local, réplica, ambiente de teste) não subia a partir do repo.
--
-- Reconstruído por introspecção do banco em produção (2026-08-02) via o esquema
-- OpenAPI do PostgREST: nomes e tipos de coluna vêm de lá e conferem. O que a
-- introspecção NÃO mostra são defaults e nomes de constraint — foram inferidos
-- do uso no código e estão marcados abaixo. Confira antes de valer como origem.
--
-- Ordem: 001 -> 002 -> 003 -> 004 -> 005 -> 006.

create extension if not exists pgcrypto;   -- gen_random_uuid()

-- ─────────────────────────────── documentos ────────────────────────────────
create table if not exists public.pdfs (
  id                       uuid primary key default gen_random_uuid(),
  pdf_file                 text not null unique,   -- slug; é a chave natural
  markdown                 text,                   -- documento inteiro concatenado
  total_pages              integer,
  approved                 boolean default false,
  approved_at              timestamp,
  extracted                boolean default false,
  extracted_at             timestamp,
  vectorized               boolean default false,
  vectorized_at            timestamp,
  extraction_time_ms       integer,
  pipeline                 text,                   -- qual combinação de extratores rodou
  created_at               timestamp default now(),
  -- 005: fila de extração pedida pelo site
  extraction_requested_at  timestamptz,
  extraction_requested_by  text,
  -- 006: vínculo com a API de curadoria
  curation_status          text,
  document_url             text                    -- nome exato do arquivo na curadoria
);

-- uma linha por página renderizada; image_file é o caminho no bucket `images`.
-- Nunca remontar esse caminho a partir de pdfs.pdf_file: um documento pode ser
-- renomeado (foi, o Boletim 100) sem que as imagens mudem de lugar.
create table if not exists public.page_images (
  id           uuid primary key default gen_random_uuid(),
  pdf_id       uuid references public.pdfs (id) on delete cascade,
  page_number  integer,
  image_file   text,
  route        text,        -- extrator escolhido para a página
  page_type    text,        -- digital | escaneada | mista
  width        integer,
  height       integer,
  unique (pdf_id, page_number)
);

create table if not exists public.page_blocks (
  id             uuid primary key default gen_random_uuid(),
  pdf_id         uuid references public.pdfs (id) on delete cascade,
  page_number    integer,
  block_type     text,      -- texto | tabela | grafico | foto | formula | titulo
  markdown_text  text,
  bbox           jsonb,     -- [x0,y0,x1,y1] normalizado 0–1
  layout         jsonb      -- sobras do extrator (origem, score, ordem)
);

create index if not exists page_images_pdf_idx on public.page_images (pdf_id, page_number);
create index if not exists page_blocks_pdf_idx on public.page_blocks (pdf_id, page_number);

-- ──────────────────────── metadados vindos da curadoria ────────────────────
create table if not exists public.article_metadata (
  id             uuid primary key default gen_random_uuid(),
  pdf_id         uuid references public.pdfs (id) on delete cascade,
  title          text,
  authors        text,
  journal        text,
  year           integer,
  doi            text,
  abstract       text,
  keywords       text,
  publisher      text,
  institution    text,
  location       text,
  volume         text,
  issue          text,
  pages          text,
  category       text,
  document_type  text,
  nutrients      text,
  crops          text,
  tools          text,
  raw            jsonb,      -- resposta crua da API, para não perder campo novo
  api_id         text,
  synced_at      timestamptz
);

create index if not exists article_metadata_pdf_idx on public.article_metadata (pdf_id);

-- ──────────────────────────────── usuários ─────────────────────────────────
-- espelha auth.users; `role` é lido por api/auth.py (admin | curador | leitor)
create table if not exists public.profiles (
  id    uuid primary key references auth.users (id) on delete cascade,
  name  text,
  role  text default 'leitor'
);

-- ──────────────────────────────── buckets ──────────────────────────────────
-- privados: o site nunca serve o arquivo direto, sempre por URL assinada
insert into storage.buckets (id, name, public)
values ('pdfs', 'pdfs', false), ('images', 'images', false)
on conflict (id) do nothing;

-- ─────────────────────────────────── RLS ───────────────────────────────────
-- Hoje estas tabelas estão SEM RLS: a chave anon lê pdfs, page_blocks e até
-- profiles inteiros. Não vaza porque o front fala só com o FastAPI e a chave
-- anon não chega ao navegador — mas expor o Supabase direto na rede sem ligar
-- RLS entrega o acervo e a lista de usuários a quem tiver a chave.
-- Ligar exige decidir o que cada cargo enxerga; deixado explícito em vez de
-- silencioso. Ver `docs/servidor-local.md`.
