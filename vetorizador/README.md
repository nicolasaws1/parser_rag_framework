# Vetorizar documentos extraídos

O site entrega um pacote, a GPU calcula os vetores e grava direto no Qdrant,
e o site confirma o que foi gravado.

```
site (aba Vetorização) --pacote_….zip--> GPU --vetores--> Qdrant
                                                            |
site <-- Confirmar (o site conta os pontos e registra no banco)
```

A máquina com GPU recebe só a chave do Qdrant, digitada na hora ou guardada nos
Secrets do Colab como `QDRANT_API_KEY`. Ela nunca vai no pacote, no notebook nem
no repositório (que é público). A chave do Supabase continua só no site.

Sem acesso ao Qdrant na máquina da GPU, o caminho antigo continua valendo:
`ENVIAR_DIRETO = False` gera um `vetores_….zip`, e o site grava ao receber.

## A coleção vem primeiro

Cada coleção do Qdrant é um índice à parte. Um documento pode estar em `sb100` e
faltar em outra, então a aba sempre mostra a situação **na coleção escolhida**:

- pontos gravados por aqui têm `pdf_id` no payload;
- pontos dos scripts antigos só têm `file` (o nome do arquivo da curadoria, às
  vezes com `.md` no lugar de `.pdf`). O site reconhece esses também, para não
  mandar revetorizar o que já está lá.

Coleção que não tem os vetores `vetor_denso` (1024, cosseno) e `vetor_esparso`
aparece desabilitada. Coleção nova só o admin cria; nome começando com `teste_`
fica marcado como teste.

## Passo a passo (GPU)

1. Aba **Vetorização**: escolha a coleção, marque os documentos (o filtro padrão
   já mostra só os que faltam nela) e clique em **Baixar pacote**. Até 100 por pacote.
2. Baixe o `vetorizador_lote.ipynb` no mesmo lugar e abra no Colab com GPU.
3. Célula 2: envie o `pacote_….zip`. Célula 3: a chave do Qdrant e, se quiser,
   outra coleção em `COLECAO` (vazio usa a escolhida no site). Célula 4: calcula
   e grava.
4. Volte à aba, clique em **Atualizar** no quadro "Gravados pela GPU, aguardando
   confirmação" e em **Confirmar vetorização**. O site conta os pontos de cada
   documento no Qdrant, recusa os incompletos (envio interrompido) e registra os
   outros no banco.

Documento que já tem pontos na coleção, gravados por aqui ou pelos scripts
antigos, é pulado antes de calcular. Para regravar, marque `SUBSTITUIR`: os
pontos antigos dele saem antes de os novos entrarem.

Sem notebook, num servidor com GPU:

```bash
pip install -r requirements-vetorizador.txt
export QDRANT_API_KEY=...        # não grave em arquivo do repositório
python vetorizador/rodar_vetorizacao.py pacote_….zip --enviar --lote 64
python vetorizador/rodar_vetorizacao.py pacote_….zip --enviar --colecao sb100_clone
```

Se a sessão cair, rode de novo com o mesmo pacote: o documento já calculado não
é recalculado, e o já gravado não é gravado de novo.

## O que vai em cada trecho

| | |
|---|---|
| texto | janelas de 1024 caracteres com 205 de sobreposição (20%), cortadas em espaço |
| tabela | inteira, num trecho só; o HTML original vai no payload (`tabela_html`) |
| figura | legenda + descrição, com o caminho do recorte (`figura`) |

Tabela e figura não interrompem o texto: o parágrafo que continua depois delas
segue na mesma janela. O texto é o da tela Analisar, com as correções feitas à mão.

O payload mantém as chaves dos scripts antigos (`content`, `file`, `pagina_pdf`,
`titulo`, `autores`, `ano`…) e acrescenta `pdf_id`, `paginas_pdf`, `blocos`,
`origem_texto`, `versao_chunker`, os modelos que calcularam os vetores, `ingestao`
(quando os vetores foram gravados) e `extraido_em` (quando o documento foi
extraído), as duas em UTC. As páginas são sempre as do PDF: `pagina_pdf` é onde
está a maior parte do trecho, `paginas_pdf` todas as que ele toca. O id de cada ponto é fixo para
(coleção, documento, trecho): regravar substitui, não duplica.

## Modelos

| | modelo | vetor |
|---|---|---|
| denso | `Qwen/Qwen3-Embedding-0.6B` | `vetor_denso`, 1024, cosseno |
| esparso | `opensearch-project/opensearch-neural-sparse-encoding-doc-v2-distill` | `vetor_esparso` |

O esparso segue a receita do cartão do modelo (máscara de atenção, log(1+relu),
tokens especiais zerados). Os scripts antigos não usavam a máscara nem o log;
numa coleção antiga, os pontos novos ficam com pesos levemente diferentes.

## Vetorizar no próprio site

A aba tem um segundo caminho, **Vetorizar no site**, que roda tudo no servidor
sem baixar nada. Ele só é liberado quando o servidor aguenta: a API sonda a CPU
e as bibliotecas num processo à parte (um import que exige instrução ausente
derrubaria o site).

Hoje o servidor do site **não aguenta**: a VM anuncia só `sse` e `sse2`, sem AVX,
porque usa um modelo de CPU genérico. Para liberar, quem administra a VM precisa
expor a CPU real do hospedeiro (no KVM ou Proxmox, por exemplo, tipo de CPU `host`) e instalar
`requirements-vetorizador.txt` na imagem. Mesmo assim será bem mais lento que GPU.
