# ── CONFIG (sem Google Drive: tudo em /content) ─────────────────────────────
import os, re, gc, json, time, subprocess, sys
from pathlib import Path

subprocess.run([sys.executable,"-m","pip","install","-q",*['docling', 'chandra-ocr[hf]', 'doclayout-yolo', 'PyMuPDF', 'beautifulsoup4', 'Pillow', 'pandas', 'huggingface_hub']], check=False)

import torch, fitz
from PIL import Image
from bs4 import BeautifulSoup
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")

BASE_DIR   = Path('/content')
PDFS_DIR   = BASE_DIR/'pdfs'
EXPORT_DIR = BASE_DIR/'export'
PDFS_DIR.mkdir(parents=True, exist_ok=True); EXPORT_DIR.mkdir(parents=True, exist_ok=True)

DPI_WEB, HIGH_DPI, MAX_LADO, YOLO_CONF = 150, 230, 1500, 0.40
MAX_PAGINAS = None
TEXTO_MIN_CHARS = 120

CLASSES_YOLO = {0:'title',1:'plain_text',2:'abandon',3:'figure',4:'figure_caption',
                5:'table',6:'table_caption',7:'table_footnote',8:'isolate_formula',9:'formula_caption'}
CLS_TEXTO = {0,1,4,6,9}
CLS_ROTA  = {3:'figura', 5:'tabela', 8:'formula'}

META = {}
_mp = BASE_DIR/'metadados_api.json'
if _mp.exists(): META = json.loads(_mp.read_text(encoding='utf-8'))
def meta_do(nome):
    a = META.get(nome, {}); g = lambda *ks: next((str(a[k]).strip() for k in ks if a.get(k)), "")
    return {"titulo":g("Título","Titulo","title"),"autores":g("Autor(es)"),"ano":g("Ano"),
            "periodico":g("Título do periódico","Editora"),"volume":g("Volume"),"doi":g("DOI") or "—",
            "categoria":g("CATEGORIA"),"tipo":g("Tipo de documento"),"palavras_chave":g("Palavras-chave")}

TARGETS = sorted(x.name for x in PDFS_DIR.glob('*.pdf'))
assert TARGETS, f"nenhum PDF em {PDFS_DIR}"
print("alvos:", TARGETS)


# ───── cell 2 ─────
from huggingface_hub import hf_hub_download
from doclayout_yolo import YOLOv10
yolo = YOLOv10(hf_hub_download(repo_id='juliozhao/DocLayout-YOLO-DocStructBench',
                               filename='doclayout_yolo_docstructbench_imgsz1024.pt'))
print("✅ YOLO")

# ───── cell 3 ─────
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import TableItem, PictureItem, DocItemLabel, CoordOrigin
_o = PdfPipelineOptions(); _o.do_ocr = False; _o.do_table_structure = True
_o.table_structure_options.do_cell_matching = True
docling_conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=_o)})
print("✅ Docling")

# ───── cell 4 ─────
from transformers import AutoModelForImageTextToText, AutoProcessor
from chandra.model.hf import generate_hf
from chandra.model.schema import BatchInputItem
from chandra.output import parse_markdown
_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
print("🔄 Chandra..."); t0=time.time()
chandra = AutoModelForImageTextToText.from_pretrained('datalab-to/chandra-ocr-2', dtype=_dtype, device_map='auto')
chandra.eval(); chandra.processor = AutoProcessor.from_pretrained('datalab-to/chandra-ocr-2')
chandra.processor.tokenizer.padding_side='left'
chandra.generation_config.max_new_tokens=4096
chandra.generation_config.do_sample=False
chandra.generation_config.repetition_penalty=1.10   # config que te deu o melhor Chandra
chandra.generation_config.no_repeat_ngram_size=0
print(f"✅ Chandra {time.time()-t0:.0f}s")

# ───── cell 5 ─────
META = {}
_mp = BASE_DIR/'metadados_api.json'
if _mp.exists(): META = json.loads(_mp.read_text(encoding='utf-8'))
def meta_do(nome):
    a = META.get(nome, {}); g = lambda *ks: next((str(a[k]).strip() for k in ks if a.get(k)), "")
    return {"titulo":g("Título","Titulo","title"),"autores":g("Autor(es)"),"ano":g("Ano"),
            "periodico":g("Título do periódico","Editora"),"volume":g("Volume"),"doi":g("DOI") or "—",
            "categoria":g("CATEGORIA"),"tipo":g("Tipo de documento"),"palavras_chave":g("Palavras-chave")}
print("meta ok:", len(META))

# ───── cell 6 ─────
# ── render + crop ──
def render_paginas(page):
    esc = HIGH_DPI/72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(esc,esc))
    im_hi = Image.frombytes('RGB',[pix.width,pix.height],pix.samples); del pix
    im_web = im_hi; w,h = im_hi.size
    if max(w,h) > MAX_LADO:
        s = MAX_LADO/max(w,h); im_web = im_hi.resize((int(w*s),int(h*s)))
    return im_hi, im_web, im_web.size

