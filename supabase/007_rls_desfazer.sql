-- Desfaz o 007. Só use se o site quebrar depois de aplicá-lo.
--
-- Isto devolve o banco ao estado ANTERIOR, em que a chave anon lê tudo —
-- inclusive `profiles`. É uma saída de emergência, não uma configuração:
-- se precisar rodar isto, o motivo precisa ser entendido antes de tentar de novo.

alter table public.pdfs             no force row level security;
alter table public.page_images      no force row level security;
alter table public.page_blocks      no force row level security;
alter table public.article_metadata no force row level security;
alter table public.profiles         no force row level security;
alter table public.document_edits   no force row level security;
alter table public.audit_log        no force row level security;
alter table public.vectorizations   no force row level security;

alter table public.pdfs             disable row level security;
alter table public.page_images      disable row level security;
alter table public.page_blocks      disable row level security;
alter table public.article_metadata disable row level security;
alter table public.profiles         disable row level security;
alter table public.document_edits   disable row level security;
alter table public.audit_log        disable row level security;
alter table public.vectorizations   disable row level security;

grant select on public.pdfs                 to anon, authenticated;
grant select on public.page_images          to anon, authenticated;
grant select on public.page_blocks          to anon, authenticated;
grant select on public.article_metadata     to anon, authenticated;
grant select on public.profiles             to anon, authenticated;
grant select on public.document_edits       to anon, authenticated;
grant select on public.audit_log            to anon, authenticated;
grant select on public.vectorizations       to anon, authenticated;
grant select on public.document_block_counts to anon, authenticated;

alter default privileges in schema public grant select on tables to anon, authenticated;
