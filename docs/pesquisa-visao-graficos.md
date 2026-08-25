# Interpretação de gráficos e figuras científicas por modelos de visão

**Pesquisa contra fontes primárias.** Model cards oficiais, papers no arXiv/NeurIPS, repositórios e leaderboards dos próprios benchmarks.
**Data da pesquisa:** 25/08/2026.
**Pergunta:** qual a qualidade real do Gemini 3 Pro / 3.1 Pro em figura científica, comparada a humano, e o que isso implica para a meta de 80% do projeto.

Regra que segui em todo o documento: **onde não achei número em fonte primária, está escrito "não encontrado em fonte primária"**. Nenhum número aqui foi inferido, arredondado de blog, ou transportado de uma versão de modelo para outra.

---

## 0. Resposta curta

1. **Gemini 3 Pro tem um único número publicado de gráfico científico: CharXiv Reasoning 81,4%**, contra baseline humano de 80,5% no mesmo conjunto de 1.000 questões. É paridade humana declarada, mas é auto-reportado pelo Google e cobre só a metade "raciocínio" do CharXiv.
2. **Gemini 3.1 Pro NÃO tem número de CharXiv, ChartQA, MathVista, ScreenSpot-Pro nem OmniDocBench publicado.** O model card de fev/2026 reporta apenas MMMU-Pro (80,5%), que é *menor* que o do Gemini 3 Pro (81,0%). Todo número de gráfico que circula atribuído ao "3.1" é, na verdade, do 3 Pro.
3. **A metade que o Google não reporta é justamente a que interessa ao projeto.** CharXiv Descriptive (título, legenda, eixos, ticks, contagem, estrutura de subplots) tem humano em 92,10% e não há número de Gemini 3 Pro publicado nela.
4. **O erro que vocês já observaram localmente é o modo de falha canônico da literatura**: valor numérico razoável, estrutura e legenda erradas. Está documentado no CharXiv (subplots, compositionality) e replicado em ciências da vida (rótulo 72% F1 contra direcionalidade de relação 34% F1).
5. **80% sem revisão humana em figura densa multi-painel não é sustentado por nenhuma evidência primária.** Com revisão humana e métrica separada para valor e para estrutura, é alcançável. Detalhe na seção 9.

---

## 1. Os benchmarks: o que mede cada um e como foi construído

| Benchmark | Gráficos | Como foi construído | Questões | Baseline humano? |
|---|---|---|---|---|
| **ChartQA** (2022) | Reais, 4 fontes (Statista, Pew, OECD, OWID) | 9,6K perguntas escritas por humanos + 23,1K geradas de resumos humanos | ~32,7K | **Não** |
| **CharXiv** (2024) | **Reais, de artigos do arXiv** (2020 a 2023, 8 áreas) | 2.323 gráficos escolhidos a mão por pós-graduandos; toda pergunta e resposta curada e verificada por humano | 9.292 descritivas + 2.323 de raciocínio | **Sim**: 80,50 (raciocínio) / 92,10 (descritiva) |
| **ChartQAPro** (2025) | Reais e diversos (web, Tableau, Pew, PPIC, OWID), inclui infográfico, dashboard e multi-gráfico | 7 anotadores co-autores revisaram; concordância inicial de 66,17% antes de resolver divergências | 1.341 gráficos, 1.948 questões (5 tipos) | **Sim**: 85,02 |
| **SciFIBench** (2024) | **Figuras reais de artigos do arXiv** | 2.000 questões, negativos escolhidos por filtragem adversarial + verificação humana | 2 tarefas (Figura→Legenda, Legenda→Figura) | **Sim**: 86,4 / 78,4 |
| **MMMU** (2024) | Misto (diagramas, tabelas, gráficos, imagens médicas, partituras...) | 50+ estudantes universitários coletaram de livros-texto e web; 30 matérias, 6 disciplinas | 11.550 (900 na validação) | **Sim**: 76,2 / 82,6 / 88,6 |
| **MMMU-Pro** (2024) | Mesmo do MMMU, endurecido | Filtra questões respondíveis só com texto, amplia para 10 alternativas, cria modo "vision-only" (pergunta embutida na imagem) | 1.730 | **Estimado**, não medido: 73,0 / 80,8 / 85,4 |
| **MathVista** (2024) | 28 datasets existentes + 3 novos (IQTest, FunctionQA, PaperQA) | Agregação; anotação de rótulos de raciocínio matemático | 6.141 | **Sim**: 60,3 |
| **ChartBench** (2024) | **Sintéticos**, 42 categorias, muitos **sem anotação de valor** no gráfico | Geração programática; métrica Acc+ (par de asserções binárias) | 66,6K gráficos, 600K pares QA | **Sim**: 88,46 (Acc+) |
| **ChartX** (2024) | **Sintéticos**, gerados a partir de CSV | 18 tipos, 7 tarefas, 22 tópicos; humanos apenas *validam* o pipeline do GPT-4 | 6K gráficos | **Não** |
| **EvoChart-QA** (2024) | Reais, 625 gráficos de 140 sites | Especialistas humanos filtram imagens e escrevem as perguntas | 1.250 | **Não** |
| **OmniDocBench 1.5** | Documentos completos (artigos, livros, manuscritos, jornais) | Anotação humana, 19 categorias de layout, 15 atributos | Métrica: Edit Distance em Texto, Fórmula, Tabela, Ordem de Leitura | **Não** |

