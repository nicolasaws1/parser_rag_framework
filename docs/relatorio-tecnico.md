# Relatório Técnico — Extração híbrida de PDFs científicos para RAG

**Projeto:** parser_rag_framework (SB100 — Squad 2, vetorização cientométrica)
**Autor:** Nicolas
**Escopo:** pipeline de extração multimodal + front de inspeção

---

## 1. Contexto e objetivo

O objetivo é transformar artigos científicos em PDF (domínio agronomia/citros, com texto,
tabelas, figuras, gráficos e fórmulas) em **Markdown estruturado + bounding boxes**, prontos
para *chunking* e vetorização em um RAG. Diferente de um RAG genérico, o corpus é **multimodal**
e **científico**: perder uma equação, uma tabela ou classificar um gráfico como tabela degrada
a qualidade da recuperação.

## 2. Por que um extrator próprio (e não um parser pronto)

Avaliei usar um parser pronto (ex.: LlamaParse). Optei por construir o extrator porque:

- **Controle e afinação:** o parser pronto é caixa-preta; eu precisava tunar comportamento
  (config do OCR, roteamento, classificação) para o padrão dos meus PDFs.
- **Privacidade / infra:** um parser em nuvem exige mandar os PDFs para fora — conflita com a
  definição de armazenamento/segredos do projeto.
- **Custo:** serviços de parsing são pagos por página; o volume previsto (~1000 PDFs/semana)
  torna isso relevante.
- **Qualidade no caso específico:** parsers genéricos erram mais em PDF científico multimodal.

Conclusão: o parser pronto, no máximo, replicaria o que construí — só que na nuvem e sem controle.

## 3. Arquitetura da solução

Extração **por página**, com roteamento híbrido. Cada componente foi escolhido pela força específica:

| Componente | Papel | Por que |
|---|---|---|
| **DocLayout-YOLO** | detecta regiões e **roteia** | melhor detector de layout (tabela/figura/texto); decide a rota da página |
| **Chandra OCR 2** | OCR de visão nas tabelas e figuras | devolve bbox + tipo + `<sup>/<sub>`; venceu nos testes vs Docling em conteúdo |
| **Docling** | ordem de leitura + tabelas nas páginas de texto | fidelidade da camada de texto + reading order |
| **PyMuPDF** | rede de segurança | extração determinística; recupera texto que o Docling dropa |

Fluxo por página:

```
DocLayout-YOLO detecta regiões e ROTEIA:
  ├─ página com tabela ─→ Chandra OCR 2 (página inteira: bbox + tipo + conteúdo)
  └─ senão            ─→ Docling (ordem + tabelas) + PyMuPDF (rede de segurança)

Figuras/gráficos: YOLO decide que é figura → Chandra descreve o recorte
                  (subclasse gráfico/foto + captura equações em LaTeX)

Saída por doc: layout.json + document.md (LaTeX) + pages/ + figures/
```

## 4. Decisões técnicas e por que as tomei

### 4.1 Roteamento por página (OCR só onde agrega)
OCR de visão é caro e lento. Rodá-lo em toda página é desperdício. **Por quê:** rotear para OCR
apenas onde há tabela/figura mantém velocidade e custo baixos nas páginas de texto, sem perder
qualidade onde ela importa.

### 4.2 YOLO manda na classe (tabela × figura); Chandra manda na subclasse
Descobri que, em páginas roteadas ao Chandra, ele **classificava gráficos como tabela** e, pior,
**perdia a equação de regressão** impressa dentro do gráfico (ex.: FIGURA 1 do Boaretto, pág. 5).
**Por quê a decisão:** o YOLO é mais confiável para dizer "isto é figura, não tabela". Então o
YOLO decide a classe; onde ele diz *figura*, o recorte vai pela rota de figura (Chandra no crop),
que preserva a equação e ainda define a subclasse (gráfico/foto). Resultado verificado: a pág. 5
passou de `tabelas:3, figuras:0` (errado) para `tabelas:1, figuras:2` (correto), com as equações
capturadas.

### 4.3 Configuração do Chandra (`no_repeat_ngram_size = 0`)
O Chandra deixava de detectar tabelas em alguns casos. A hipótese inicial (orçamento de tokens)
estava **errada**: o diagnóstico mostrou que `no_repeat_ngram_size = 3` causava parada precoce da
geração. **Por quê:** voltei para `0` (+ filtro anti-repetição), que foi o que funcionou nos testes.

### 4.4 Normalização científica: LaTeX no vetor, unicode no legível
Isótopos, expoentes e fórmulas não podem ser perdidos (`¹⁵N`, `kg⁻¹`, `(NH₄)₂SO₄`, `x²`).
**Por quê a separação:**
- **Legível (humano):** unicode bonito (`¹⁵N`).
- **document.md (vetor/LLM):** **LaTeX** (`^{15}N`, `(NH_{4})_{2}SO_{4}`) — *lossless* e sem
  ambiguidade. Superscritos unicode tokenizam mal em muitos embedders; achatar para ASCII (`15N`,
  `x2`) perde informação (expoente vira ambíguo). LaTeX resolve os dois problemas.

