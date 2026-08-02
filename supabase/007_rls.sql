-- 007 — fecha o banco para acesso direto
--
-- Até aqui `anon` lia tudo: pdfs, page_blocks, article_metadata, audit_log e
-- profiles — a lista de usuários e cargos. Não vazava porque a chave anon nunca
-- chega ao navegador (o front fala só com o FastAPI). Mas isso é a topologia
-- atual, não uma regra: no instante em que o Supabase local ficar acessível na
-- rede, quem tiver a chave anon lê o acervo inteiro.
--
-- A postura aqui: **o banco não é uma API pública**. Todo acesso passa pelo
-- FastAPI, que usa `service_role`. A `service_role` ignora RLS por desenho, então
-- ligar RLS sem política nenhuma não quebra o app — fecha só a porta de trás.
--
-- Consequência que não dá para esquecer: RLS **não** separa admin de curador de
-- leitor, porque a service_role passa por cima de todas elas. Essa separação vive
-- em `api/auth.py:exigir_cargo` e só ali. Se um dia o front falar direto com o
-- PostgREST usando o JWT do usuário, este arquivo precisa ganhar políticas de
-- verdade por cargo — hoje ele deliberadamente não tem nenhuma.

-- ── as políticas de quando o login era protótipo ────────────────────────────
-- 002/004/006 abriram `using (true)` porque não havia autenticação para checar.
-- Hoje há. `using (true)` sob RLS libera para anon: é pior que RLS desligada,
-- porque parece protegido.
drop policy if exists "ler edicoes"       on public.document_edits;
drop policy if exists "gravar edicoes"    on public.document_edits;
drop policy if exists "ler auditoria"     on public.audit_log;
drop policy if exists "gravar auditoria"  on public.audit_log;
drop policy if exists "ler vetorizacoes"  on public.vectorizations;
drop policy if exists "gravar vetorizacoes" on public.vectorizations;

-- ── RLS ligada em tudo, sem política: ninguém entra a não ser a service_role ─
alter table public.pdfs             enable row level security;
alter table public.page_images      enable row level security;
alter table public.page_blocks      enable row level security;
alter table public.article_metadata enable row level security;
alter table public.profiles         enable row level security;
alter table public.document_edits   enable row level security;
alter table public.audit_log        enable row level security;
alter table public.vectorizations   enable row level security;

-- force: sem isso o dono da tabela continua escapando da RLS
alter table public.pdfs             force row level security;
alter table public.page_images      force row level security;
alter table public.page_blocks      force row level security;
alter table public.article_metadata force row level security;
alter table public.profiles         force row level security;
alter table public.document_edits   force row level security;
alter table public.audit_log        force row level security;
alter table public.vectorizations   force row level security;

-- ── cinto e suspensório: tirar o GRANT também ───────────────────────────────
-- RLS sem política devolve zero linhas, mas devolve 200. Sem o GRANT, a mesma
-- tentativa devolve 401/403 — o erro diz a verdade, e uma política criada por
-- engano no futuro não reabre nada sozinha.
revoke all on public.pdfs             from anon, authenticated;
revoke all on public.page_images      from anon, authenticated;
revoke all on public.page_blocks      from anon, authenticated;
revoke all on public.article_metadata from anon, authenticated;
revoke all on public.profiles         from anon, authenticated;
revoke all on public.document_edits   from anon, authenticated;
revoke all on public.audit_log        from anon, authenticated;
revoke all on public.vectorizations   from anon, authenticated;

revoke all on public.document_block_counts from anon, authenticated;

-- futuras tabelas nascem fechadas
alter default privileges in schema public revoke all on tables from anon, authenticated;

-- ── storage ────────────────────────────────────────────────────────────────
-- Os dois buckets já são privados e o app só serve por URL assinada. Sem política
-- em storage.objects, anon não lista nem baixa. Fica registrado para o próximo
-- que criar um bucket achar que `public: true` é conveniência inofensiva.
update storage.buckets set public = false where id in ('pdfs', 'images');

-- ── conferência ────────────────────────────────────────────────────────────
-- Depois de rodar, `scripts/checar_rls.py` tenta ler cada tabela com a chave
-- anon e falha se alguma responder com linha.
