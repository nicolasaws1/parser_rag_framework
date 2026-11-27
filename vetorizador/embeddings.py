# -*- coding: utf-8 -*-
"""Os dois modelos de embedding, com a mesma receita das coleções existentes.

    denso    Qwen/Qwen3-Embedding-0.6B                    -> vetor_denso (1024, cosseno)
    esparso  opensearch-neural-sparse-encoding-doc-v2-distill -> vetor_esparso

Precisa de torch, transformers e sentence-transformers. Roda em GPU (o
destino normal) ou em CPU com AVX. Na CPU do servidor do site, que anuncia só
SSE2, não roda: por isso este arquivo nunca é importado pela API, só pelo
script e por um subprocesso.

Uma diferença deliberada em relação aos scripts antigos, no vetor esparso: eles
completavam o texto com padding até 512 tokens e tiravam o máximo da sequência
inteira, padding incluído, sem o log. Isso deixa os tokens de padding votarem.
Aqui segue a receita do cartão do modelo: máscara de atenção, log(1+relu) e
tokens especiais zerados. Dentro de uma coleção nova não há mistura; numa
coleção antiga, os pontos novos ficam com pesos levemente diferentes dos velhos.
"""
from __future__ import annotations

import base64
import os

# antes de qualquer import do torch: sem isto a memória fragmenta entre lotes de
# tamanhos diferentes e sobra GPU "reservada mas sem uso" (4,7 GB numa L4)
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

MODELO_DENSO = "Qwen/Qwen3-Embedding-0.6B"
MODELO_ESPARSO = "opensearch-project/opensearch-neural-sparse-encoding-doc-v2-distill"
MAX_TOKENS_ESPARSO = 512


class Embedder:
    def __init__(self, dispositivo: str | None = None, lote: int = 32):
        import torch
        from sentence_transformers import SentenceTransformer
        from transformers import AutoModelForMaskedLM, AutoTokenizer

        self.torch = torch
        self.dev = dispositivo or ("cuda" if torch.cuda.is_available() else "cpu")
        self.lote = lote
        # vai para o payload de cada ponto: é o registro de qual modelo gerou o vetor
        self.modelos = {"denso": MODELO_DENSO, "esparso": MODELO_ESPARSO}
        kw = {"torch_dtype": torch.float16} if self.dev == "cuda" else {}
        self._denso = SentenceTransformer(MODELO_DENSO, device=self.dev, model_kwargs=kw)
        self._tok = AutoTokenizer.from_pretrained(MODELO_ESPARSO)
        self._esp = AutoModelForMaskedLM.from_pretrained(MODELO_ESPARSO).to(self.dev).eval()
        self._especiais = list(self._tok.all_special_ids)

    def _sem_estourar(self, fn, textos: list[str]):
        """Chama fn(textos, lote); se a GPU encher, refaz com metade do lote.

        Quem estoura é o grupo dos trechos mais longos: o sentence-transformers
        junta os textos por tamanho, e tabela grande vai inteira (já houve de
        22 mil caracteres). Com 128 delas num lote, uma L4 de 22 GB enche. O
        lote menor vale só para esta chamada: o documento seguinte, sem tabela
        grande, volta ao lote cheio."""
        lote = self.lote
        while True:
            try:
                return fn(textos, lote)
            except self.torch.cuda.OutOfMemoryError:
                self.torch.cuda.empty_cache()
                if lote <= 1:
                    raise
                lote = max(1, lote // 2)
                print(f"      memória da GPU cheia: refazendo este documento com lote {lote}",
                      flush=True)

    def _encode(self, textos: list[str], lote: int):
        return self._denso.encode(textos, batch_size=lote, convert_to_numpy=True,
                                  normalize_embeddings=True, show_progress_bar=False)

    def denso(self, textos: list[str]) -> list[str]:
        """float32 em base64: 4 KB por vetor, e o site lê sem numpy."""
        import numpy as np
        emb = self._sem_estourar(self._encode, textos)
        return [base64.b64encode(np.asarray(e, dtype="<f4").tobytes()).decode() for e in emb]

    def matriz(self, textos: list[str]):
        """Vetores densos normalizados, em numpy: o chunking semantic mede com eles
        a distância entre frases vizinhas."""
        return self._sem_estourar(self._encode, textos)

    def esparso(self, textos: list[str]) -> list[dict]:
        return self._sem_estourar(self._esparso, textos)

    def _esparso(self, textos: list[str], lote: int) -> list[dict]:
        t = self.torch
        fora = []
        for i in range(0, len(textos), lote):
            tk = self._tok(textos[i:i + lote], return_tensors="pt", padding=True,
                           truncation=True, max_length=MAX_TOKENS_ESPARSO).to(self.dev)
            with t.no_grad():
                logits = self._esp(**tk).logits
            # máscara no próprio tensor: lote x 512 x 30 mil em float32 já são ~8 GB
            # com lote 128, e `logits * máscara` criaria outra cópia do mesmo tamanho
            logits.mul_(tk["attention_mask"].unsqueeze(-1))
            v, _ = t.max(logits, dim=1)
            del logits
            v = t.log1p(t.relu(v))
            v[:, self._especiais] = 0
            for linha in v:
                idx = t.nonzero(linha, as_tuple=True)[0]
                vals = linha[idx]
                fora.append({"indices": idx.tolist(), "values": [round(float(x), 5) for x in vals]}
                            if len(idx) else {"indices": [0], "values": [0.0]})
        return fora