**A divisão que importa para o projeto:**

- **Sintético** (ChartQA em boa parte, ChartBench, ChartX): gráfico limpo, um painel, valor frequentemente anotado no próprio gráfico. O ChartBench argumenta explicitamente que a anotação de valor no gráfico do ChartQA "induz o modelo a resolver por OCR em vez de inferência visual".
- **Real de artigo científico** (CharXiv, SciFIBench): multi-painel, legenda compartilhada entre subplots, eixo denso, tipos misturados no mesmo gráfico. É este o regime dos PDFs de citros e cana.

O CharXiv registra que 37,7% dos seus gráficos têm 2 a 4 subplots e 23,7% têm 5 ou mais. Só 28,6% são de painel único.

---

## 2. Números do Gemini 3 Pro e do Gemini 3.1 Pro

### 2.1 Gemini 3 Pro (fonte: model card oficial)

Fonte: [Gemini 3 Pro Model Card](https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Pro-Model-Card.pdf) (lançamento nov/2025, card atualizado em mai/2026). O card diz literalmente: "Results as of November, 2025".

Métrica: pass@1, tentativa única, sem votação majoritária, via API `gemini-3-pro-preview`.

| Benchmark | Gemini 3 Pro | Gemini 2.5 Pro | Claude Sonnet 4.5 | GPT-5.1 |
|---|---|---|---|---|
| **CharXiv Reasoning** | **81,4%** | 69,6% | 68,5% | 69,5% |
| MMMU-Pro | **81,0%** | 68,0% | 68,0% | 76,0% |
| ScreenSpot-Pro | **72,7%** | 11,4% | 36,2% | 3,5% |
| **OmniDocBench 1.5** (Edit Distance, *menor é melhor*) | **0,115** | 0,145 | 0,145 | 0,147 |
| Video-MMMU | **87,6%** | 83,6% | 77,8% | 80,4% |

