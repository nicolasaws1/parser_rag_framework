# -*- coding: utf-8 -*-
"""Simula a execução do notebook célula a célula:
   - sintaxe de cada célula
   - todo import está disponível no momento em que a célula roda?
   - todo nome usado foi definido por uma célula anterior?
"""
import json, ast, sys, io, re, builtins
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# o que o Colab já traz instalado (sem pip)
PREINSTALADO = {
    'os','re','gc','json','time','sys','io','pathlib','subprocess','tempfile','shutil',
    'collections','itertools','math','random','datetime','typing','warnings','unicodedata',
    'PIL','bs4','numpy','pandas','torch','requests','matplotlib','google','huggingface_hub',
    'transformers','IPython',
}
# pacote pip -> módulo importável
PIP_PARA_MODULO = {
    'PyMuPDF':'fitz', 'mineru':'mineru', 'mineru[core]':'mineru',
    'doclayout-yolo':'doclayout_yolo', 'chandra-ocr':'chandra', 'chandra-ocr[hf]':'chandra',
    'beautifulsoup4':'bs4', 'Pillow':'PIL', 'pandas':'pandas', 'marker-pdf':'marker',
    'transformers':'transformers', 'huggingface_hub':'huggingface_hub',
}

def imports_da_celula(src):
    """Módulos de topo importados por esta célula."""
    try:
        arv = ast.parse(src)
    except SyntaxError:
        return set()
    mods = set()
    for n in ast.walk(arv):
        if isinstance(n, ast.Import):
            mods |= {a.name.split('.')[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split('.')[0])
    return mods

def instalados_por(src):
    """Módulos que ficam disponíveis após um !pip install desta célula."""
    out = set()
    for linha in src.splitlines():
        if 'pip install' not in linha:
            continue
        for tok in re.findall(r'[A-Za-z0-9_.\-\[\]]+', linha.split('pip install')[1]):
            if tok in ('-q','-U','--upgrade','install','pip','q'):
                continue
            out.add(PIP_PARA_MODULO.get(tok, tok.split('[')[0].replace('-','_')))
    return out

def nomes_definidos(src):
    try:
        arv = ast.parse(src)
    except SyntaxError:
        return set()
    out = set()
    for n in ast.walk(arv):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
            if not isinstance(n, ast.ClassDef):            # parâmetros contam como definidos
                for a in n.args.args + n.args.kwonlyargs + n.args.posonlyargs: out.add(a.arg)
                if n.args.vararg: out.add(n.args.vararg.arg)
                if n.args.kwarg: out.add(n.args.kwarg.arg)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name): out.add(sub.id)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            for sub in ast.walk(n.target):
                if isinstance(sub, ast.Name): out.add(sub.id)
        elif isinstance(n, (ast.comprehension,)):
            for sub in ast.walk(n.target):
                if isinstance(sub, ast.Name): out.add(sub.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for a in n.args.args + n.args.kwonlyargs: out.add(a.arg)
            if n.args.vararg: out.add(n.args.vararg.arg)
            if n.args.kwarg: out.add(n.args.kwarg.arg)
        elif isinstance(n, ast.Lambda):
            for a in n.args.args + n.args.kwonlyargs + n.args.posonlyargs: out.add(a.arg)
            if n.args.vararg: out.add(n.args.vararg.arg)
            if n.args.kwarg: out.add(n.args.kwarg.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add(a.asname or a.name.split('.')[0])
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            for sub in ast.walk(n.target):
                if isinstance(sub, ast.Name): out.add(sub.id)
        elif isinstance(n, ast.withitem) and n.optional_vars is not None:
            for sub in ast.walk(n.optional_vars):
                if isinstance(sub, ast.Name): out.add(sub.id)
    return out

def nomes_usados(src):
    try:
        arv = ast.parse(src)
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(arv) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}

def testar(caminho, fases):
    """fases: lista de (nome, [indices das células de código nessa ordem])"""
    nb = json.load(open(caminho, encoding='utf-8'))
    cod = [(i, "".join(c["source"])) for i, c in enumerate(nb["cells"]) if c["cell_type"] == "code"]
    print("=" * 74); print(Path(caminho).name); print("=" * 74)

    problemas = []
    for nome_fase, indices in fases:
        print(f"\n--- {nome_fase} ---")
        disp = set(PREINSTALADO)                    # módulos disponíveis
        definidos = set(dir(builtins)) | {'__name__','True','False','None'}
        for k in indices:
            i, src = cod[k]
            rotulo = f"célula[{i}]"
            # 1) sintaxe
            if not src.lstrip().startswith(('!', '%')):
                try:
                    ast.parse(src)
                except SyntaxError as e:
                    problemas.append(f"{nome_fase} {rotulo}: SINTAXE — {e}")
                    print(f"  {rotulo}  ❌ sintaxe: {e}"); continue
            # 2) instalações desta célula
            disp |= instalados_por(src)
            # 3) imports precisam existir
            faltando = [m for m in imports_da_celula(src) if m not in disp]
            # 4) nomes usados precisam ter sido definidos
            definidos |= nomes_definidos(src)      # a célula define seus próprios nomes
            usados = nomes_usados(src)
            nao_def = sorted(u for u in usados
                             if u not in definidos and u not in disp
                             and not hasattr(builtins, u))
            if faltando:
                problemas.append(f"{nome_fase} {rotulo}: import indisponível {faltando}")
                print(f"  {rotulo}  ❌ import sem instalação: {faltando}")
            elif nao_def:
                problemas.append(f"{nome_fase} {rotulo}: nome não definido {nao_def[:6]}")
                print(f"  {rotulo}  ❌ nome não definido: {nao_def[:6]}")
            else:
                print(f"  {rotulo}  ✅")
    print()
    if problemas:
        print(f"❌ {len(problemas)} problema(s):")
        for p in problemas: print("   -", p)
    else:
        print("✅ nenhum problema — as duas fases rodam na ordem")
    return problemas

if __name__ == "__main__":
    NB = r"C:\Users\nicol\OneDrive\Área de Trabalho\SB100\squad-2\extractor\hybrid_mineru_2fases.ipynb"
    # índices das células de CÓDIGO (não do notebook): 0=config 1=inst1 2=fase1 3=inst2 4=func2 5=fase2
    # 0=config 1=inst1 2=fase1 | 3=inst2 4=verif 5=funcs2 6=fase2
    testar(NB, [("FASE 1", [0, 1, 2]), ("FASE 2 (após restart)", [0, 3, 4, 5, 6])])
