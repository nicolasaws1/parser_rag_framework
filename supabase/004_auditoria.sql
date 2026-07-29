-- Log de auditoria: quem entrou, quem saiu, quem alterou documento, o que entrou
-- no acervo.
--
-- Append-only por política: ninguém edita nem apaga linha daqui pelo app. Se um
-- dia precisar limpar, é decisão explícita no SQL Editor.
--
-- Rodar no SQL Editor do Supabase (a API PostgREST não executa DDL).

create table if not exists public.audit_log (
    id         bigserial primary key,
    evento     text        not null,   -- login | logout | documento_editado | ...
    ator       text,                   -- quem: hoje é o usuário digitado no login
    alvo       text,                   -- pdf_id ou nome do documento, quando houver
    detalhe    jsonb,                  -- páginas mexidas, nº de blocos, etc.
    ip         text,
    criado_em  timestamptz not null default now()
);

create index if not exists audit_log_criado_em_idx on public.audit_log (criado_em desc);
create index if not exists audit_log_evento_idx    on public.audit_log (evento);
create index if not exists audit_log_alvo_idx      on public.audit_log (alvo);

comment on table public.audit_log is
    'Append-only. O app só insere e lê; não atualiza nem remove.';
comment on column public.audit_log.ator is
    'Usuário informado no login. ATENÇÃO: o login do front é protótipo e não '
    'autentica — este campo diz o que a pessoa digitou, não quem ela é. Só vira '
    'identidade de verdade quando o Supabase Auth entrar.';

alter table public.audit_log enable row level security;

drop policy if exists "ler auditoria" on public.audit_log;
create policy "ler auditoria" on public.audit_log for select using (true);

-- só insert: sem update nem delete, para o log não poder ser reescrito pelo app
drop policy if exists "gravar auditoria" on public.audit_log;
create policy "gravar auditoria" on public.audit_log for insert with check (true);