Detalhes de metodologia, do [PDF de avaliação do Gemini 3 Pro](https://storage.googleapis.com/deepmind-media/gemini/gemini_3_pro_model_evaluation.pdf):

- **CharXiv**: "results are on 1000 reasoning questions from the validation split of CharXiv". Isto é importante: **é exatamente o mesmo conjunto onde o baseline humano de 80,5% foi medido.** A comparação é direta.
- **MMMU-Pro**: média entre o modo Standard (10 alternativas) e o modo Vision.
- **ScreenSpot-Pro**: usa `media_resolution="extra_high"` e uma ferramenta de captura de tela. Com `"high"`, o Gemini 3 cai para 60,5.
- **OmniDocBench 1.5**: média do Edit Distance em Texto, Fórmula, Tabela e Ordem de Leitura, seguindo a metodologia do DeepSeek-OCR.
- **Quem calculou**: o Google. O PDF diz que MMMU-Pro, ScreenSpot-Pro, CharXiv Reasoning, OmniDocBench 1.5, Video-MMMU, MMMLU e Global PIQA foram computados pelo próprio Google via API dos provedores, porque não havia número auto-reportado nem de leaderboard oficial. **Nenhum desses números foi verificado por terceiro.**

### 2.2 Gemini 3.1 Pro: o que NÃO existe

Fontes: [Model Card do Gemini 3.1 Pro](https://deepmind.google/models/model-cards/gemini-3-1-pro/) e [PDF de avaliação do Gemini 3.1 Pro](https://storage.googleapis.com/deepmind-media/gemini/gemini_3-1_pro_model_evaluation.pdf), publicados em fevereiro de 2026. Resultados declarados "as of February, 2026".

**Não há número publicado de Gemini 3.1 Pro em CharXiv, ChartQA, MathVista, ScreenSpot-Pro ou OmniDocBench.** Verifiquei o model card, o PDF de metodologia e a [página do produto](https://deepmind.google/models/gemini/pro/). O único benchmark de imagem na tabela do 3.1 Pro é o MMMU-Pro. A seção "Image" da metodologia do 3.1 Pro contém uma única linha, sobre MMMU-Pro, enquanto a do 3 Pro tinha quatro linhas.

O único número multimodal do 3.1 Pro:

| Benchmark | Gemini 3.1 Pro | Gemini 3 Pro | Sonnet 4.6 | Opus 4.6 | GPT-5.2 |
|---|---|---|---|---|---|
| MMMU-Pro | 80,5% | **81,0%** | 74,5% | 73,9% | 79,5% |

Repare: **o 3.1 Pro é 0,5 ponto pior que o 3 Pro no único benchmark multimodal comparável.** O salto do 3.1 foi em raciocínio abstrato (ARC-AGI-2 de 31,1% para 77,1%), agente e código, não em visão.

**Conclusão prática:** se a decisão do projeto depende de gráfico, o número que vale é o do **Gemini 3 Pro**, de novembro de 2025. Atribuir 81,4% de CharXiv ao "3.1 Pro" seria inventar.

### 2.3 O leaderboard oficial do CharXiv está parado

Fonte: [repositório princeton-nlp/CharXiv](https://github.com/princeton-nlp/CharXiv). Última atualização do leaderboard: **25/12/2024**. Última notícia no repositório: 14/04/2025. Versão corrente: v1.0.

Topo do leaderboard oficial hoje:

| Modelo | Reasoning | Descriptive |
|---|---|---|
| 🎖️ **Humano** | **80,50** | **92,10** |
| 🥇 Claude 3.5 Sonnet | 60,20 | 84,30 |
| 🥈 GPT-4o | 47,10 | 84,45 |
| 🥉 Gemini 1.5 Pro | 43,30 | 71,97 |

**Não há entrada de Gemini 3, GPT-5 ou Claude 4.x no leaderboard oficial.** O 81,4% existe apenas no material do Google. O mesmo vale para o [SciFIBench](https://scifibench.github.io/), cujo leaderboard ainda tem GPT-4o no topo.

---

## 3. O baseline humano de cada benchmark

### CharXiv (o mais relevante para o projeto)

| Categoria | Humano | GPT-4o | InternVL Chat V1.5 |
|---|---|---|---|
| **Raciocínio, geral** | **80,50** | 47,10 | 29,20 |
| Texto no gráfico | 77,27 | 50,00 | 30,00 |
| Texto em geral | 77,78 | 61,62 | 45,45 |
| **Número no gráfico** | **84,91** | 47,84 | 32,33 |
| **Número em geral** | **83,41** | 34,50 | 17,47 |
| **Descritiva, geral** | **92,10** | 84,45 | 58,50 |
| Extração de informação | 91,40 | 82,44 | 69,63 |
| Enumeração | 91,20 | 89,18 | 52,95 |
| Reconhecimento de padrão | 95,63 | 90,17 | 53,06 |
| Contagem | 93,38 | 85,50 | 64,63 |
| **Composicionalidade** (contar ticks rotulados) | **92,86** | 59,82 | 5,80 |

*Data de corte da tabela: 12/06/2024.*

**Como foi medido:** participantes humanos internos ("in-house"), recebendo exatamente o mesmo prompt e as mesmas instruções dos modelos, com as respostas avaliadas pelo mesmo corretor. O paper declara na checklist do NeurIPS: "We did not recruit external human subjects in our study".

**Limitação:** o paper **não informa quantos participantes foram**, nem a qualificação deles, nem concordância entre anotadores para o baseline. Não encontrado em fonte primária.

### SciFIBench

| | Figura→Legenda | Legenda→Figura |
|---|---|---|
| **Humano (µ±σ)** | **86,4 ± 8,24** | **78,4 ± 8,24** |
| GPT-4o (mesmo subconjunto) | 72,0 | 76,0 |
| Gemini 1.5 Pro (mesmo subconjunto) | 84,0 | 72,0 |

**Como foi medido:** 25 questões por tarefa, média de **5 participantes**, estudantes de graduação e pós-graduação, **não necessariamente especialistas de domínio**, com o mesmo prompt dos modelos. Os autores registram um viés honesto: a etapa de verificação humana durante a curadoria pode ter enviesado o conjunto na direção de perguntas mais fáceis para humanos.

### MMMU

| | Validação (900 questões) |
|---|---|
| Expert (pior) | 76,2 |
| Expert (mediano) | 82,6 |
| **Expert (melhor)** | **88,6** |

**Como foi medido:** 90 estudantes de último ano de graduação, **3 por matéria em 30 matérias**, respondendo as 30 questões da sua própria matéria. **Podiam consultar seus livros-texto.**

### MMMU-Pro

| | Standard (10 opções) e Vision |
|---|---|
| Humano (baixo) | 73,0 |
| Humano (médio) | 80,8 |
| Humano (alto) | 85,4 |

**Atenção:** este baseline é uma **estimativa derivada dos dados humanos do MMMU original**, não uma nova medição. Os autores dizem explicitamente que não conduziram avaliação humana nova por custo e tempo, e justificam a extrapolação pelo fato de o conteúdo das questões não ter mudado.

Consequência: o "81,0% do Gemini 3 Pro supera o humano mediano de 80,8%" é verdadeiro na letra, mas compara um número medido contra um número extrapolado.

### MathVista

Humano: **60,3%**. Medido via Amazon Mechanical Turk, anotadores com histórico satisfatório, aprovados em exemplos de qualificação, com ensino médio completo ou superior, respondendo 5 questões em até 20 minutos. (O Fleiss Kappa de 0,775 citado no paper refere-se à concordância na rotulagem de "raciocínio matemático" entre 3 anotadores, **não** ao baseline humano de desempenho.)

### ChartQAPro

Humano geral: **85,02** (Factoid 80,00 / Múltipla escolha 94,00 / Conversacional 88,70 / Fact-checking 92,00 / Hipotético 70,42).

**Como foi medido:** **um único** estudante de pós-graduação interno, respondendo 50 questões amostradas de cada categoria, com o mesmo prompt dos modelos. É o baseline humano mais frágil da lista, e os autores o descrevem como aproximação de limite superior.

### ChartBench

Humano geral: **88,46 Acc+**. Por tarefa: Reconhecimento de gráfico 93,68 / **Extração de valor 84,56** / Comparação de valor 88,68 / Cálculo global 86,91.

**Como foi medido:** questionário online, 10 subcategorias sorteadas por questionário, 1 gráfico e 4 asserções por subcategoria, **68 questionários válidos**, respondentes majoritariamente estudantes de graduação e pós e pesquisadores. Tempo médio de 15 min 17 s.

Comparação na mesma tabela: GPT-4V em Extração de Valor = **29,27** contra 84,56 do humano. É a maior distância da tabela e é a tarefa "ler o valor de um ponto que não está rotulado no gráfico".

### Sem baseline humano publicado

- **ChartQA**: não há. O paper reporta concordância entre crowd workers na anotação (61,04% por casamento exato, 78,55% ao tolerar erros de digitação e variações lexicais), que é medida de qualidade da anotação, não de desempenho humano na tarefa.
- **ChartX**: não há. Humanos só validaram a saída do GPT-4 (taxa de detecção de erro abaixo de 1%).
- **EvoChart-QA**: não há. Humanos selecionaram gráficos e escreveram perguntas. O melhor modelo do paper, GPT-4o, faz 49,8%.
- **OmniDocBench**: não há baseline humano reportado.

---

## 4. A queda entre regimes, quantificada

### 4.1 Mesmo modelo, três regimes (a evidência mais limpa)

Fonte: paper do ChartQAPro, Tabela 10. Claude Sonnet 3.5, mesma data:

| Benchmark | Regime | Acurácia |
|---|---|---|
| ChartQA | Gráfico simples, 4 fontes | **90,50%** |
| **CharXiv** | **Gráfico científico real do arXiv** | **60,20%** |
| ChartQAPro | Real e diverso, infográfico e dashboard | **55,81%** |

**Queda de 30,3 pontos de ChartQA para CharXiv.** É o número que responde diretamente à pergunta 4.

### 4.2 A mesma queda vista pelo lado do CharXiv

O CharXiv mostra que, em 174 questões de DVQA, FigureQA e ChartQA (subconjuntos do testmini do MathVista), vários modelos open-source **superavam** os proprietários por uma diferença de 0,58 ponto. Nas 1.000 questões de raciocínio do split de validação do CharXiv, a diferença inverte e vira **17,9 pontos** a favor dos proprietários. A conclusão do paper é que os benchmarks sintéticos estavam medindo outra coisa.

### 4.3 Fragilidade a perturbação

Teste de estresse do CharXiv: **SPHINX V2 cai de 63,2% para 28,6%** quando a pergunta é levemente modificada sobre o mesmo conjunto de gráficos. Queda de **34,5 pontos** sem trocar a imagem.

### 4.4 Onde o Gemini 3 Pro se encaixa

| Regime | Melhor número primário | Humano |
|---|---|---|
| Gráfico simples (ChartQA) | não encontrado em fonte primária para Gemini 3 Pro; Claude Sonnet 3.5 fazia 90,50% em 2025 | sem baseline |
| **Gráfico científico real, raciocínio (CharXiv Reasoning)** | **Gemini 3 Pro: 81,4%** (nov/2025, auto-reportado) | **80,50%** |
| **Gráfico científico real, descrição/estrutura (CharXiv Descriptive)** | **não encontrado em fonte primária para Gemini 3 Pro** | **92,10%** |
| Figura de artigo, casar figura e legenda (SciFIBench) | não encontrado em fonte primária para Gemini 3 Pro; GPT-4o 73,8 / 65,4 em 2024 | 86,4 / 78,4 |
| Parsing de documento (OmniDocBench 1.5) | Gemini 3 Pro: 0,115 de Edit Distance | sem baseline |

A lacuna do CharXiv Descriptive é a mais incômoda para este projeto e está registrada na seção 8.

---

## 5. Modos de erro documentados nos papers

### 5.1 Leitura de valor numérico contra interpretação de estrutura

O CharXiv separa as respostas de raciocínio por tipo, e o resultado inverte o perfil humano:

| Tipo de resposta | Humano | GPT-4o | Diferença |
|---|---|---|---|
| Texto no gráfico | 77,27 | 50,00 | -27,3 |
| Texto em geral | 77,78 | 61,62 | -16,2 |
| Número no gráfico | 84,91 | 47,84 | **-37,1** |
| Número em geral | 83,41 | 34,50 | **-48,9** |

**O humano é melhor em número do que em texto. O modelo é pior em número do que em texto.** A distância modelo-humano em "número em geral" (valor que exige derivar do gráfico, não ler um rótulo) é quase três vezes a distância em "texto em geral".

Isso é relevante para o caso local de vocês: o Chandra acertar três percentuais de variância de PCA é consistente com "ler rótulo impresso" (OCR), que é a tarefa fácil, e não com "derivar valor do gráfico". A repetição do percentual da linha anterior é o outro fenômeno, descrito abaixo.

### 5.2 Alucinação de número plausível quando a pergunta não é respondível

O CharXiv é o primeiro benchmark de gráfico a incluir perguntas deliberadamente não respondíveis: **25% das perguntas descritivas** pedem informação que não existe naquele subplot. A resposta correta é "Not Applicable".

Exemplo textual do paper, sobre a diferença entre valores consecutivos de tick no eixo x, quando o eixo é categórico e a resposta correta é "Not Applicable":

- GPT-4o: responde "20".
- Claude 3 Sonnet: identifica corretamente que os rótulos são texto, não valores numéricos, e responde "Not Applicable".
- Reka Core: descreve o eixo como indo "de 0 a 100", com ticks igualmente espaçados, e conclui que a diferença é 20. Inventa o eixo inteiro.

O paper mede isso e observa que **modelos que ficam abaixo de 80% em perguntas não respondíveis exibem padrões idiossincráticos de falha**: o IDEFICS 2 Chatty erra quase 90% das não respondíveis sobre título e rótulos de eixo, mas acerta mais de 90% das que perguntam sobre interseção de linhas e presença de legenda.

Para um RAG, este é o pior erro possível: um número plausível entra no índice como fato e não há sinal de baixa confiança.

### 5.3 Múltiplos painéis

Análise dedicada do CharXiv (Figura 6b): com **6 ou mais subplots**, o desempenho em perguntas descritivas cai **de 10 a 30 pontos nos modelos proprietários** e **de 30 a 50 pontos nos open-source**.

A hipótese dos autores é que os open-source foram ajustados em datasets de gráfico sem subplots (DVQA, ChartQA), e por isso quebram mais.

Dois detalhes finos:

- Não há correlação clara entre número de subplots e capacidade de **raciocínio**. A degradação é na parte **descritiva**, ou seja, exatamente em identificar o painel certo, a legenda certa, o eixo certo.
- Quando os elementos básicos (legenda, eixo, título) são **compartilhados entre subplots**, o modelo precisa entender a relação entre os painéis para extrair a informação correta. É este caso que produz o erro de "atribuir ao painel A a legenda do painel B".

Isto casa exatamente com o que vocês observaram: o extrator repetiu um percentual da linha anterior.

### 5.4 Sensibilidade a densidade de rótulos

A tarefa descritiva que mais separa humano de modelo no CharXiv é **contar quantos ticks rotulados existem no eixo x e no eixo y**:

| | Acurácia |
|---|---|
| Humano | **92,86** |
| GPT-4o (melhor proprietário) | 59,82 |
| InternVL Chat V1.5 (melhor open-source) | 5,80 |
| Baseline aleatório | 5,35 |

**20 de 24 modelos avaliados ficaram abaixo de 10%**, ou seja, no nível do acaso. Contar é trivial para humano e é onde a visão dos modelos colapsa.

### 5.5 Ler valor de ponto não anotado

O ChartBench isola isso na tarefa "Value Extraction", com gráficos deliberadamente **sem anotação de valor**, obrigando o modelo a inferir de cor, legenda e sistema de coordenadas:

| | Value Extraction |
|---|---|
| Humano | 84,56 |
| GPT-4V | 29,27 |
| InternLM-XComposer-v2 | 36,63 |

Os próprios autores do ChartBench registram que os humanos também acham essa a tarefa mais difícil ("o olho humano tem dificuldade em determinar o valor de pontos não marcados"), o que explica o 84,56 em vez de 90+.

### 5.6 Cadeia de raciocínio longa piora quando a percepção falha

O CharXiv observa que respostas mais longas (com mais traço de chain-of-thought) **prejudicam** o desempenho em raciocínio nos modelos com baixa acurácia descritiva (MoAI 28,70% e Qwen VL Plus 28,93% em descritiva), e **ajudam** nos com acurácia descritiva mais alta (Mini-Gemini HD Yi 34B 52,68%, Reka Flash 56,45%). A hipótese dos autores: sem entendimento visual básico, o CoT só amplifica o erro inicial.

Implicação de engenharia: pedir para o modelo "explicar o gráfico passo a passo" não conserta erro de percepção. Piora.

### 5.7 Erros por disciplina

Todos os modelos avaliados pelo CharXiv mostram capacidade descritiva consistentemente **mais fraca em gráficos de física** e mais forte em engenharia elétrica, finanças quantitativas e áreas correlatas. Não há corte específico para biologia quantitativa publicado no texto principal, embora "Quant. Biology" seja uma das 8 áreas (293 gráficos, 12,6% do conjunto).

---

## 6. Modelo contra anotador humano em figuras de artigos reais

Esta é a seção com menos material primário disponível. Encontrei quatro trabalhos, nenhum em agronomia de citros ou cana.

### 6.1 Ciências da vida: anotação de figuras de artigos de revisão sobre senescência celular

Fonte: *Genomics & Informatics* 2024;22:7, publicado em 17/06/2024. [PMC11800539](https://pmc.ncbi.nlm.nih.gov/articles/PMC11800539/)

Tarefa: GPT-4V e GPT-4 Turbo anotando **nove figuras manualmente anotadas de quatro artigos de revisão**, extraindo entidades, classificando tipo de nó e identificando relações causais e mecanismos regulatórios.

| Tarefa | Resultado |
|---|---|
| Extração de rótulo | **72% F1** (precisão 0,76 / revocação 0,69) |
| Classificação de tipo de nó | **acima de 80%** |
| **Direcionalidade da relação** | **34% F1** (precisão 0,46 / revocação 0,28) |
| Direcionalidade, só diagramas com menos de 30 nós | 64% F1 |
| Regulação positiva ou negativa | 82% (85% positiva, 66% negativa) |

Modos de erro relatados:
- Confunde categorias parecidas (molécula contra processo; citocinas classificadas como processos).
- Erra ao reconhecer relação inibitória representada pelo símbolo `-|`.
- Acurácia cai conforme a complexidade do diagrama cresce.
- **Inversões de fonte e alvo mesmo em diagramas simples.**

**Este é o padrão exato do problema de vocês, em outro domínio: extrair o rótulo funciona (72%), entender a estrutura que liga os rótulos não funciona (34%).**

Limitação da fonte: apenas 9 figuras, modelo de 2024, e o paper **não informa quantos curadores humanos** fizeram a anotação de referência nem concordância entre eles.

### 6.2 Materiais: extração de dados quantitativos de figuras com Gemini

Fonte: [arXiv 2606.00065](https://arxiv.org/abs/2606.00065), submetido em 19/05/2026. ComProScanner com extração de figura por VLM.

Corpus: 50 artigos de cerâmicas piezoelétricas do conjunto de teste de d33; 48 renderam dados avaliáveis.

| Modelo | Acurácia de composição | F1 normalizado |
|---|---|---|
| **Gemini-3-Flash-Preview** | **0,97** | **0,97** |
| Gemini-2.5-Pro | 0,86 | 0,84 |
| GPT-5.1 | 0,78 | 0,72 |
| GPT-5-Chat-Latest | 0,78 | 0,71 |

Ressalvas que mudam a leitura do 0,97:

- É **Gemini 3 Flash**, não Pro. Os modelos foram escolhidos por um critério de custo abaixo de US$ 1,50 por milhão de tokens de entrada, o que exclui o Pro.
- A acurácia de 0,97 é da **composição química** (a string da fórmula), avaliada por similaridade semântica com limiar 1,0.
- O **valor numérico** (d33) foi avaliado com **tolerância de faixa** de ±0,5, ±1 e ±2 pC/N conforme a magnitude, não por casamento exato. Os autores introduzem esse parâmetro justamente por reconhecer que o casamento exato de valor lido de figura é inviável.
- A referência de verdade vem do corpus curado no trabalho anterior; o detalhamento de quantos curadores humanos e concordância **não é repetido neste paper**.

Ou seja: 0,97 é um número forte, mas é de identificação de entidade química com crédito parcial no valor, não de leitura exata de valor numérico em figura.

### 6.3 Figuras de artigos em geral: SciFIBench

Já coberto nas seções 1 e 3. É o único benchmark que compara **diretamente** modelo contra participante humano na mesma amostra de figuras de artigos do arXiv, com o mesmo prompt. Humano 86,4 / 78,4; GPT-4o na mesma amostra 72,0 / 76,0. As figuras são majoritariamente de ciência da computação e categorias gerais do arXiv, **não de ciências da vida**.

### 6.4 Agronomia: AgroBench

Fonte: [arXiv 2507.20519](https://arxiv.org/abs/2507.20519), ICCV 2025.

Único benchmark de VLM em agricultura anotado por **agrônomos especialistas**, cobrindo 203 categorias de cultura e 682 de doença, sete tópicos. Achado principal: os VLMs têm espaço para melhorar em identificação de granularidade fina, e em identificação de planta daninha a maioria dos VLMs open-source fica perto do acaso.

**Mas não serve para a decisão deste projeto**: AgroBench avalia **fotografias** de plantas, doenças e pragas, não figuras, gráficos ou tabelas de artigos. Baseline humano de acurácia: não localizado em fonte primária.

### 6.5 O que não existe

**Não encontrei nenhum estudo primário comparando um modelo de fronteira contra curador humano na interpretação de figuras de artigos de agronomia**, muito menos de citros ou cana. A lacuna é real e não foi preenchida por suposição.

---

## 7. Comparação lado a lado, na mesma data

Para a pergunta 2, os números de outros modelos de fronteira no mesmo benchmark e na mesma data já estão nas tabelas da seção 2.1 (nov/2025, todos computados pelo Google) e 2.2 (fev/2026). Repetindo o essencial de gráfico:

**CharXiv Reasoning, novembro de 2025, todos medidos pelo Google nas mesmas 1.000 questões de validação:**

| Modelo | CharXiv Reasoning |
|---|---|
| Humano (baseline do benchmark, 2024) | 80,50 |
| **Gemini 3 Pro** | **81,4** |
| Gemini 2.5 Pro | 69,6 |
| GPT-5.1 | 69,5 |
| Claude Sonnet 4.5 | 68,5 |
| Claude 3.5 Sonnet (leaderboard oficial, dez/2024) | 60,20 |
| GPT-4o (paper original, jun/2024) | 47,10 |

Observação sobre a evolução: de jun/2024 (47,1) a nov/2025 (81,4) são 34,3 pontos em 17 meses. Ganho real e grande. Mas os últimos três pontos (de 78 para 81) são os que cruzam o baseline humano, e são também os menos verificados.

---

## 8. Lacunas: o que eu não achei em fonte primária

Ordenadas por quanto pesam na decisão.

1. **CharXiv Descriptive do Gemini 3 Pro.** O Google reporta só a metade "Reasoning". A metade descritiva (título, legenda, eixos, contagem de ticks, estrutura de subplots) é a que corresponde ao trabalho do extrator de vocês, e tem humano em 92,10%. **Não encontrado em fonte primária.**
2. **Qualquer número de gráfico do Gemini 3.1 Pro.** CharXiv, ChartQA, MathVista, ScreenSpot-Pro, OmniDocBench: nenhum. Só MMMU-Pro (80,5%).
3. **ChartQA do Gemini 3 Pro.** Não está no model card nem no PDF de metodologia. **Não encontrado em fonte primária.** Isso significa que a "ponta fácil" da queda entre regimes não pode ser quantificada com o Gemini 3 Pro; usei o Claude Sonnet 3.5 como proxy (seção 4.1), com a data explícita.
4. **MathVista do Gemini 3 Pro.** Não encontrado em fonte primária.
5. **SciFIBench, ChartQAPro, ChartBench, ChartX e EvoChart do Gemini 3 Pro ou 3.1 Pro.** Nenhum. Nenhum desses leaderboards foi atualizado com modelos de 2025 ou 2026.
6. **Verificação independente do 81,4%.** O leaderboard oficial do CharXiv está parado em 25/12/2024 e não tem entrada de Gemini 3. O número é auto-computado pelo Google, sem verificação de terceiro, ao contrário do ARC-AGI-2 (que é "ARC Prize Verified") ou do Terminal-Bench (leaderboard público).
7. **Número de participantes e concordância no baseline humano do CharXiv.** O paper diz apenas "in-house human participants" e confirma que não houve sujeitos externos. Sem n, sem qualificação, sem kappa.
8. **Baseline humano medido para o MMMU-Pro.** O publicado é extrapolação do MMMU original, declarada como tal pelos autores.
9. **Baseline humano de ChartQA, ChartX, EvoChart-QA e OmniDocBench.** Não existem.
10. **Estudo primário de VLM contra curador humano em figuras de artigos de agronomia** (citros, cana, ou culturas perenes em geral). Não existe até onde a busca alcançou.
11. **Número primário para os três tipos de figura específicos do projeto**: PCA multi-painel, dispersão com regressão e R², barras com barra de erro. O CharXiv informa que "Error Bar Plot" e "Scatter Plot" estão entre seus tipos de gráfico, e reporta desempenho por número de subplots e por disciplina, mas **não** publica corte por tipo de gráfico cruzado com modelo. Não encontrado em fonte primária.

---

## 9. Implicação prática para a decisão

### 9.1 O que esperar de acurácia em figura científica densa

O melhor número público, para o melhor modelo, no benchmark mais próximo do seu caso, é **81,4% em raciocínio sobre gráficos reais de artigos do arXiv**, auto-reportado pelo Google em novembro de 2025. Contra 80,5% de humano.

Três ajustes para baixo antes de usar esse número como previsão:

1. **O CharXiv Reasoning não é a sua tarefa.** Sua tarefa é mais parecida com o CharXiv Descriptive somado à extração de valor do ChartBench. Na descritiva, o humano faz 92,10% e o Google não publicou o número do Gemini 3 Pro. Na extração de valor não anotado, a última medição pública de um modelo de fronteira (GPT-4V) foi 29,27% contra 84,56% humano.
2. **Suas figuras estão no pior quartil do CharXiv.** PCA multi-painel é exatamente o caso de "legenda e eixo compartilhados entre subplots" onde a degradação documentada é de 10 a 30 pontos nos modelos proprietários com 6 ou mais painéis.
3. **A métrica do CharXiv é resposta curta com corretor automático.** A sua métrica é "um curador humano leu a saída e disse que está certa", que é mais rigorosa, porque pega o erro de legenda que uma resposta curta não expõe.

Faixa realista de expectativa, marcando claramente que é **minha inferência e não um número de fonte primária**: em figura científica densa e multi-painel, com curador humano julgando estrutura *e* valor juntos, esperar algo na casa dos 60% a 75% por figura completa, com o componente "valor numérico" bem acima disso e o componente "estrutura e legenda" bem abaixo.

### 9.2 O 80% é alcançável sem revisão humana?

**Não, com a evidência disponível.** As razões, cada uma com fonte:

- O único número que passa de 80% em figura científica real cobre raciocínio, não descrição estrutural, e é auto-reportado sem verificação independente (seção 2.3).
- O modo de falha que vocês já observaram (valor certo, estrutura e legenda erradas) é o modo de falha documentado e persistente, replicado em outro domínio de ciências da vida com números duros: 72% F1 em rótulo contra 34% F1 em estrutura (seção 6.1).
- A alucinação de número plausível em pergunta não respondível é documentada e **não vem com sinal de baixa confiança**. Num RAG, um número inventado indexado é indistinguível de um número correto até alguém consultar (seção 5.2).
- Pedir raciocínio explícito ao modelo não corrige erro de percepção, e piora quando a percepção base é fraca (seção 5.6).

**Com revisão humana, 80% é alcançável e provavelmente já é o patamar.** A questão passa a ser onde alocar o curador, não se ele é necessário.

### 9.3 Vale trocar o Chandra pelo Gemini?

O que a evidência sustenta:

- **O ganho esperado não é em OCR de valor.** É onde o Chandra já acerta, e é a tarefa que os benchmarks mostram ser a mais fácil quando o valor está impresso no gráfico. Trocar por causa disso não se justifica.
- **O ganho esperado é em estrutura, legenda e relação entre painéis.** O Gemini 3 Pro é o único modelo com número publicado acima do humano em raciocínio de gráfico científico, e lidera OmniDocBench 1.5 com folga (0,115 contra 0,145 de Gemini 2.5 Pro e Claude Sonnet 4.5, e 0,147 de GPT-5.1), que é a métrica mais próxima do pipeline de parsing de documento de vocês. Isso é evidência a favor, mas indireta.
- **Nenhum número publicado cobre o caso específico.** Por isso a decisão não deveria ser tomada por benchmark.

**Recomendação:** medir localmente antes de trocar, com duas mudanças no protocolo atual.

1. **Separar a métrica em duas.** "Acurácia de valor numérico" e "acurácia de estrutura, legenda e eixo", contadas em separado. Uma métrica única de 80% esconde exatamente o erro que vocês já viram, porque uma figura com três valores certos e uma legenda errada pode passar de 75%.
2. **Rotular um conjunto próprio de 100 a 200 figuras** dos seus PDFs de citros e cana, com **dois curadores independentes** e concordância reportada. Nenhum dos benchmarks públicos tem PCA de safra agronômica, e todos os baselines humanos citados aqui têm n pequeno ou não informado. Um conjunto próprio de 150 figuras com dois anotadores é metodologicamente mais forte que a maioria dos baselines desta pesquisa.

**Teste barato de sanidade antes disso:** o split de validação do CharXiv é público no [HuggingFace](https://huggingface.co/datasets/princeton-nlp/CharXiv) (1.000 gráficos, 1.000 questões de raciocínio e 4.000 descritivas, com respostas liberadas). Rodar o Gemini que vocês pretendem usar contra ele custa pouco e produz **o seu próprio número**, incluindo o Descriptive que o Google não publicou. Isso substitui a confiança no auto-reporte por medição.

**Política operacional sugerida enquanto isso:** revisão humana obrigatória em figura com mais de 2 painéis ou com eixo gêmeo, e amostragem em painel único. É onde a literatura localiza a queda, e concentrar o curador ali é mais barato que revisar tudo.

---

## Fontes

Todas consultadas em 25/08/2026.

**Model cards e avaliações oficiais**
- [Gemini 3 Pro Model Card](https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Pro-Model-Card.pdf) (lançamento nov/2025, atualizado mai/2026)
- [Gemini 3 Pro, Model Evaluation: Approach, Methodology & Results](https://storage.googleapis.com/deepmind-media/gemini/gemini_3_pro_model_evaluation.pdf)
- [Gemini 3.1 Pro Model Card](https://deepmind.google/models/model-cards/gemini-3-1-pro/) (fev/2026)
- [Gemini 3.1 Pro, Model Evaluation: Approach, Methodology & Results](https://storage.googleapis.com/deepmind-media/gemini/gemini_3-1_pro_model_evaluation.pdf) (fev/2026)
- [Gemini 3.1 Pro, página do produto](https://deepmind.google/models/gemini/pro/)

**Papers dos benchmarks**
- CharXiv: [arXiv 2406.18521](https://arxiv.org/abs/2406.18521) / [NeurIPS 2024 D&B](https://proceedings.neurips.cc/paper_files/paper/2024/file/cdf6f8e9fd9aeaf79b6024caec24f15b-Paper-Datasets_and_Benchmarks_Track.pdf) / [repositório](https://github.com/princeton-nlp/CharXiv) / [dataset](https://huggingface.co/datasets/princeton-nlp/CharXiv) / [site](https://charxiv.github.io/)
- ChartQA: [arXiv 2203.10244](https://arxiv.org/abs/2203.10244)
- ChartQAPro: [arXiv 2504.05506](https://arxiv.org/abs/2504.05506)
- SciFIBench: [arXiv 2405.08807](https://arxiv.org/abs/2405.08807) / [site](https://scifibench.github.io/)
- MMMU: [arXiv 2311.16502](https://arxiv.org/abs/2311.16502)
- MMMU-Pro: [arXiv 2409.02813](https://arxiv.org/abs/2409.02813)
- MathVista: [arXiv 2310.02255](https://arxiv.org/abs/2310.02255)
- ChartBench: [arXiv 2312.15915](https://arxiv.org/abs/2312.15915)
- ChartX: [arXiv 2402.12185](https://arxiv.org/abs/2402.12185)
- EvoChart: [arXiv 2409.01577](https://arxiv.org/abs/2409.01577)
- OmniDocBench: [arXiv 2412.07626](https://arxiv.org/abs/2412.07626)

**Estudos de modelo contra anotador humano**
- Anotação de figuras de senescência celular: *Genomics & Informatics* 2024;22:7, [PMC11800539](https://pmc.ncbi.nlm.nih.gov/articles/PMC11800539/)
- ComProScanner com VLM: [arXiv 2606.00065](https://arxiv.org/abs/2606.00065)
- AgroBench: [arXiv 2507.20519](https://arxiv.org/abs/2507.20519) (ICCV 2025)