def _crop_hi(im_hi, bbox, m=14):
    W,H = im_hi.size; x0,y0,x1,y1 = bbox
    return im_hi.crop((max(0,int(x0*W)-m), max(0,int(y0*H)-m), min(W,int(x1*W)+m), min(H,int(y1*H)+m)))

# ── Chandra ──
def _anti_rep(t, lim=4):
    out, ant, rep = [], None, 0
    for l in (t or '').splitlines():
        if l.strip() and l == ant:
            rep += 1
            if rep >= lim: continue
        else: rep, ant = 0, l
        out.append(l)
    return "\n".join(out)

def chandra_md(pil):
    res = generate_hf([BatchInputItem(image=pil.convert('RGB'), prompt_type='ocr_layout')], chandra)[0]
    raw = getattr(res,'raw','') or getattr(res,'markdown','') or ''
    try: md_ = parse_markdown(raw)
    except Exception: md_ = raw
    torch.cuda.empty_cache()
    return _anti_rep(md_ or '')

def classificar_figura(md_txt):
    txt = md_txt or ''
    n_num = len(re.findall(r'\d', txt)); n_alpha = len(re.findall(r'[A-Za-zÀ-ÿ]', txt))
    kw = ('eixo','axis','fig','regress','dose','kg','ha','ph','cm','mg','%')
    tem = any(any(k in t for k in kw) for t in re.findall(r'\w+', txt.lower()))
    if n_alpha < 8 and n_num < 4: return 'foto'
    if n_num >= 8 and (tem or n_alpha >= 15): return 'grafico'
    return 'grafico' if n_num >= n_alpha*0.30 else 'foto'

def limpar_figura(mdk):
    """MD de figura p/ vetor: remove ![](img) fantasma, $ vazios/soltos e tabelas DUPLICADAS,
    preservando reações (LaTeX), descrição e a tabela de dados. Robusto p/ figuras compostas
    (diagramas de reação + gráfico). A descrição PT vem da LEGENDA (associada em exportar_pdf)."""
    if not mdk: return ""
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', mdk)          # remove ![alt](img)
    t = re.sub(r'\$\s*\$', ' ', t)                        # remove $ $ vazios
    _seen = set()
    def _dedup(m):
        k = re.sub(r'\s+', '', m.group())
        if k in _seen: return ''
        _seen.add(k); return m.group()
    t = re.sub(r'<table.*?</table>', _dedup, t, flags=re.S)   # tabelas duplicadas -> 1
    t = re.sub(r'[ \t]{2,}', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t).strip()
    return t

# ── YOLO ──
def detectar_regioes(img_path, w, h, conf=YOLO_CONF):
    det = yolo.predict(str(img_path), imgsz=1024, conf=conf, verbose=False)
    boxes = det[0].boxes; out = []
    if boxes is None or len(boxes)==0: return out
    cls=boxes.cls.cpu().numpy().astype(int); xyxy=boxes.xyxy.cpu().numpy(); cfs=boxes.conf.cpu().numpy()
    for c,(x0,y0,x1,y1),cf in zip(cls,xyxy,cfs):
        out.append({'classe_id':int(c),'tipo_rota':CLS_ROTA.get(int(c)),
                    'bbox':[float(x0)/w,float(y0)/h,float(x1)/w,float(y1)/h],'conf':float(cf)})
    return out

def tem_duas_colunas(regs, folga=0.02, larg_max=0.55, sobrep=0.5):
    """Ha' par de blocos ESTREITOS lado a lado na mesma faixa vertical?

    So' isso caracteriza duas colunas. Sem esta checagem, uma folha de rosto
    centralizada (Boletim 100 p6) era fatiada em duas: os blocos tem largura ~0.61,
    logo abaixo do full_thr, e o centro deles cai perto de 0.5 — metade ia para o
    balde da esquerda, metade para o da direita, e o texto saia embaralhado
    ('O Conteudo do Texto' antes de '1. Adubacao', 'Tiragem' antes de 'A reproducao').

    Exige bloco estreito (< larg_max) e sobreposicao vertical real (>= sobrep da
    altura do menor), senao numero de pagina ao lado do cabecalho ja' contaria como
    coluna. Medido no corpus: 169 paginas Chandra com duas colunas, 128 com uma."""
    bs = [r['bbox'] for r in regs if r.get('bbox') and (r['bbox'][2]-r['bbox'][0]) < larg_max]
    for i, A in enumerate(bs):
        for B in bs[i+1:]:
            ov = min(A[3], B[3]) - max(A[1], B[1])
            menor = min(A[3]-A[1], B[3]-B[1])
            if menor > 0 and ov/menor >= sobrep and (A[2] < B[0]+folga or B[2] < A[0]+folga):
                return True
    return False

