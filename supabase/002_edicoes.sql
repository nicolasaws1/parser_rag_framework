-- Camada de edição manual sobre a extração.
--
-- Modelo: a extração original é IMUTÁVEL (pdfs.markdown, page_blocks). Cada
-- documento tem NO MÁXIMO UMA linha de edição, sobrescrita a cada alteração —
-- não acumula histórico nem cria cópias novas. A data em `edited_at` diz quando
-- foi a última alteração.
--
-- Rodar no SQL Editor do Supabase (a API PostgREST não executa DDL).

create table if not exists public.document_edits (
    pdf_id      uuid primary key references public.pdfs (id) on delete cascade,
    layout      jsonb,        -- páginas/blocos editados; null = layout original
    markdown    text,         -- document.md editado; null = markdown original
    edited_at   timestamptz not null default now(),
    edited_by   text
);

comment on table  public.document_edits is
    'Uma linha por documento, sobrescrita a cada edição. A extração original nunca é alterada.';
comment on column public.document_edits.layout is
    'Layout completo editado. Null quando só o markdown foi mexido.';
comment on column public.document_edits.edited_at is
    'Momento da ÚLTIMA alteração — não há histórico anterior, por decisão de projeto.';

-- mantém edited_at correto sem depender de quem escreve
create or replace function public.tocar_edited_at()
returns trigger language plpgsql as $$
begin
    new.edited_at = now();
    return new;
end;
$$;

drop trigger if exists trg_document_edits_edited_at on public.document_edits;
create trigger trg_document_edits_edited_at
    before insert or update on public.document_edits
    for each row execute function public.tocar_edited_at();

alter table public.document_edits enable row level security;

-- leitura para qualquer usuário autenticado; escrita idem.
-- AJUSTE conforme a política de acesso do projeto: hoje o login do front é
-- protótipo e não autentica de verdade, então a escrita fica aberta a quem
-- alcançar a API.
drop policy if exists "ler edicoes" on public.document_edits;
create policy "ler edicoes" on public.document_edits
    for select using (true);

drop policy if exists "gravar edicoes" on public.document_edits;
create policy "gravar edicoes" on public.document_edits
    for all using (true) with check (true);
