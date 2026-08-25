# Extrair um lote de PDFs no Colab

Cole cada bloco numa célula. O lote de 10 leva de 30 a 60 minutos de GPU,
conforme o tamanho dos documentos.

Escolha **GPU** em Ambiente de execução → Alterar tipo de ambiente de execução.

## Célula 1 — segredos

O `.env` não vai para o Colab. Guarde as chaves em Secrets (o ícone de chave na
barra lateral), com estes nomes, e marque "Notebook access":

`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`

```python
from google.colab import userdata
from pathlib import Path

!git clone -q https://github.com/nicolasaws1/parser_rag_framework.git /content/repo
%cd /content/repo
!pip install -q supabase python-dotenv pymupdf

Path("/content/repo/.env").write_text("\n".join(
    f"{k}={userdata.get(k)}" for k in
    ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY")))
print("pronto")
```

## Célula 2 — baixar o lote

```python
!python extractor/baixar_lote.py --quantos 10
```

Prioriza o que foi pedido pelo botão do site; depois os menores, que dão retorno
rápido e cabem numa sessão sem risco de perder tudo no meio. Ele imprime a
estimativa de GPU antes de você começar.

Para um documento específico: `--doc bernardi`. Para começar pelos curtos:
`--menores`.

## Célula 3 — extrair

```python
!python extractor/extrator_colab.py
```

Instala YOLO, Docling e Chandra na primeira vez (uns 5 minutos), depois processa
os PDFs de `/content/pdfs` e escreve em `/content/export`.

Se a sessão cair no meio, rode de novo: ele pula o que já terminou.

## Célula 4 — mandar para o Supabase

```python
!python scripts/ingerir_extracao.py /content/export
!python scripts/gerar_figuras.py --aplicar
```

O primeiro **atualiza** o documento que já existe, sem tocar em
`article_metadata`. Não use o `ingest_supabase.py` aqui: aquele apaga a linha de
`pdfs` e recria, e o cascade levaria junto os metadados que vieram da curadoria,
que o Colab não tem de onde repor.

O segundo gera os recortes de figura. É idempotente, então só faz o que falta.

## Célula 5 — conferir

```python
!python scripts/acervo.py
```

Depois abra o site: os documentos do lote devem aparecer como extraídos, com as
figuras.

## Se a sessão cair

Nada se perde do que já foi para o Supabase. Reabra, rode a célula 1, e a 2 vai
trazer só o que ainda falta, porque a seleção é por `extracted = false`.

## Ritmo

A média medida é de **16,9 s por página**. Faltam cerca de 2.459 páginas, ou
seja, **11 a 12 horas de GPU** para o acervo inteiro. Em lotes de 10, são umas
20 rodadas.