def ordenar_regioes(regs, x_split=0.5, full_thr=0.62):
    cx=lambda r:(r['bbox'][0]+r['bbox'][2])/2; wd=lambda r:r['bbox'][2]-r['bbox'][0]
    regs=sorted(regs,key=lambda r:(round(r['bbox'][1],3),cx(r)))
    if not tem_duas_colunas(regs):
        return regs                        # coluna unica: a ordem de leitura e' o topo->base
    ordem,esq,dirr=[],[],[]
    def flush():
        ordem.extend(sorted(esq,key=lambda r:r['bbox'][1])); ordem.extend(sorted(dirr,key=lambda r:r['bbox'][1]))
        esq.clear(); dirr.clear()
    for r in regs:
        if wd(r)>=full_thr: flush(); ordem.append(r)
        elif cx(r)<x_split: esq.append(r)
        else: dirr.append(r)
    flush(); return ordem

# ── tabela: html -> heads/regs -> texto/resumo ──
def _regs_de_html(html):
    trs = BeautifulSoup(html or '', 'html.parser').find_all('tr')
    if not trs: return [], []
    heads = [c.get_text(strip=True) for c in trs[0].find_all(['th','td'])]
    regs = []
    for tr in trs[1:]:
        cels = [c.get_text(strip=True) for c in tr.find_all(['td','th'])]
        if any(cels): regs.append({(heads[k] if k<len(heads) else f'col{k}'):v for k,v in enumerate(cels)})
    return heads, regs

def tabela_texto(titulo, heads, regs):
    p=[titulo] if titulo else []; p.append("Tabela com colunas: "+", ".join(heads)+".")
    idc=heads[0] if heads else ""
    for r in regs:
        ident=r.get(idc,""); vals=[f"{k} = {r[k]}" for k in heads[1:] if str(r.get(k,'')).strip()]
        if ident and vals: p.append(f"{idc} {ident}: "+"; ".join(vals)+".")
    return "\n".join(p)

def tabela_resumo(titulo, heads, regs):
    idc=heads[0] if heads else ""; itens=", ".join(str(r.get(idc,"")) for r in regs[:8])
    return ((titulo+" ") if titulo else "")+f"Tabela com {len(regs)} linhas e {len(heads)} colunas ({', '.join(heads)}). {idc}: {itens}."

def _md_table(heads, regs):
    if not heads: return ""
    esc = lambda s: str(s).replace("|","\\|").replace("\n"," ").strip()
    linhas = ["| " + " | ".join(esc(h) for h in heads) + " |",
              "| " + " | ".join("---" for _ in heads) + " |"]
    for r in regs:
        linhas.append("| " + " | ".join(esc(r.get(k,"")) for k in heads) + " |")
    return "\n".join(linhas)

def bloco_tabela(html, bbox):
    heads, regs = _regs_de_html(html)
    res = tabela_resumo("", heads, regs) if heads else ""
    tbl = _md_table(heads, regs)
    # MD p/ vetor: resumo em linguagem natural (âncora semântica) + tabela markdown (LLM-friendly)
    md = "\n\n".join(x for x in [res, tbl] if x) or "[tabela]"
    return {'tipo':'tabela','bbox':[round(v,4) for v in bbox],
            'md':md,
            'tabela_colunas':heads,'tabela_html':html,'tabela_json':regs,'tabela_resumo':res}

_SUP={'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹','+':'⁺','-':'⁻','(':'⁽',')':'⁾','n':'ⁿ','i':'ⁱ'}
_SUB={'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉','+':'₊','-':'₋','(':'₍',')':'₎'}
def _conv(s, mapa): return ''.join(mapa.get(c,c) for c in s)
_FORMQ=[('(NH4)2SO4','(NH₄)₂SO₄'),('(NH₄)2SO4','(NH₄)₂SO₄'),('K2SO4','K₂SO₄'),('H2SO4','H₂SO₄'),
        ('CaCl2','CaCl₂'),('P2O5','P₂O₅'),('K2O','K₂O'),('CaCO3','CaCO₃'),('KNO3','KNO₃'),
        ('NaNO3','NaNO₃'),('NH4','NH₄'),('NO3','NO₃'),('NO2','NO₂'),('SO4','SO₄'),('PO4','PO₄'),
        ('H2O','H₂O'),('CO2','CO₂')]
def formatar_ciencia(t):
    """Isótopos, unidades com expoente e fórmulas químicas ASCII -> unicode (para páginas Docling)."""
    if not t: return t
    t=re.sub(r'\b15\s?N(?![a-zç])','¹⁵N',t); t=re.sub(r'\b13\s?C(?![a-zç])','¹³C',t)
    t=re.sub(r'\b14\s?N(?![a-zç])','¹⁴N',t); t=re.sub(r'\b18\s?O(?![a-zç])','¹⁸O',t)
    t=re.sub(r'\b(kg|ha|g|mg|µg|L|mL|dm|cm|mol|m|t)\s?-([123])\b', lambda m:m.group(1)+_conv('-'+m.group(2),_SUP), t)
    for a,b in _FORMQ: t=t.replace(a,b)
    t=re.sub(r'\)(\d)([A-Z])', lambda m:')'+_conv(m.group(1),_SUB)+m.group(2), t)
    return t