### 4.5 Tabelas em Markdown table
No vetor, tabela como prosa embaralhada é ruim para o LLM. **Por quê:** passei a emitir
**Markdown table** (que o LLM lê nativo) + uma linha-resumo em linguagem natural, que serve de
**âncora semântica** para o embedding (uma tabela de números sozinha não casa com nenhuma query).

### 4.6 Rede de segurança por dedup de TEXTO (não de bbox)
O Docling dropava parágrafos (ex.: pág. 1 do Boaretto). A rede de segurança do PyMuPDF os
recupera, mas o dedup original (por bbox) reintroduzia duplicatas quando o Docling fatiava o
parágrafo. **Por quê:** troquei o dedup para comparar o **texto** contra o texto concatenado do
Docling — assim não perde o parágrafo dropado e não duplica o que já veio.

### 4.7 Legenda em PT (dos autores) nas figuras
O Chandra descreve figuras em **inglês**. **Por quê:** em vez de traduzir, associei a **legenda do
próprio artigo** ("FIGURA 1 - ...") ao bloco da figura — é PT autêntico, de graça, e melhor para
recuperação. As equações e a tabela de dados (neutras de idioma) são preservadas.

### 4.8 Salvar recortes de figura
Cada gráfico/figura é recortado e salvo em `figures/`. **Por quê:** viabiliza recuperação por
imagem no futuro (multimodal), além da citação de origem.

### 4.9 Ordem de leitura ciente de coluna
Layout de 2 colunas quebrava a ordem. **Por quê:** ordenação por coluna (esquerda inteira, depois
direita; blocos full-width como separadores) corrige o zigue-zague.

## 5. O front de inspeção

Um SPA (HTML/CSS/JS, sem build) para inspecionar cada extração **antes** de vetorizar — inspirado
no LlamaParse. **Por quê:** QA humano do parser é essencial num corpus científico. Recursos:
PDF à esquerda (com zoom), abas **Legível / Markdown / Metadados / Código**, destaque "imã"
(hover no bbox acende o texto), adicionar/excluir/editar bloco com sincronia Legível↔Markdown,
campo de página editável, Diagnostics e login (protótipo — auth real fica no backend).

## 6. Validação

- **Pág. 5 (Boaretto):** gráficos corretamente classificados (`figuras:2`), com as equações
  `y = -0,00003x² + 0,024x + 25,1` etc. capturadas.
- **Pág. 1 (Boaretto):** parágrafo antes dropado, recuperado.
- **document.md:** ciência em LaTeX (`^{15}N`, `(^{15}NH_{4})_{2}SO_{4})`).
- **Alva:** 52 páginas processadas (50 Docling + 2 Chandra), 2 tabelas, 7 figuras.

## 7. Decisões sobre frameworks (o que adiei e por quê)

- **LlamaIndex:** o núcleo dele (chunk, embed, retrieve, rerank) **já está feito** no projeto —
  adotá-lo seria reescrever o que funciona. Baixo retorno. Ele não tem OCR próprio; no máximo
  o LlamaParse (nuvem, proprietário). **Decisão:** manter o extrator custom.
- **LangGraph:** não é "RAG mais rápido" — é orquestração de fluxo **agêntico** (self-RAG,
  multi-hop, human-in-the-loop). **Decisão:** só quando o RAG simples deixar de bastar; um bom
  gancho seria um *quality gate* de revisão para extrações de baixa confiança.
- **MLflow:** camada de **avaliação/observabilidade** (medir faithfulness/relevância, comparar
  configs do extrator). **Decisão:** adotar quando for medir qualidade a sério (com Ragas).
- **GraphRAG:** técnica de grafo de conhecimento — alinhada com cientometria (perguntas
  relacionais/globais). **Decisão:** avaliar depois; o difícil não é extrair triplas (o LLM faz),
  é a **resolução de entidade** e a atualização incremental em escala.

## 8. Limitações e próximos passos

- **PDF escaneado:** não há detecção de camada de texto + rota "Chandra no documento inteiro"
  (corpus atual é 100% orgânico; `escaneadas: 0`).
- **Proveniência por bloco:** hoje é por página (`rota`); um `extrator` por trecho ajudaria auditoria.
- **Backend/infra:** armazenamento de PDFs/markdowns/imagens, índice de estado, segredos e login
  real (em definição com a área de banco/infra).
- **Camada de resposta + avaliação:** gerar resposta com citação de volta ao `bbox`/figura e medir
  qualidade (MLflow + Ragas).
