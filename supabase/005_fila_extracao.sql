-- Fila de extração.
--
-- A extração roda no lado com GPU (hoje o notebook no Colab). O servidor que
-- serve o site NÃO tem GPU e não extrai nada — o botão do site só marca que o
-- documento foi pedido, e o lado GPU consulta essa marca para saber o que fazer.
--
-- Uma coluna resolve: não precisa de tabela de fila enquanto o volume for este.
--
-- Rodar no SQL Editor do Supabase (a API PostgREST não executa DDL).

alter table public.pdfs
    add column if not exists extraction_requested_at timestamptz,
    add column if not exists extraction_requested_by text;

comment on column public.pdfs.extraction_requested_at is
    'Quando a extração foi pedida pelo site. Null = ninguém pediu. '
    'Volta a null quando a extração conclui (extracted = true).';

-- para o lado GPU perguntar "o que está pedido e ainda não foi extraído?"
create index if not exists pdfs_fila_extracao_idx
    on public.pdfs (extraction_requested_at)
    where extracted is not true and extraction_requested_at is not null;
