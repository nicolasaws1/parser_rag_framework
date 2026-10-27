# Vetorizar documentos extraídos

A vetorização segue o mesmo desenho da extração: o site entrega um pacote, quem
tem GPU devolve os vetores, o site grava no Qdrant.

```
site (aba Vetorização) --pacote_….zip--> GPU --vetores_….zip--> site --> Qdrant
```

A máquina com GPU não recebe chave nenhuma, nem do Supabase nem do Qdrant.

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
3. Célula 2: envie o `pacote_….zip`. Rode até a 4, que baixa o `vetores_….zip`.
4. Volte à aba e arraste o `vetores_….zip`. O site mostra, documento por
   documento, o que é novo e o que já está na coleção, e só grava quando você confirma.
   Para regravar quem já está lá, marque a opção: os pontos antigos dele (inclusive
   os dos scripts antigos) são apagados antes.

Sem notebook, num servidor com GPU:

```bash
pip install -r requirements-vetorizador.txt
python vetorizador/rodar_vetorizacao.py pacote_….zip --lote 64
```

Se a sessão cair, rode de novo com o mesmo pacote: o documento que já terminou é pulado.

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
`origem_texto`, `versao_chunker` e os modelos. O id de cada ponto é fixo para
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
