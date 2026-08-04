# Subir o site em HTTPS no servidor

O combinado com quem administra o servidor: **a aplicação precisa estar de pé e
respondendo em HTTPS antes de o domínio existir.** Só então ele aponta o
Cloudflare para cá.

## A decisão que encurta isto

O Supabase **não** entra neste deploy. A API fala com o Supabase Cloud pelas
variáveis do `.env`, exatamente como faz hoje na máquina do Nicolas.

Isso separa duas coisas que estavam grudadas: colocar o site no ar é um dia de
trabalho; self-hostar o Supabase é uma semana (sete contêineres, migrar dados,
provar o esquema, assumir backup e atualização). Trazer o Supabase para casa
depois é trocar `SUPABASE_URL` no `.env` e reiniciar — o `docker-compose.yml`
não muda.

Se a ordem fosse a inversa, o domínio ficaria esperando a parte difícil.

## O que sobe

```
  Cloudflare ──► :443 caddy ──► api:8000 (rede interna)
```

Só o Caddy publica porta. A API fica na rede do compose, sem `ports:` — não
aceita conexão de fora do host. Quem termina TLS é o Caddy.

## Certificado

Use o **Origin Certificate** do Cloudflare, não o Let's Encrypt.

O Let's Encrypt exige que o domínio já aponte para este servidor para validar, e
o combinado é o contrário. O certificado de origem é emitido no painel da zona
(SSL/TLS → Origin Server → Create Certificate), vale 15 anos, é grátis, e só o
Cloudflare confia nele — que é tudo o que precisamos, já que ninguém acessa a
origem direto. Com ele dá para ligar **Full (strict)** no Cloudflare.

Quem tem acesso à zona emite: nós não temos o domínio. Peça os dois arquivos e
ponha em `deploy/certs/` como `origin.pem` e `origin.key`.

> **Alternativa que dispensa certificado:** um túnel `cloudflared`. A origem
> serve HTTP em localhost, o túnel sai de dentro para fora e **nenhuma porta de
> entrada é aberta no servidor**. Se ele preferir, é mais simples e mais seguro
> que abrir 443 — e o `docker-compose.yml` fica sem o serviço `caddy`. Vale
> perguntar, porque ele é quem conhece o padrão da casa.

## Passo a passo

```bash
git clone https://github.com/nicolasaws1/parser_rag_framework.git
cd parser_rag_framework
cp .env.example .env
```

Preencha o `.env`:

| variável | de onde vem |
|---|---|
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | painel do Supabase |
| `CURATION_API_URL`, `CURATION_API_USER`, `CURATION_API_PASSWORD` | API de curadoria |
| `WORKER_TOKEN` | invente uma string longa: `openssl rand -hex 32` |
| `DOMINIO` | o hostname que ele vai apontar |

`WORKER_TOKEN` é obrigatório no compose — ele se recusa a subir sem. É o que
fecha `/api/fila` e o heartbeat, que hoje aceitam qualquer chamada.

Coloque `origin.pem` e `origin.key` em `deploy/certs/`, e então:

```bash
docker compose up -d --build
```

## Conferir antes de avisar que está pronto

```bash
curl -k https://localhost/api/health
```

Tem de responder `{"status":"ok","supabase":"ok","worker_protegido":true}`.

Se `worker_protegido` vier `false`, o `WORKER_TOKEN` não chegou ao contêiner.

```bash
python scripts/checar_rls.py          # banco fechado para a chave anon
python scripts/testar_acesso.py       # login, leitura, escrita, cargo
```

Só depois de os três passarem é que faz sentido pedir o domínio.

## Depois que o domínio subir

O notebook de extração precisa passar a mandar `X-Worker-Token` ao consultar
`/api/fila` e ao bater heartbeat — senão a fila para de ser vista pelo lado GPU.
É a única coisa que quebra com o `WORKER_TOKEN` ligado, e quebra em silêncio.

## O que ainda não foi testado

O `Dockerfile` e o `docker-compose.yml` foram escritos sem Docker na máquina de
desenvolvimento — **nunca rodaram**. O primeiro `docker compose up --build` no
servidor é o primeiro teste real. Erro provável: alguma dependência de sistema
que o `pymupdf` precise no `python:3.12-slim`. Se acontecer, aparece no build,
não em produção.
