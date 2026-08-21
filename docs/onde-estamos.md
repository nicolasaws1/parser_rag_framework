# Onde estamos e o que falta

Referência única do projeto. Atualizado em 2026-08-21.

## 1. O que está no ar

| | |
|---|---|
| Site (público) | https://sb100extracao.optin.com.br |
| Site (ZeroTier) | https://172.28.181.92 (avisa do certificado, é esperado) |
| Servidor | `squad2@172.28.181.92`, pasta `~/parser_rag_framework` |
| Banco | Supabase Cloud, projeto `yzxqpitsgwlbxmchlbxv` |
| Repositório | github.com/nicolasaws1/parser_rag_framework (público) |
| Local | `C:\Users\nicol\OneDrive\Área de Trabalho\SB100\squad-2` |

Dois contêineres: `api` (a aplicação) e `caddy` (HTTPS do acesso por ZeroTier).
O `cloudflared` do Silvio, que roda no mesmo servidor, encaminha o domínio para
`172.28.181.92:8010` em HTTP.

## 2. Números

| | |
|---|---|
| PDFs aprovados na curadoria | 229 |
| No banco e no storage | 230 (um saiu da curadoria depois, ver §4) |
| **Extraídos** | **12** |
| Vetorizados | 0 |
| Páginas extraídas | 724 de 3.183 |
| Storage usado | 182 MB de imagem + 347 MB de PDF |

## 3. Pendências urgentes

O site é público desde 21/08. Estas deixaram de ser higiene.

**1. Trocar a senha da Melissa.** Passou por WhatsApp em texto puro. A conta é
`admin`: cria, edita e remove usuários. Pelo painel do Supabase em
Authentication → Users, ou pedindo para ela trocar em "Minha conta" no site.

**2. Trocar a chave da API do Qdrant.** Circulou fora do repositório e está no
`PDFExtractor.py` antigo. Depois de trocar, atualizar `QDRANT_API_KEY` no `.env`
local e no do servidor.

**3. Trocar a senha do usuário `squad2`** no servidor (`passwd`), que também veio
por WhatsApp.

## 4. Pendências sem pressa

**Reboot do servidor.** 119 atualizações pendentes, 3 de segurança, e um
`*** System restart required ***` desde antes de a gente chegar. Combinar janela
com o Silvio. O `restart: unless-stopped` traz os contêineres de volta sozinho.

**O documento fora da curadoria.** `effect-of-various-amino-acids-on-shoot-regeneration-of-sugar.pdf`
saiu dos aprovados em 04/08 e continua no banco. Pela regra de usar só o que a
curadoria aprova, deveria sair. Não foi removido porque é decisão de quem cura;
o `acervo.py` continua listando como divergência.

**Provar o `001_base.sql`.** O esquema do banco foi reconstruído por
introspecção e nunca subiu num Postgres vazio. Enquanto isso não for feito, o
repositório *parece* ser a origem da verdade sem ser. Só importa se um dia o
Supabase sair da nuvem.

## 5. O trabalho que falta de verdade

**218 documentos para extrair.** A extração roda em GPU (Colab). O site já tem o
botão que coloca na fila, e a fila é lida por `/api/fila`.

**O vetorizador não existe.** O botão "Vetorizar" hoje só valida a seleção e
avisa isso na tela. Falta escrever a segmentação (chunks de 1024 com 256 de
sobreposição) e o envio ao Qdrant, collection `sb100`, com vetor denso
(Qwen3-Embedding-0.6B, dim 1024) e esparso. O que ele precisa já existe: os
textos estão em `page_blocks` e `pdfs.markdown`, e o Qdrant está no ar.

**O heartbeat do worker nunca foi ligado.** A aba Extração mostra "Offline"
sempre, porque o notebook não bate em `/api/worker/heartbeat`. Quando ligar,
lembrar do cabeçalho `X-Worker-Token`, senão leva 401.

## 6. Comandos do dia a dia

Na sua máquina, em `C:\Users\nicol\OneDrive\Área de Trabalho\SB100\squad-2`:

```bash
python scripts/checar_deploy.py       # antes de mexer no servidor
python scripts/acervo.py              # curadoria x disco x banco (só relata)
python scripts/checar_rls.py          # o banco está fechado para a chave anon?
python scripts/testar_acesso.py       # login, leitura, escrita, cargo (pede senha)
```

No servidor, em `~/parser_rag_framework`:

```bash
sudo docker compose ps
sudo docker compose logs api --tail 40
sudo docker compose exec api python scripts/checar_tudo.py
```

Depois de mudar código:

```bash
cd ~/parser_rag_framework && git pull && sudo docker compose up -d --build api
```

Depois de mudar o `.env` ou um Caddyfile:

```bash
sudo docker compose up -d caddy
```

`restart` **não** relê o `.env`; só `up -d` recria o contêiner com o valor novo.

## 7. Onde estão os arquivos

| | |
|---|---|
| PDFs originais (233) | `C:\Users\nicol\OneDrive\Área de Trabalho\SB100\PDFS aprovados` |
| Extrações (arquivo mestre) | `C:\Users\nicol\SB100_extracoes` |
| Boletim 100 pelo Chandra | `SB100_extracoes\2026-07-29_boletim100_chandra` (130 MB) |
| Cópia duplicada, pode apagar | `C:\Users\nicol\SB100_site\dados` |

## 8. Documentação

| | |
|---|---|
| `docs/deploy.md` | como subir, o que mordeu, comandos |
| `docs/pedido-infra.md` | uma página para quem administra servidor |
| `docs/servidor-local.md` | o que falta para tirar o Supabase da nuvem |
| `docs/relatorio-tecnico.md` | as decisões do pipeline de extração |
| `supabase/001` a `007` | o esquema do banco, na ordem |

## 9. Quem é quem

- **Silvio Barbieri** administra a infraestrutura e o Cloudflare, domínio
  `optin.com.br`. Trabalha com IP e porta, e o padrão da casa é TLS terminando
  no Cloudflare com HTTP até a origem.
- **Melissa** é da Squad 2 e cuida do servidor junto. Foi quem liberou o
  `sudo docker` para o usuário `squad2`.
- O servidor é compartilhado com outras squads. A porta 8000 já estava ocupada,
  por isso a nossa é a 8010.
