-- 1) Metadados completos da API de curadoria + 2) procedência da vetorização.
--
-- Rodar no SQL Editor do Supabase (a API PostgREST não executa DDL).

-- ── 1) metadados ────────────────────────────────────────────────────────────
-- A API devolve 45 campos por artigo e o schema dela JÁ MUDOU uma vez: as chaves
-- eram em português com espaços ("URL DO DOCUMENTO", "APROVAÇÃO CURADOR (marcar)")
-- e hoje são camelCase em inglês (documentUrl, status). Guardar o registro cru em
-- jsonb evita ter que migrar coluna a cada mudança dela — as colunas tipadas
-- ficam só para o que o site consulta e ordena.
alter table public.article_metadata
    add column if not exists raw          jsonb,      -- registro completo da API
    add column if not exists abstract     text,
    add column if not exists keywords     text,
    add column if not exists publisher    text,
    add column if not exists institution  text,
    add column if not exists location     text,
    add column if not exists volume       text,
    add column if not exists issue        text,
    add column if not exists pages        text,
    add column if not exists category     text,
    add column if not exists document_type text,
    add column if not exists nutrients    text,
    add column if not exists crops        text,
    add column if not exists tools        text,
    add column if not exists api_id       text,       -- _id da API, para reconciliar
    add column if not exists synced_at    timestamptz;

create index if not exists article_metadata_api_id_idx on public.article_metadata (api_id);

-- status vindo da curadoria: hoje "Aprovado por IA" ou "Rejeitado"
alter table public.pdfs
    add column if not exists curation_status text,
    add column if not exists document_url    text;    -- nome do arquivo na API (chave de junção)

create index if not exists pdfs_document_url_idx on public.pdfs (document_url);

-- ── 2) procedência da vetorização ───────────────────────────────────────────
-- "o que já está vetorizado, como foi vetorizado e quando". Uma linha por
-- (documento, collection), sobrescrita a cada nova vetorização — mesmo critério
-- das edições: sem acumular cópias.
create table if not exists public.vectorizations (
    pdf_id        uuid not null references public.pdfs (id) on delete cascade,
    collection    text not null,
    dense_model   text,
    dense_dim     integer,
    sparse_model  text,
    chunk_size    integer,
    chunk_overlap integer,
    chunks        integer,
    pontos        integer,
    origem_texto  text,          -- 'extracao' ou 'edicao': de onde saiu o texto
    started_at    timestamptz,
    finished_at   timestamptz,
    ok            boolean,
    erro          text,
    primary key (pdf_id, collection)
);

comment on column public.vectorizations.origem_texto is
    'De onde veio o texto vetorizado. Importa: se for "extracao", a curadoria '
    'feita à mão em document_edits NÃO entrou no índice.';

create index if not exists vectorizations_finished_idx
    on public.vectorizations (finished_at desc);

alter table public.vectorizations enable row level security;
drop policy if exists "ler vetorizacoes" on public.vectorizations;
create policy "ler vetorizacoes" on public.vectorizations for select using (true);
drop policy if exists "gravar vetorizacoes" on public.vectorizations;
create policy "gravar vetorizacoes" on public.vectorizations for all using (true) with check (true);