def limpar_sup_sub(t):
    t = re.sub(r'<sup>(.*?)</sup>', lambda m: _conv(m.group(1), _SUP), t or '', flags=re.I|re.S)
    t = re.sub(r'<sub>(.*?)</sub>', lambda m: _conv(m.group(1), _SUB), t, flags=re.I|re.S)
    return formatar_ciencia(t)
def _abaixo(b):
    y = min(0.95, b[3]+0.004)
    return [b[0], y, b[2], min(0.99, y+0.035)]

# ── monta blocos de uma página roteada pro CHANDRA (usa data-bbox do RAW do Chandra) ──
def chandra_raw(pil):
    """Roda o Chandra e devolve o RAW (com <div data-bbox data-label>) + tamanho da imagem enviada."""
    im = pil.convert('RGB')
    if max(im.size) > 1800:
        s = 1800/max(im.size); im = im.resize((int(im.size[0]*s), int(im.size[1]*s)))
    res = generate_hf([BatchInputItem(image=im, prompt_type='ocr_layout')], chandra)[0]
    raw = getattr(res, 'raw', '') or ''
    torch.cuda.empty_cache()
    return raw, im.size

def _div_para_texto(div):
    inner = div.decode_contents()
    inner = limpar_sup_sub(inner)                       # <sup>/<sub> -> unicode (+ química)
    return BeautifulSoup(inner, 'html.parser').get_text(' ', strip=True)

def blocos_chandra(im_hi, regioes=None):
    """Cada <div data-bbox data-label> do Chandra vira um bloco. AUTO-CALIBRA a bbox.
    O YOLO (regioes) manda na classe tabela-vs-figura: onde o YOLO diz FIGURA, o Chandra
    NÃO transforma em tabela — o recorte vai pela rota de figura (Chandra define subclasse)."""
    raw, _ = chandra_raw(im_hi)
    divs = [d for d in BeautifulSoup(raw, 'html.parser').find_all('div') if d.get('data-bbox')]
    coords = []
    for d in divs:
        try:
            v = [float(x) for x in d['data-bbox'].split()][:4]
            coords.append(v if len(v) == 4 else None)
        except Exception:
            coords.append(None)
    allx = [x for c in coords if c for x in (c[0], c[2])]
    ally = [y for c in coords if c for y in (c[1], c[3])]
    MX = (min(allx) + max(allx)) if allx else 1.0   # margem esquerda ≈ direita (preserva margem)
    MY = (min(ally) + max(ally)) if ally else 1.0   # margem topo ≈ base
    MX = MX or 1.0; MY = MY or 1.0
    figs = [r['bbox'] for r in (regioes or []) if r.get('tipo_rota') == 'figura']  # YOLO manda em figura
    blocos = []
    for d, v in zip(divs, coords):
        if not v:
            continue
        bbox = [round(min(1, min(v[0],v[2])/MX), 4), round(min(1, min(v[1],v[3])/MY), 4),
                round(min(1, max(v[0],v[2])/MX), 4), round(min(1, max(v[1],v[3])/MY), 4)]
        if figs and _dentro(bbox, figs, frac=0.45):
            continue                                   # YOLO diz FIGURA -> não vira tabela/texto aqui
        label = d.get('data-label') or 'Text'
        tab = d.find('table')
        if tab is not None or 'Table' in label:
            blocos.append(bloco_tabela(str(tab) if tab is not None else '', bbox))
        elif 'Picture' in label or 'Figure' in label:
            desc = _div_para_texto(d)
            blocos.append({'tipo': (classificar_figura(desc) if desc else 'foto'), 'bbox': bbox, 'md': desc})
        else:
            md = _div_para_texto(d)
            if md.strip():
                blocos.append({'tipo': 'texto', 'bbox': bbox, 'md': md, 'origem': 'chandra'})
    # FIGURAS pelo YOLO: recorte + Chandra OCR do crop (captura equação DENTRO do gráfico) + subclasse
    for r in [r for r in (regioes or []) if r.get('tipo_rota') == 'figura']:
        mdk = chandra_md(_crop_hi(im_hi, r['bbox']))
        blocos.append({'tipo': classificar_figura(mdk), 'bbox': [round(v,4) for v in r['bbox']],
                       'md': limpar_figura(mdk), 'conf': round(r.get('conf', 0), 3),
                       'origem': 'chandra-figura'})
    # aqui a ordenacao geometrica CONTINUA: o Chandra nao garante ordem de leitura
    blocos = ordenar_regioes(blocos)
    return blocos

