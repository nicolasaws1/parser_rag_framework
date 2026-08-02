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

### 2. RLS desligada nas tabelas centrais  — em aberto

A chave anon lê hoje, sem restrição:

```
pdfs  page_images  page_blocks  article_metadata  profiles  audit_log
```

Inclusive `profiles`, que é a lista de usuários e cargos.

Hoje isso não vaza, porque a chave anon nunca chega ao navegador. Mas o motivo
de estar seguro é acidental: é a topologia atual, não uma regra. No momento em
que o Supabase local ficar acessível na rede, qualquer um com a chave anon lê o
acervo inteiro e a lista de quem tem acesso.

Ligar RLS exige decidir o que cada cargo (`admin` / `curador` / `leitor`)
enxerga — decisão de produto, não detalhe técnico. Por isso ficou explícito no
`001` em vez de resolvido em silêncio.

Vale junto: os endpoints de edição ainda não checam cargo. Um `leitor`
autenticado hoje edita documento.

### 3. Usuários não atravessam por REST

`auth.users` não é exposto pelo PostgREST — só `profiles`, que é o espelho. São
2 contas hoje, todas de teste. Mais simples recriá-las no destino do que migrar
hash de senha.

### 4. Não há Dockerfile nem compose

O FastAPI roda hoje por `uvicorn` na mão. Para o servidor local falta a
imagem, o compose amarrando API + Supabase + Qdrant, e o `cloudflared`.

## Ordem sugerida

1. Conferir o `001` contra o banco vivo (defaults, constraints, cascatas).
2. Subir um Supabase local vazio e rodar `001` → `006`. Se subir limpo, o repo
   passa a ser origem da verdade.
3. Decidir a política de RLS por cargo e escrever como `007`.
4. Copiar dados: tabelas por REST, buckets por download/upload.
5. Recriar as contas, apagando as de teste.
6. Dockerfile + compose.

Só depois disso a troca de `SUPABASE_URL` no `.env` é uma mudança de uma linha.

## À parte

A chave da API do Qdrant foi colada no chat e está no `PDFExtractor.py` antigo.
Trocar antes de o servidor ir ao ar.
