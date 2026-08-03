# SB100 / Squad 2 — o que precisamos do servidor

Uma página para quem administra a infraestrutura. Contexto curto, pedido no fim.

## O que é

Plataforma de extração e vetorização de PDFs científicos (agronomia — citros e
cana). Pega o PDF, separa página por página em texto, tabelas e figuras, e joga
no Qdrant para busca semântica. Tem um site interno onde a equipe confere e
corrige o que a extração produziu.

## Onde estamos

| | |
|---|---|
| PDFs aprovados na curadoria | 230, todos já baixados e catalogados |
| extraídos | 12 (3.183 páginas no total do acervo) |
| site | funcionando, com login, cargos e trilha de auditoria |
| Qdrant | collection `sb100` já existe no servidor de vocês |

A extração pesada roda em GPU **fora** deste servidor (hoje Colab). Este
servidor não precisa de placa de vídeo.

## As peças

```
  navegador ──► Cloudflare ──► API (FastAPI, Python)
                                 │
                                 ├─► Supabase  (Postgres + Auth + Storage)
                                 └─► Qdrant    (já está aí)
```

A API é leve: cinco dependências Python, um processo, sem GPU, sem build de
front-end (o site é um HTML servido por ela mesma).

O Supabase é a parte grande — self-hosted ele é um `docker compose` com ~7
contêineres (Postgres, GoTrue para autenticação, PostgREST, storage-api, Kong,
Studio). Não é escolha nossa: é como o projeto se distribui.

## Recursos

| | |
|---|---|
| disco (dados) | ~1,4 GB quando os 230 estiverem extraídos — 0,34 GB de PDF e ~1 GB de imagem de página |
| disco (folga total) | 20 GB confortável, contando Postgres, índices e backup |
| memória | 4 GB dá conta; 8 GB folgado |
| CPU | 2 núcleos. Nenhum processamento pesado roda aqui |
| banco | ~34 mil linhas na maior tabela. Irrelevante para o Postgres |

O número de disco é medido, não estimado: 724 páginas já extraídas ocupam
182 MB de imagem, ou 258 KB por página, e o acervo tem 3.183 páginas.

## O pedido

1. **Docker e docker compose** no host.
2. **Um hostname** atrás do Cloudflare, do mesmo jeito que o
   `sb100qdrant.optin.com.br` já está. Túnel (`cloudflared`) ou DNS proxiado,
   o que for padrão de vocês.
3. **Só a API exposta.** Postgres, GoTrue, PostgREST e storage-api ficam na
   rede interna do compose, sem porta publicada. Isso importa: o banco guarda
   o acervo e a lista de usuários.
4. **Backup do volume do Postgres e do volume de storage.** Se o disco morrer,
   o que se perde é a extração — semanas de GPU, não só arquivo.

## Detalhes de Cloudflare que já nos morderam

- **Timeout de ~100 s.** A API de curadoria de onde puxamos os PDFs tem um
  endpoint que zipa a coleção inteira: ele devolve **524** sempre, porque o zip
  demora mais que o limite. Contornamos baixando um a um. Nosso próprio serviço
  não tem requisição longa — a mais pesada é o Boletim 100 com 511 páginas, e
  responde em 1,6 s —, mas vale saber que o teto existe.
- **Tamanho de corpo.** Subimos PDFs de até ~8 MB. Dentro do limite padrão,
  mas se houver regra mais apertada no proxy de vocês, precisamos saber.
- Sem WebSocket, sem streaming, sem sessão presa a instância.

## O que ainda não está pronto do nosso lado

Para não haver surpresa depois:

- **Dockerfile e compose ainda não existem.** É o próximo passo nosso.
- **O esquema do banco (`supabase/001_base.sql`) nunca foi provado num banco
  vazio.** Foi reconstruído por introspecção do banco atual. Antes de migrar
  para valer, subimos um Supabase local vazio e rodamos as migrações do zero.
- **Dois endpoints de fila ainda são abertos** (`/api/fila` e o heartbeat do
  worker). Aceitam um cabeçalho `X-Worker-Token` assim que a variável
  `WORKER_TOKEN` existir; fechamos junto com o deploy. `/api/health` responde
  `worker_protegido: false` enquanto estiverem assim.

O acesso direto ao banco já está fechado: RLS ligada em todas as tabelas, sem
política, e `GRANT` revogado de `anon`/`authenticated`. Só a chave de servidor
lê, e ela não sai da API.