def _fracao_coberta(reg, bb):
    """Fração da área da REGIÃO do YOLO que o bloco cobre (0 a 1)."""
    ix0,iy0 = max(reg[0],bb[0]), max(reg[1],bb[1])
    ix1,iy1 = min(reg[2],bb[2]), min(reg[3],bb[3])
    inter = max(0,ix1-ix0)*max(0,iy1-iy0)
    area = max(1e-9,(reg[2]-reg[0])*(reg[3]-reg[1]))
    return inter/area

def auditar_cobertura(regioes, blocos, fpage=None):
    """Mede quanto de cada região de texto do YOLO foi coberto pelos blocos extraídos.

    O YOLO detecta TODAS as regiões (inclusive title/plain_text, que o roteamento
    ignora). Comparando com as bboxes dos blocos dá para saber o que o Docling pulou
    — sem alterar a extração. Guarda também o texto da região descoberta, para
    inspeção posterior."""
    bbs = [b['bbox'] for b in blocos if b.get('bbox')]
    regs = []
    for r in regioes:
        if r.get('classe_id') not in CLS_TEXTO:
            continue
        cob = max((_fracao_coberta(r['bbox'], bb) for bb in bbs), default=0.0)
        item = {'classe': CLASSES_YOLO.get(r['classe_id'], '?'),
                'bbox': [round(v,4) for v in r['bbox']],
                'conf': round(r.get('conf',0),3),
                'coberta': round(cob,3)}
        if cob < 0.30 and fpage is not None:          # provavelmente pulada -> guarda o texto
            try:
                pw, ph = fpage.rect.width, fpage.rect.height
                rect = fitz.Rect(r['bbox'][0]*pw, r['bbox'][1]*ph, r['bbox'][2]*pw, r['bbox'][3]*ph)
                item['texto_na_regiao'] = (fpage.get_text('text', clip=rect) or '').strip()[:400]
            except Exception:
                pass
        regs.append(item)
    n = len(regs)
    cobertas = sum(1 for r in regs if r['coberta'] >= 0.30)
    return {'regioes_texto': n, 'cobertas': cobertas,
            'cobertura': round(cobertas/n, 3) if n else 1.0, 'regioes': regs}

def _mesma_coluna(a, b, x_split=0.5):
    return ((a[0]+a[2])/2 < x_split) == ((b[0]+b[2])/2 < x_split)

def inserir_recuperado(blocos, novo):
    """Encaixa um bloco recuperado mantendo a ordem de leitura já existente.

    O Docling tem modelo de reading order — reordenar a página inteira por
    geometria desfaz esse trabalho (numa página com duas zonas de 2 colunas
    empilhadas, joga o Abstract para depois da Introdução). Aqui a sequência do
    Docling é a espinha dorsal: o bloco novo entra logo após o último bloco que
    o precede na MESMA coluna; se não houver, após o último que começa acima."""
    bn = novo.get('bbox')
    if not bn:
        blocos.append(novo); return blocos
    pos = 0
    for i, b in enumerate(blocos):
        bb = b.get('bbox')
        if not bb: continue
        if _mesma_coluna(bb, bn) and bb[1] <= bn[1]:
            pos = i + 1
    if pos == 0:                                  # nenhuma âncora na mesma coluna
        for i, b in enumerate(blocos):
            bb = b.get('bbox')
            if bb and bb[1] <= bn[1]: pos = i + 1
    blocos.insert(pos, novo)
    return blocos

# ── página SEM tabela: Docling (ordem) + rede de segurança PyMuPDF + figura Chandra ──
def _norm(t): return re.sub(r'\s+',' ',(t or '')).strip().lower()
_SHN = 20          # tamanho do trecho comparado
_NOVO_MIN = 40     # caracteres inéditos contíguos para o bloco valer a pena

def _shingles(t, n=_SHN):
    """Conjunto de trechos sobrepostos de n caracteres (busca em O(1))."""
    if not t: return set()
    return {t[i:i+n] for i in range(len(t)-n+1)} if len(t) >= n else {t}

def _novidade(nt, base, n=_SHN):
    """Maior trecho CONTÍGUO do fragmento que NÃO existe no texto do Docling.

    Mede o que o fragmento ACRESCENTA, em vez de quanto ele repete — a proporção
    coberta engana: um bloco 83% coberto pode trazer 400 caracteres inéditos (deve
    entrar), e um 50% coberto pode trazer só 24 (não deve). Medido no corpus real:
    fragmento duplicado dá 0; parágrafo que o Docling dropou dá centenas."""
    if len(nt) < n:
        return 0 if nt in base else len(nt)
    maior = atual = 0
    for i in range(len(nt)-n+1):
        atual = atual + 1 if nt[i:i+n] not in base else 0
        if atual > maior: maior = atual
    return maior + n - 1 if maior else 0

