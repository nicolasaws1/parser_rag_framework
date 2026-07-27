# ── COMPARAÇÃO LEGÍVEL: como cada extrator lida com a notação científica ─────
# Cole esta célula no lugar da última do comparativo_extratores.ipynb
import json, re, unicodedata
from pathlib import Path

# Casos difíceis deste PDF: âncora de busca + o que o texto DEVE conter
CASOS = [
    ("Temperatura -80 °C",  "stored at",                  ["-80", "−80", "80"]),
    ("Coef. 155 mM⁻¹cm⁻¹",  "extinction coefficient of 155", ["155"]),
    ("Tampão 100 mmol L⁻¹", "potassium phosphate buffer",  ["100"]),
    ("Peróxido H₂O₂",       "decomposition of",            ["H2O2", "H₂O₂", "H_{2}O_{2}", "H2O"]),
]

VISIVEL = {"\x00": "␀", "\x0e": "␎", "\x0f": "␏", "\x01": "␁", "\x02": "␂",
           "\x03": "␃", "\x04": "␄", "\x05": "␅", "\x06": "␆", "\x07": "␇"}


def mostrar(t):
    """Torna caracteres de controle visíveis para inspeção."""
    return "".join(VISIVEL.get(c, c if unicodedata.category(c)[0] != "C" else "␦") for c in t)


def diagnostico(txt):
    """Sintomas de corrupção de caracteres."""
    ctrl = sum(1 for c in txt if unicodedata.category(c)[0] == "C" and c not in "\n\r\t")
    return {
        "controle": ctrl,                                  # caracteres de controle (corrupção)
        "nul": txt.count("\x00"),                          # NUL — quebra o Postgres
        "grau": txt.count("°"),                            # símbolo de grau preservado
        "sup_sub": sum(txt.count(c) for c in "⁰¹²³⁴⁵⁶⁷⁸⁹⁻₀₁₂₃₄₅₆₇₈₉"),  # super/subscritos
        "latex": len(re.findall(r"\$[^$]{2,80}\$", txt)),  # notação em LaTeX
    }


saidas = {}
for jf in sorted(OUT.glob("*.json")):
    nome = json.loads(jf.read_text(encoding="utf-8"))["extrator"]
    f = OUT / f"{nome}.md"
    if f.exists():
        saidas[nome] = f.read_text(encoding="utf-8")

if not saidas:
    print("Nenhum resultado encontrado em", OUT)

# ── 1) saúde do texto ────────────────────────────────────────────────────────
print("=" * 78)
print("SAÚDE DO TEXTO  (controle/NUL = corrupção; grau, sup/sub, latex = notação preservada)")
print("=" * 78)
print(f"{'extrator':<10} {'chars':>9} {'controle':>9} {'NUL':>6} {'grau':>6} {'sup/sub':>8} {'latex':>6}")
print("-" * 78)
for nome, txt in saidas.items():
    d = diagnostico(txt)
    print(f"{nome:<10} {len(txt):>9,} {d['controle']:>9} {d['nul']:>6} "
          f"{d['grau']:>6} {d['sup_sub']:>8} {d['latex']:>6}")

# ── 2) os mesmos trechos, lado a lado ────────────────────────────────────────
for titulo, ancora, esperados in CASOS:
    print("\n" + "=" * 78)
    print(f"CASO: {titulo}")
    print("=" * 78)
    for nome, txt in saidas.items():
        i = txt.find(ancora)
        if i < 0:
            print(f"  {nome:<9} ⚠️  trecho não encontrado")
            continue
        trecho = re.sub(r"\s+", " ", mostrar(txt[i:i + 110])).strip()
        ok = any(e in txt[i:i + 140] for e in esperados)
        print(f"  {nome:<9} {'✅' if ok else '❌'} {trecho}")

print("\n" + "=" * 78)
print("LEGENDA: ␀ = byte NUL (rejeitado pelo Postgres) · ␎/␦ = caractere de controle")
print("Ambos indicam que o extrator não mapeou o símbolo original (menos, grau, etc.).")
