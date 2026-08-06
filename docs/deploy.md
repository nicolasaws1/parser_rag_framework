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

## Certificado — não espere por ele

**Dá para subir hoje, sem certificado e sem domínio.** É o padrão do compose: o
`Caddyfile.interno` usa um certificado que o próprio Caddy gera. A aplicação
fica de pé em HTTPS e você prova que responde. O navegador reclama se você
acessar a origem direto — esperado, ninguém assinou aquilo — mas o Cloudflare em
modo **Full** aceita origem assim.

Quando o **Origin Certificate** chegar, é trocar uma linha no `.env`:

```
CADDYFILE=Caddyfile.origem
```

e `docker compose up -d`. Sem rebuild, sem downtime relevante. Aí dá para ligar
**Full (strict)**.

O certificado sai do painel da zona (SSL/TLS → Origin Server → Create
Certificate), vale 15 anos e é grátis. **Quem tem a zona emite** — nós não temos
o domínio. Peça `origin.pem` e `origin.key` e ponha em `deploy/certs/`.

Não use Let's Encrypt: ele exige o domínio já apontando para este servidor, e a
ordem combinada é a inversa.

> **Alternativa que dispensa certificado de vez:** um túnel `cloudflared`. A
> origem serve HTTP, o túnel sai de dentro para fora e **nenhuma porta de entrada
> é aberta no servidor**. Mais simples e mais seguro que abrir a 443, e o
> `caddy` sai do compose. Vale perguntar — ele é quem conhece o padrão da casa.

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

`DOMINIO` pode ficar `localhost` enquanto o domínio não existir, e `CADDYFILE`
pode ficar no padrão. Nada disso impede de subir:

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

## O que foi testado sem Docker, e o que não dá para testar sem ele

Não há Docker na máquina de desenvolvimento. Estas partes foram verificadas
uma a uma, fora do contêiner:

| verificado | resultado |
|---|---|
| todo pacote tem wheel manylinux cp312 | sim — nada compila, `python:3.12-slim` basta sem `gcc` |
| `pymupdf` precisa de biblioteca do sistema | não — a wheel é `abi3` manylinux_2_28 e a base tem glibc 2.36 |
| o `CMD` sobe a aplicação | sim |
| o `HEALTHCHECK` devolve 0 com a API viva | sim |
| `WORKER_TOKEN` fecha `/api/fila` e o heartbeat | sim — 5 casos: sem token, token errado e token certo |
| `/api/health` reflete o token | sim — `worker_protegido: true` |
| YAML do compose | válido |
| chave privada fora do git e da imagem | `.gitignore` e `.dockerignore` cobrem |

**O que só o servidor prova:** a montagem dos volumes, a rede entre `caddy` e
`api`, o `depends_on: service_healthy`, e o certificado sendo aceito. Nenhum
deles depende de código nosso — se falharem, falham no `up`, com mensagem
clara, e não em produção.
