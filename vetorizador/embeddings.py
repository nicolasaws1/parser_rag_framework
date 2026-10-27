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
        kw = {"torch_dtype": torch.float16} if self.dev == "cuda" else {}
        self._denso = SentenceTransformer(MODELO_DENSO, device=self.dev, model_kwargs=kw)
        self._tok = AutoTokenizer.from_pretrained(MODELO_ESPARSO)
        self._esp = AutoModelForMaskedLM.from_pretrained(MODELO_ESPARSO).to(self.dev).eval()
        self._especiais = list(self._tok.all_special_ids)

    def denso(self, textos: list[str]) -> list[str]:
        """float32 em base64: 4 KB por vetor, e o site lê sem numpy."""
        import numpy as np
        emb = self._denso.encode(textos, batch_size=self.lote, convert_to_numpy=True,
                                 normalize_embeddings=True, show_progress_bar=False)
        return [base64.b64encode(np.asarray(e, dtype="<f4").tobytes()).decode() for e in emb]

    def esparso(self, textos: list[str]) -> list[dict]:
        t = self.torch
        fora = []
        for i in range(0, len(textos), self.lote):
            tk = self._tok(textos[i:i + self.lote], return_tensors="pt", padding=True,
                           truncation=True, max_length=MAX_TOKENS_ESPARSO).to(self.dev)
            with t.no_grad():
                logits = self._esp(**tk).logits
            v, _ = t.max(logits * tk["attention_mask"].unsqueeze(-1), dim=1)
            v = t.log1p(t.relu(v))
            v[:, self._especiais] = 0
            for linha in v:
                idx = t.nonzero(linha, as_tuple=True)[0]
                vals = linha[idx]
                fora.append({"indices": idx.tolist(), "values": [round(float(x), 5) for x in vals]}
                            if len(idx) else {"indices": [0], "values": [0.0]})
        return fora