def _canon(t):
    """Forma canonica p/ comparar texto de extratores diferentes: descarta espacos,
    pontuacao e notacao cientifica. Docling e PyMuPDF quebram o mesmo texto de formas
    distintas ('20 u l' vs '20 ul', 'H2O2' subscrito vs ASCII) e a comparacao literal
    deixava passar paragrafo duplicado."""
    return re.sub(r'[^a-z0-9]', '', para_llm(t or '').lower())
def _dentro(bn, regs, frac=0.5):
    a=max(1e-9,(bn[2]-bn[0])*(bn[3]-bn[1]))
    for r in regs:
        ix0,iy0=max(bn[0],r[0]),max(bn[1],r[1]); ix1,iy1=min(bn[2],r[2]),min(bn[3],r[3])
        if max(0,ix1-ix0)*max(0,iy1-iy0)/a>=frac: return True
    return False
def _norm_bbox(prov, pw, ph):
    bb=prov.bbox
    try:
        if bb.coord_origin==CoordOrigin.BOTTOMLEFT: top,bot=ph-max(bb.t,bb.b),ph-min(bb.t,bb.b)
        else: top,bot=min(bb.t,bb.b),max(bb.t,bb.b)
    except Exception: top,bot=min(bb.t,bb.b),max(bb.t,bb.b)
    l,r=min(bb.l,bb.r),max(bb.l,bb.r)
    return [round(l/pw,4),round(top/ph,4),round(r/pw,4),round(bot/ph,4)]

def blocos_docling(doc, pno, fpage, itens_pag, regioes, im_hi):
    try: ps=doc.pages[pno].size; pw,ph=ps.width,ps.height
    except Exception: pw,ph=fpage.rect.width,fpage.rect.height
    blocos=[]
    for it in itens_pag:
        if isinstance(it, (TableItem,)): continue   # (não deveria haver tabela aqui)
        if isinstance(it, PictureItem): continue     # figura tratada via YOLO+Chandra abaixo
        t=(getattr(it,'text','') or '').strip()
        if not t: continue
        tipo='formula' if getattr(it,'label',None)==DocItemLabel.FORMULA else 'texto'
        blocos.append({'tipo':tipo,'bbox':_norm_bbox(it.prov[0],pw,ph),'md':limpar_sup_sub(t),
                       'origem':'docling'})
    # rede de segurança PyMuPDF (dedup robusto: compara com o TEXTO CONCATENADO do Docling,
    # pegando o caso em que o Docling fatiou o mesmo parágrafo em vários itens -> evita duplicata)
    base=_shingles(_canon(" ".join(b.get('md','') for b in blocos)))
    pfw,pfh=fpage.rect.width,fpage.rect.height
    for pb in fpage.get_text('blocks'):
        x0,y0,x1,y1,txt=pb[0],pb[1],pb[2],pb[3],pb[4]; t=(txt or '').strip()
        nt=_canon(t)
        if len(nt)<12: continue
        if _novidade(nt, base) < _NOVO_MIN: continue    # não acrescenta nada -> não duplica
        bn=[round(x0/pfw,4),round(y0/pfh,4),round(x1/pfw,4),round(y1/pfh,4)]
        blocos.append({'tipo':'texto','bbox':bn,'md':limpar_sup_sub(t),
                       'origem':'pymupdf'}); base|=_shingles(nt)
    # figuras via YOLO + Chandra (subtipo)
    for r in [r for r in regioes if r['tipo_rota']=='figura']:
        mdk=chandra_md(_crop_hi(im_hi, r['bbox']))
        blocos.append({'tipo':classificar_figura(mdk),'bbox':[round(v,4) for v in r['bbox']],
                       'md':limpar_figura(mdk),'conf':round(r['conf'],3),'origem':'chandra-figura'})
    # ordenacao por coluna: coluna esquerda inteira, depois a direita. Verificado na
    # pagina 2 do bernardi-2022 — inserir por proximidade jogava a figura (topo da
    # coluna direita) para antes do texto da esquerda, quebrando a leitura.
    blocos = ordenar_regioes(blocos)
    return blocos

# ── MD p/ o vetor (document.md): super/subscritos unicode -> LaTeX (LOSSLESS, LLM-friendly) ──
_SUP_INV = {'⁰':'0','¹':'1','²':'2','³':'3','⁴':'4','⁵':'5','⁶':'6','⁷':'7','⁸':'8','⁹':'9','⁺':'+','⁻':'-','⁽':'(','⁾':')','ⁿ':'n','ⁱ':'i'}
_SUB_INV = {'₀':'0','₁':'1','₂':'2','₃':'3','₄':'4','₅':'5','₆':'6','₇':'7','₈':'8','₉':'9','₊':'+','₋':'-','₍':'(','₎':')'}
_RE_SUP = re.compile('[' + re.escape(''.join(_SUP_INV)) + ']+')
_RE_SUB = re.compile('[' + re.escape(''.join(_SUB_INV)) + ']+')
def para_llm(t):
    """MD p/ vetor: converte super/subscritos unicode -> LaTeX. LOSSLESS — preserva isótopos,
    expoentes e índices químicos numa forma que o LLM lê sem ambiguidade e nada se perde:
    ¹⁵N -> ^{15}N ; kg⁻¹ -> kg^{-1} ; (NH₄)₂SO₄ -> (NH_{4})_{2}SO_{4} ; x² -> x^{2}.
    O Legível mantém o unicode; isto é exclusivo do document.md (vetor)."""
    if not t: return t
    t = _RE_SUP.sub(lambda m: '^{' + ''.join(_SUP_INV[c] for c in m.group()) + '}', t)
    t = _RE_SUB.sub(lambda m: '_{' + ''.join(_SUB_INV[c] for c in m.group()) + '}', t)
    return t

