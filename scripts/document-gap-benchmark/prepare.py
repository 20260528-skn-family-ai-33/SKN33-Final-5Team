"""Freeze a small controlled corpus; never execute inference or overwrite a corpus."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pymupdf

p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
p.add_argument('--hwp-inputs', type=Path, required=True)
p.add_argument('--hwp-gold', type=Path, required=True)
p.add_argument('--public-corpus', type=Path, required=True)
args = p.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
(out / 'inputs').mkdir()
(out / 'gold').mkdir()

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

# These expectations precede PDF creation. The corpus is controlled, not representative.
specs = [
    {'id': 'SYN-A', 'title': 'Quarterly report', 'values': [12, 25, 40],
     'formula': {'latex': r'\frac{a+b}{c}', 'description': '(a+b) divided by c'},
     'table': [['Category', 'Amount'], ['Alpha', '12'], ['Beta', '25'], ['Gamma', '40']], 'merged_header': 'Results',
     'anchors': ['Quarterly report', '1. Summary', '2. Formula', '3. Table', '4. Chart'],
     'chart': {'kind': 'bar', 'x_labels': ['A', 'B', 'C'], 'y_label': 'Units', 'values': [12,25,40]}},
    {'id': 'SYN-B', 'title': 'Annual analysis', 'values': [15, 30, 45],
     'formula': {'latex': r'x_{2} = a^{2} + b^{2}', 'description': 'x subscript 2 equals a squared plus b squared'},
     'table': [['Category', 'Amount'], ['Alpha', '15'], ['Beta', '30'], ['Gamma', '45']], 'merged_header': 'Results',
     'anchors': ['Annual analysis', '1. Summary', '2. Formula', '3. Table', '4. Chart'],
     'chart': {'kind': 'bar', 'x_labels': ['A', 'B', 'C'], 'y_label': 'Units', 'values': [15,30,45]}}
]
write(out / 'gold' / 'synthetic-spec.json', {'version':'gap-gold-v1', 'specifications':specs,
    'provenance':'authored before fixture generation; not human-verified real documents'})

def make_pdf(spec, path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    def text(x,y,s,size=12): page.insert_text((x,y), s, fontsize=size)
    text(50,45,spec['title'],20)
    text(50,80,'1. Summary',15); text(50,102,'Fixed control document. Identifier 000017.',11)
    text(50,140,'2. Formula',15)
    if spec['id']=='SYN-A':
        text(83,163,'a + b',12); page.draw_line((80,168),(120,168)); text(96,184,'c',12)
    else:
        text(80,173,'x',15); text(89,177,'2',8); text(95,173,'= a',15); text(128,166,'2',8)
        text(135,173,'+ b',15); text(162,166,'2',8)
    text(50,220,'3. Table',15)
    # Two-level table header with a merged cell; numbers are positioned as data cells.
    xs=[50,190,300]; ys=[235,257,279,301,323,345]
    for x in xs: page.draw_line((x,257 if x == 190 else 235),(x,345))
    for y in ys: page.draw_line((50,y),(300,y))
    text(60,251,'Results',10)
    text(60,273,'Category',10); text(200,273,'Amount',10)
    for i, row in enumerate(spec['table'][1:]):
        text(60,295+22*i,row[0],10); text(200,295+22*i,row[1],10)
    text(50,390,'4. Chart',15); text(52,416,'Units',10)
    page.draw_line((90,560),(330,560)); page.draw_line((90,560),(90,420))
    for i,(label,value) in enumerate(zip(['A','B','C'],spec['values'])):
        x=120+i*70; top=560-value*2.5
        page.draw_rect(pymupdf.Rect(x,top,x+30,560),color=(.2,.3,.6),fill=(.55,.68,.85))
        text(x+6,top-6,str(value),10); text(x+10,577,label,10)
    text(50,620,'Figure 1. Values by category.',11)
    doc.save(path, garbage=3, deflate=True)
    doc.close()

def raster_pdf(src, dest, png=None):
    doc=pymupdf.open(src); pix=doc[0].get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
    img=pix.tobytes('png')
    if png: png.write_bytes(img)
    scanned=pymupdf.open(); pg=scanned.new_page(width=612,height=792)
    pg.insert_image(pg.rect,stream=img); scanned.save(dest,garbage=3,deflate=True)
    scanned.close(); doc.close()

cases=[]
for spec in specs:
    gold=out/'gold'/f"{spec['id']}.json"; write(gold,spec)
    native=out/'inputs'/f"{spec['id']}-native.pdf"; make_pdf(spec,native)
    scan=out/'inputs'/f"{spec['id']}-scan.pdf"
    image=out/'inputs'/f"{spec['id']}-image.png" if spec['id']=='SYN-A' else None
    raster_pdf(native,scan,image)
    for path,variant in [(native,'native'),(scan,'scan')]+([(image,'image')] if image else []):
        cases.append(dict(id=path.stem, input=str(path.relative_to(out)), sha256=sha(path),
            gold=str(gold.relative_to(out)), gold_sha256=sha(gold), family=spec['id'],
            variant=variant, suite='synthetic_control', format=path.suffix[1:]))
for src in sorted(args.hwp_inputs.glob('HWP-*')):
    if src.suffix.lower() not in {'.hwp','.hwpx'}: continue
    family=src.stem.rsplit('-',1)[0]
    truth=args.hwp_gold/(family+'.json')
    if not truth.is_file(): raise FileNotFoundError(truth)
    dest=out/'inputs'/src.name; shutil.copyfile(src,dest)
    target=out/'gold'/(family+'.json')
    if not target.exists(): shutil.copyfile(truth,target)
    cases.append(dict(id=src.stem,input=str(dest.relative_to(out)),sha256=sha(dest),gold=str(target.relative_to(out)),
        gold_sha256=sha(target),family=family,variant=src.suffix[1:].lower(),suite='existing_hwp',format=src.suffix[1:].lower()))
if sum(c['suite']=='existing_hwp' for c in cases)!=6:
    raise ValueError('Expected exactly 6 HWP/HWPX input files')
public = args.public_corpus.resolve()
public_manifest = json.loads((public/'manifest.json').read_text(encoding='utf-8'))
selected = [c for c in public_manifest['cases'] if c['suite']=='omnidocbench']
if len(selected)!=18: raise ValueError('Expected exactly 18 fixed OmniDocBench inputs')
for c in selected:
    source=(public/c['input']).resolve(); truth=(public/c['gold']).resolve()
    if not source.is_relative_to(public) or not truth.is_relative_to(public): raise ValueError('Unsafe public corpus path')
    if sha(source)!=c['sha256'] or sha(truth)!=c['gold_sha256']: raise ValueError('Public corpus hash mismatch: '+c['id'])
    dest=out/'inputs'/(c['id']+'.pdf'); shutil.copyfile(source,dest)
    target=out/'gold'/'omni-selected.json'
    if not target.exists(): shutil.copyfile(truth,target)
    cases.append(dict(id=c['id'],input=str(dest.relative_to(out)),sha256=sha(dest),gold=str(target.relative_to(out)),
        gold_sha256=sha(target),family=c['original_pdf_path'],image_id=c['image_id'],variant='source',
        suite='omnidocbench',format='pdf'))
broken=[c for c in public_manifest['cases'] if c.get('expected_failure')]
if len(broken)!=1: raise ValueError('Expected one fixed corrupt input')
c=broken[0]; source=(public/c['input']).resolve()
if not source.is_relative_to(public) or sha(source)!=c['sha256']: raise ValueError('Corrupt input hash mismatch')
truth=out/'gold'/'corrupt.json';write(truth,{'expected':'failure','reason':'intentionally corrupt PDF'})
dest=out/'inputs'/'CORRUPT.pdf';shutil.copyfile(source,dest)
cases.append(dict(id='CORRUPT',input=str(dest.relative_to(out)),sha256=sha(dest),gold=str(truth.relative_to(out)),
    gold_sha256=sha(truth),family='CORRUPT',variant='corrupt',suite='failure_input',format='pdf'))
manifest={'version':'document-gap-corpus-v1','cases':cases,'notes':['Not a representative or held-out set.',
    'HWP sources are three authored content families in two formats; human independent adjudication is pending.',
    'Synthetic gold was written before input generation.',
    'OmniDocBench pages are the fixed existing sample; full official evaluation uses the separate original benchmark.']}
if len({c['id'] for c in cases}) != len(cases): raise ValueError('Duplicate case IDs')
write(out/'manifest.json',manifest)
print(json.dumps({'output':str(out),'cases':len(cases),'manifest_sha256':sha(out/'manifest.json')},ensure_ascii=False))
