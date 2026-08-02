# Mudar o Supabase para servidor local

Levantamento feito em 2026-08-02 contra o banco em produção.

## Resposta curta

Não, ainda não. Falta pouco, mas há dois itens que impedem — um deles é
segurança, e só aparece **depois** que o Supabase for exposto na rede.

## O que já não atrapalha

| item | situação |
|---|---|
| URL do Supabase no código | nenhuma. Tudo por `SUPABASE_URL` / `SUPABASE_*_KEY` |
| chave anon no navegador | não vai. O front fala só com `/api`; o FastAPI usa `service_role` do lado servidor |
| volume de dados | pequeno: 645 MB de storage e ~9 mil linhas |
| front | servido pelo próprio FastAPI (`StaticFiles`), sem build |

Volume medido:

```
pdfs                230 linhas      page_images    724
page_blocks       7 826            article_metadata  230
audit_log           294            profiles           2

bucket pdfs      230 objetos   347 MB
bucket images  1 235 objetos   298 MB
```

Copiar isso é questão de minutos, não é o gargalo.

## O que impede

### 1. O esquema não subia do repositório  — resolvido, falta conferir

As migrações começavam em `002`. As tabelas centrais (`pdfs`, `page_images`,
`page_blocks`, `article_metadata`, `profiles`) foram criadas à mão no painel e
nunca entraram no repo. Um Supabase novo não tinha como ser reconstruído.

`supabase/001_base.sql` foi escrito por introspecção do banco vivo. Nomes e
tipos de coluna vêm do esquema OpenAPI do PostgREST e conferem. **Defaults e
nomes de constraint não são visíveis por introspecção** e foram inferidos do
uso no código — precisam de uma passada antes de valer como origem da verdade.

### 2. RLS desligada nas tabelas centrais  — resolvido em 2026-08-02

A chave anon lia, sem restrição, `pdfs`, `page_images`, `page_blocks`,
`article_metadata`, `audit_log` e `profiles` — a lista de usuários e cargos.

`007_rls.sql` fechou: RLS ligada e forçada nas oito tabelas, sem política
nenhuma, mais `revoke` do GRANT de `anon`/`authenticated`. Conferido com
`scripts/checar_rls.py`: as nove relações respondem 401 para a chave anon, e a
`service_role` continua lendo e escrevendo normalmente.

O que a RLS **não** resolve, e por isso quase passou batido: a `service_role`
ignora RLS por desenho, e é ela que o FastAPI usa. Separação por cargo não pode
morar no banco enquanto for assim — mora em `api/auth.py:exigir_cargo`.

E ali o buraco era maior do que "um leitor edita documento": nenhum endpoint de
escrita pedia token. Sem login algum dava para editar, descartar edição, pedir
extração e disparar a sincronização. Agora leitura exige usuário e escrita
exige `admin` ou `curador`.

### 3. Usuários não atravessam por REST

`auth.users` não é exposto pelo PostgREST — só `profiles`, que é o espelho. São
2 contas hoje, todas de teste. Mais simples recriá-las no destino do que migrar
hash de senha.

### 4. Não há Dockerfile nem compose

O FastAPI roda hoje por `uvicorn` na mão. Para o servidor local falta a
imagem, o compose amarrando API + Supabase + Qdrant, e o `cloudflared`.

## Ordem sugerida

1. Conferir o `001` contra o banco vivo (defaults, constraints, cascatas).
2. Subir um Supabase local vazio e rodar `001` → `007`. Se subir limpo, o repo
   passa a ser origem da verdade.
3. Copiar dados: tabelas por REST, buckets por download/upload.
4. Recriar as contas, apagando as de teste.
5. Dockerfile + compose.

Só depois disso a troca de `SUPABASE_URL` no `.env` é uma mudança de uma linha.

## À parte

- **Chave do Qdrant**: circulou fora do repositório e está num script antigo
  (`PDFExtractor.py`). Trocar antes de o servidor ir ao ar.
- **`WORKER_TOKEN`**: enquanto a variável não existir, `/api/fila` e
  `/api/worker/heartbeat` aceitam qualquer chamada — o lado GPU ainda não manda
  o cabeçalho `X-Worker-Token`, e exigir agora derrubaria a fila. `/api/health`
  responde `worker_protegido: false` justamente para isso não passar batido.
  Fechar junto com o deploy, quando o notebook passar a mandar o cabeçalho.