print("✅ Funções prontas.")

# ───── cell 7 ─────
def slugify(n): return re.sub(r'[^a-z0-9]+','-',Path(n).stem.lower()).strip('-')[:60]

def exportar_pdf(nome):
    _t0=time.time()
    pdf=PDFS_DIR/nome; slug=slugify(nome); out=EXPORT_DIR/slug; (out/'pages').mkdir(parents=True,exist_ok=True)
    print(f"\n=== {nome} -> {slug}/  (Docling convertendo...)")
    doc = docling_conv.convert(str(pdf)).document
    itens_pag={}
    for it,_ in doc.iterate_items():
        pv=getattr(it,'prov',None)
        if pv: itens_pag.setdefault(pv[0].page_no,[]).append(it)

    fdoc=fitz.open(str(pdf)); N=len(fdoc); lim=N if MAX_PAGINAS is None else min(N,MAX_PAGINAS)
    paginas=[]
    for pi in range(lim):
        pno=pi+1; fpage=fdoc[pi]
        im_hi,im_web,(w,h)=render_paginas(fpage)
        im_web.save(out/f'pages/p{pno:03d}.jpg', quality=85)
        img_path=out/f'pages/p{pno:03d}.jpg'
        # camada de texto selecionável (limiar) -> decide se a página é orgânica ou escaneada
        texto_cru = fpage.get_text('text') or ''
        tem_texto = len(texto_cru.strip()) >= TEXTO_MIN_CHARS
        regioes=detectar_regioes(img_path, w, h)
        tem_tabela = any(r['tipo_rota']=='tabela' for r in regioes)
        if not tem_texto:
            blocos=blocos_chandra(im_hi, regioes)         # SEM camada de texto -> OCR full-page
            rota='chandra'; tipo_pg='escaneada'
        elif tem_tabela:
            blocos=blocos_chandra(im_hi, regioes)         # Chandra full-page + data-bbox; YOLO manda em tabela-vs-figura
            rota='chandra'; tipo_pg='organica'
        else:
            blocos=blocos_docling(doc, pno, fpage, itens_pag.get(pno,[]), regioes, im_hi)
            rota='docling'; tipo_pg='organica'
        for j,b in enumerate(blocos): b['id']=f'p{pno}-b{j}'
        # salva recortes de figuras/gráficos p/ recuperação posterior na busca
        for b in blocos:
            if b['tipo'] in ('grafico','foto') and b.get('bbox'):
                (out/'figures').mkdir(exist_ok=True)
                _crop_hi(im_hi, b['bbox']).save(out/'figures'/f"{b['id']}.jpg", quality=85)
                b['fig']=f"figures/{b['id']}.jpg"
        # associa a LEGENDA do artigo (PT, dos autores) ao gráfico/figura mais próximo
        for j,b in enumerate(blocos):
            if b['tipo'] in ('grafico','foto'):
                for k in (j+1, j-1):
                    ok = (0<=k<len(blocos) and blocos[k]['tipo']=='texto'
                          and re.match(r'\s*(figura|fig\.)\s*\d', blocos[k].get('md','') or '', re.I))
                    if ok:
                        cap=blocos[k]['md']; b['caption']=cap
                        b['md']=(cap+"\n\n"+(b['md'] or '')).strip(); break
        _aud = auditar_cobertura(regioes, blocos, fpage)     # só mede, não altera os blocos
        paginas.append({'n':pno,'img':f'pages/p{pno:03d}.jpg','w':w,'h':h,'tipo':tipo_pg,
            'rota':rota,'texto_cru':texto_cru,'auditoria':_aud,
            'chars':sum(len(b.get('md','')) for b in blocos),
            'counts':{'tabelas':sum(1 for b in blocos if b['tipo']=='tabela'),
                      'figuras':sum(1 for b in blocos if b['tipo'] in ('grafico','foto')),
                      'formulas':sum(1 for b in blocos if b['tipo']=='formula')},
            'blocos':blocos})
        c=paginas[-1]['counts']
        print(f"    p{pno:03d} [{rota:<7}|{tipo_pg[:4]}] blocos={len(blocos)} tab={c['tabelas']} "
              f"fig={c['figuras']} | YOLO texto={_aud['regioes_texto']:2d} "
              f"cobertas={_aud['cobertas']:2d} ({_aud['cobertura']:.0%})")
        gc.collect(); torch.cuda.empty_cache()
    fdoc.close()
    resumo={'organicas':sum(1 for p in paginas if p['tipo']=='organica'),
            'escaneadas':sum(1 for p in paginas if p['tipo']=='escaneada'),
            'tabelas':sum(p['counts']['tabelas'] for p in paginas),
            'figuras':sum(p['counts']['figuras'] for p in paginas),
            'formulas':sum(p['counts']['formulas'] for p in paginas)}
    layout={'arquivo':nome,'slug':slug,'n_paginas':N,'processadas':lim,'meta':meta_do(nome),'resumo':resumo,'tempo_s':round(time.time()-_t0,1),'paginas':paginas}
    (out/'layout.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2),encoding='utf-8')
    titulo=layout['meta'].get('titulo') or Path(nome).stem
    linhas=[f"# {titulo}",""]
    for p in paginas:
        linhas.append(f"\n---\n\n## Página {p['n']} ({p['rota']})\n")
        for b in p['blocos']: linhas.append(para_llm(b['md'])); linhas.append("")   # ASCII p/ o LLM/vetor
    (out/'document.md').write_text("\n".join(linhas),encoding='utf-8')
    print(f"  ✅ {slug} | resumo={resumo}")
    return layout

# ═══ LOOP RETOMÁVEL ═══
# Pula PDF que já tem layout.json; grava index.json a cada item; 1 erro não derruba a rodada.
IDX_PATH = EXPORT_DIR/'index.json'
INDEX = json.loads(IDX_PATH.read_text(encoding='utf-8')) if IDX_PATH.exists() else []
feitos = {e['slug'] for e in INDEX}

def _entrada(lay, nome):
    return {'slug':lay['slug'],'arquivo':nome,'n_paginas':lay['n_paginas'],
            'meta':lay['meta'],'resumo':lay['resumo']}

erros=[]; novos=0; pulados=0
for i, nome in enumerate(TARGETS, 1):
    slug = slugify(nome); lay_path = EXPORT_DIR/slug/'layout.json'
    if lay_path.exists():                                  # já extraído -> pula (retomável)
        if slug not in feitos:
            try:
                INDEX.append(_entrada(json.loads(lay_path.read_text(encoding='utf-8')), nome))
                feitos.add(slug)
            except Exception as e:
                print(f"    ⚠️  layout.json ilegível em {slug}: {e}")
        pulados += 1
        print(f"[{i}/{len(TARGETS)}] ⏭️  {slug} (já extraído)")
        continue
    try:
        print(f"[{i}/{len(TARGETS)}] ▶️  {nome}")
        lay = exportar_pdf(nome)
        INDEX.append(_entrada(lay, nome)); feitos.add(slug); novos += 1
        IDX_PATH.write_text(json.dumps(INDEX,ensure_ascii=False,indent=2),encoding='utf-8')  # incremental
    except Exception as e:
        erros.append({'arquivo':nome,'erro':repr(e)})
        print(f"    ❌ ERRO em {nome}: {e}")
        (EXPORT_DIR/'erros.json').write_text(json.dumps(erros,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        gc.collect(); torch.cuda.empty_cache()

IDX_PATH.write_text(json.dumps(INDEX,ensure_ascii=False,indent=2),encoding='utf-8')
print(f"\n🎉 Rodada concluída em {EXPORT_DIR}")
print(f"   novos={novos} | pulados={pulados} | erros={len(erros)} | total no índice={len(INDEX)}")
if erros:
    print("   ⚠️  Falhas (veja erros.json) — rode a célula de novo para tentar só elas:")
    for e in erros: print("    -", e['arquivo'])

# ───── cell 8 ─────
slug=INDEX[0]['slug']
lay=json.loads((EXPORT_DIR/slug/'layout.json').read_text(encoding='utf-8'))
print("Rota por página:", {p['n']:p['rota'] for p in lay['paginas']})
p1=lay['paginas'][0]
print("p1 tem título no topo?", any('nitrogen' in b['md'].lower() or 'acúmulo' in b['md'].lower() for b in p1['blocos'][:3]))
for p in lay['paginas']:
    for b in p['blocos']:
        if b['tipo']=='tabela':
            print(f"tabela p{p['n']} {b['id']}: colunas={b.get('tabela_colunas')[:6]}... linhas={len(b.get('tabela_json',[]))}")
            break

# ───── cell 9 ─────
import shutil
shutil.make_archive('/content/SITE_EXPORT_HIBRIDO','zip',EXPORT_DIR)
print("📦 /content/SITE_EXPORT_HIBRIDO.zip")
try:
    from google.colab import files; files.download('/content/SITE_EXPORT_HIBRIDO.zip')
except Exception as e: print("baixe manual:", e)