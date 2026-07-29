-- Contadores por documento para a Home.
--
-- Antes a API buscava TODOS os blocos (13 mil linhas, em páginas de mil) e
-- contava em Python a cada visita: ~4,9 s com 12 documentos, e cresce linear
-- com o corpus — com 180 documentos ficaria inviável.
--
-- A view deixa o Postgres agregar e devolve uma linha por documento. Não
-- duplica dado: é sempre derivada de page_blocks, então nunca desatualiza.
--
-- Rodar no SQL Editor do Supabase (a API PostgREST não executa DDL).

create or replace view public.document_block_counts as
select
    pdf_id,
    count(*)                                                as blocos,
    count(*) filter (where block_type = 'tabela')           as tabelas,
    count(*) filter (where block_type in ('grafico','foto')) as figuras,
    count(*) filter (where block_type = 'formula')          as formulas
from public.page_blocks
group by pdf_id;

comment on view public.document_block_counts is
    'Contadores por documento para a Home. Derivada de page_blocks — não guarda cópia.';

-- a view varre page_blocks agrupando por pdf_id; o índice sustenta isso e também
-- a leitura por página (/api/document/{id}/pagina/{n}), que filtra pelos dois
create index if not exists page_blocks_pdf_pagina_idx
    on public.page_blocks (pdf_id, page_number);

grant select on public.document_block_counts to anon, authenticated, service_role;
