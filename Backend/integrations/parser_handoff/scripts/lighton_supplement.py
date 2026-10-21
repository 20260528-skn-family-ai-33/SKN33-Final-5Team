"""Opt-in, local GPU picture-text repair. Evidence and rejected candidates are retained.

Never rewrites tables/native PDF text or trusts a page-level VLM output wholesale.
Two-context agreement is a conservative heuristic, not a correctness guarantee.
"""
import copy, os, re, time, hashlib
from difflib import SequenceMatcher
from pathlib import Path
from pdf_text_supplement import normalized

REVISION='be7c41313c30e940fe5571de59e5fabc779e00c8'
_provider=None
def enabled():return os.environ.get('PDF_LIGHTON_ENABLED','0')=='1'
def key(s):return normalized(s).casefold()
def eligible(s):
    return 3<=len(key(s))<=48 and not re.search(r'\d',s) and any(c.isalpha() for c in s) and not re.search(r'(.)\1{3,}',key(s))
def similarity(a,b):return SequenceMatcher(None,key(a),key(b),autojunk=False).ratio()

def sparse_picture_supported(raw):
    """Require two credible text detections before expanding a sparse OCR picture."""
    return sum(eligible(t) and c>=.5 for _,t,c in raw)>=2

def numeric_ocr_replacement(previous,detected,candidate,confidence):
    """Only image OCR digits may yield to independent, high-confidence text agreement.

    Caller must also enforce native PDF exclusion, unique geometric overlap and
    agreement of two completed VLM reads. Pure numeric/new numeric output is excluded.
    """
    return (bool(re.fullmatch(r'\d{1,6}',previous.strip())) and confidence>=.95
            and eligible(candidate) and key(detected)==key(candidate))
def candidates(text):
    from bs4 import BeautifulSoup
    from markdown_it import MarkdownIt
    soup=BeautifulSoup(MarkdownIt('commonmark',{'html':True}).enable('table').render(text),'html.parser')
    found=[];seen=set()
    for tag in soup.find_all(['td','th','p','li','h1','h2','h3','h4']):
        s=tag.get_text(' ',strip=True).replace('**','').strip()
        if eligible(s) and key(s) not in seen:found.append(s);seen.add(key(s))
    return found
def agreement(candidate,verification,complete):
    return complete and any(key(x)==key(candidate) for x in candidates(verification))
def overlap(a,b):
    x=max(0,min(a[2],b[2])-max(a[0],b[0]));y=max(0,min(a[3],b[3])-max(a[1],b[1]))
    area=lambda r:max(0,r[2]-r[0])*max(0,r[3]-r[1])
    return x*y/max(1e-9,min(area(a),area(b)))
def top_box(prov,height):
    b=prov.bbox
    return [b.l,height-b.t,b.r,height-b.b] if str(b.coord_origin.value)=='BOTTOMLEFT' else [b.l,b.t,b.r,b.b]

def repetitive_suffix(tokens):
    """Stop short-crop decoding only after four exact cycles, at least 24 tokens.

    Input/prompt tokens must be excluded. An early stop is never a completed OCR
    result; normal text of arbitrary length still has the original token budget.
    """
    if len(tokens)<24:return False
    return any(tokens[-size:]*4==tokens[-4*size:]
               for size in range(1,min(24,len(tokens)//4)+1)
               if 4*size>=24)

class LocalProvider:
    def __init__(self):
        import torch,easyocr
        from transformers import LightOnOcrProcessor,LightOnOcrForConditionalGeneration
        if not torch.cuda.is_available():raise RuntimeError('LightOn supplementation requires CUDA')
        repo='lightonai/LightOnOCR-2-1B'
        self.processor=LightOnOcrProcessor.from_pretrained(repo,revision=REVISION,local_files_only=True,fix_mistral_regex=True)
        self.model=LightOnOcrForConditionalGeneration.from_pretrained(repo,revision=REVISION,local_files_only=True,dtype=torch.bfloat16,attn_implementation='sdpa').to('cuda').eval()
        self.reader=easyocr.Reader(['ko','en'],gpu=True,download_enabled=False)
        self.read_audit=[]
    def read(self,image,max_tokens):
        import torch
        from transformers import StoppingCriteria,StoppingCriteriaList
        inputs=self.processor.apply_chat_template([{'role':'user','content':[{'type':'image','image':image}]}],tokenize=True,add_generation_prompt=True,return_dict=True,return_tensors='pt')
        inputs={k:v.to(device='cuda',dtype=torch.bfloat16) if v.is_floating_point() else v.to('cuda') for k,v in inputs.items()}
        torch.cuda.synchronize();start=time.perf_counter()
        prompt_length=inputs['input_ids'].shape[1]
        class RepetitionGuard(StoppingCriteria):
            stopped=False
            def __call__(guard,input_ids,scores,**kwargs):
                generated=input_ids.shape[1]-prompt_length
                # Inspect a bounded suffix only after enough generated tokens.
                if generated>=24:
                    guard.stopped=repetitive_suffix(input_ids[0,-min(generated,96):].tolist())
                return torch.full((input_ids.shape[0],),guard.stopped,dtype=torch.bool,device=input_ids.device)
        guard=RepetitionGuard()
        with torch.inference_mode():out=self.model.generate(**inputs,max_new_tokens=max_tokens,max_time=60 if max_tokens>128 else 15,do_sample=False,stopping_criteria=StoppingCriteriaList([guard]))
        torch.cuda.synchronize()
        tokens=out[0,inputs['input_ids'].shape[1]:];text=self.processor.decode(tokens,skip_special_tokens=True)
        eos=self.model.generation_config.eos_token_id;eos=eos if isinstance(eos,list) else [eos]
        complete=bool(len(tokens)) and int(tokens[-1]) in eos and not guard.stopped
        seconds=time.perf_counter()-start
        self.read_audit.append(dict(tokens=len(tokens),max_tokens=max_tokens,complete=complete,seconds=seconds,stop_reason='repetition' if guard.stopped else 'eos' if complete else 'limit'))
        return text,complete,seconds
    def detect(self,image):
        import numpy as np
        return self.reader.readtext(np.array(image))

def supplement_picture_text(source,doc,provider=None,*,max_regions=12,max_candidates=32,time_budget=300):
    global _provider
    result=copy.deepcopy(doc)
    report=dict(engine='lighton-local',revision=REVISION,enabled=True,accepted=[],review=[],regions=[],errors=[],deferred=[],seconds=0)
    start=time.perf_counter()
    try:
        if provider is None:
            if _provider is None:_provider=LocalProvider()
            provider=_provider
        audit_start=len(getattr(provider,'read_audit',[]))
        import pymupdf,io
        from PIL import Image
        from docling_core.types.doc import DocItemLabel,ProvenanceItem,BoundingBox,CoordOrigin
        evidence_dir=os.environ.get('PDF_LIGHTON_AUDIT_DIR')
        with pymupdf.open(source) as pdf:
            nregions=0;nchecks=0;nretries=0
            for picture in result.pictures:
                if not picture.prov:continue
                p=picture.prov[0];n=p.page_no;page=pdf[n-1];height=page.rect.height
                box=top_box(p,height)
                # Detect text in substantial pictures even when Docling OCR missed it.
                # Empty detections cause no VLM calls; native-text protection remains below.
                children=[ref.resolve(result) for ref in picture.children]
                children=[x for x in children if getattr(x,'text','').strip()]
                if box[2]-box[0]<80 or box[3]-box[1]<25:continue
                if nregions>=max_regions or time.perf_counter()-start>time_budget:
                    report['deferred'].append(dict(page=n,picture=picture.self_ref));continue
                nregions+=1
                rect=pymupdf.Rect(box)+(-3,-3,3,3);rect=rect & page.rect
                def render(r,scale):
                    pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=r,alpha=False)
                    im=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB');im.thumbnail((1540,1540));return im
                low=render(rect,3);high=render(rect,6)
                raw=provider.detect(low)
                if len(children)<3 and not sparse_picture_supported(raw):
                    report['deferred'].append(dict(page=n,picture=picture.self_ref,reason='sparse_low_confidence_text'))
                    continue
                # Cheap OCR first: only absent or disagreeing text boxes reach LightOn.
                native_words=page.get_text('words')
                existing='\n'.join(x.text for x in result.texts if any(q.page_no==n for q in x.prov))
                proposals=[];elapsed=0;probes=[]
                for b,t,c in raw:
                    if not eligible(t) or (c>=.9 and key(t) in key(existing)):continue
                    anchor=[rect.x0+min(pt[0] for pt in b)*rect.width/low.width,rect.y0+min(pt[1] for pt in b)*rect.height/low.height,rect.x0+max(pt[0] for pt in b)*rect.width/low.width,rect.y0+max(pt[1] for pt in b)*rect.height/low.height]
                    if any(overlap(anchor,w[:4])>.25 for w in native_words):continue
                    if len(probes)>=max_candidates or time.perf_counter()-start>time_budget:break
                    # Wider context for proposal; the original tight crop still verifies it.
                    context=(pymupdf.Rect(anchor)+(-5,-3,5,3)) & page.rect
                    text,ok,secs=provider.read(render(context,6),128)
                    elapsed+=secs;probes.append(dict(ocr_text=t,bbox=anchor,output=text,complete=ok,seconds=secs))
                    if ok:proposals.extend(x for x in candidates(text) if similarity(x,t)>=.55)
                full='\n\n'.join(dict.fromkeys(proposals));complete=True
                region=dict(page=n,picture=picture.self_ref,bbox=list(rect),complete=complete,seconds=elapsed,raw_ocr=[dict(text=t,confidence=float(c),box=[[float(v) for v in pt] for pt in b]) for b,t,c in raw],output=full)
                region['probes']=probes
                report['regions'].append(region)
                if evidence_dir:
                    folder=Path(evidence_dir);folder.mkdir(parents=True,exist_ok=True)
                    high.save(folder/f'p{n}-region{nregions}.png')
                if not complete:
                    report['review'].append(dict(page=n,reason='incomplete_region'));continue
                native_words=page.get_text('words')
                for candidate in candidates(full):
                    existing='\n'.join(x.text for x in result.texts if any(q.page_no==n for q in x.prov))
                    if key(candidate) in key(existing):continue
                    matches=sorted([(similarity(candidate,t),b,t,c) for b,t,c in raw if eligible(t)],key=lambda x:x[0],reverse=True)
                    if not matches or matches[0][0]<.55:continue
                    score,b,old,confidence=matches[0]
                    if len(matches)>1 and matches[1][0]>=score-.08:
                        report['review'].append(dict(page=n,candidate=candidate,reason='ambiguous_ocr_match'));continue
                    anchor=[rect.x0+min(pt[0] for pt in b)*rect.width/low.width,rect.y0+min(pt[1] for pt in b)*rect.height/low.height,rect.x0+max(pt[0] for pt in b)*rect.width/low.width,rect.y0+max(pt[1] for pt in b)*rect.height/low.height]
                    if any(overlap(anchor,w[:4])>.25 for w in native_words):continue
                    if nchecks>=max_candidates or time.perf_counter()-start>time_budget:
                        report['deferred'].append(dict(page=n,candidate=candidate));continue
                    nchecks+=1
                    clip=(pymupdf.Rect(anchor)+(-2,-2,2,2)) & page.rect
                    local,ok,seconds=provider.read(render(clip,6),128)
                    entry=dict(page=n,picture=picture.self_ref,bbox=anchor,candidate=candidate,ocr_text=old,ocr_confidence=float(confidence),verification=local,verification_complete=ok,seconds=seconds)
                    # Only truncated verification gets one bounded, differently padded retry.
                    # Never salvage a prefix of an unfinished response or inject expected text.
                    if not ok and nretries<4 and time.perf_counter()-start<time_budget:
                        nretries+=1
                        retry_clip=(pymupdf.Rect(anchor)+(-3,-3,3,3)) & page.rect
                        retry_text,retry_ok,retry_seconds=provider.read(render(retry_clip,6),32)
                        entry['retry']=dict(output=retry_text,complete=retry_ok,seconds=retry_seconds,padding=3,max_tokens=32)
                        local,ok=retry_text,retry_ok
                    if not agreement(candidate,local,ok):
                        report['review'].append(dict(**entry,reason='local_disagreement'));continue
                    overlaps=[x for x in children if any(q.page_no==n and overlap(anchor,top_box(q,height))>.3 for q in x.prov)]
                    if len(overlaps)>1:
                        report['review'].append(dict(**entry,reason='multiple_existing_items'));continue
                    if overlaps:
                        target=overlaps[0]
                        numeric_repair=numeric_ocr_replacement(target.text,old,candidate,confidence)
                        if (similarity(target.text,old)<.65 or re.search(r'\d',target.text)) and not numeric_repair:
                            report['review'].append(dict(**entry,reason='existing_conflict'));continue
                        if numeric_repair:entry['replacement_evidence']='high_confidence_ocr_plus_two_vlm_reads'
                        entry.update(action='replace',previous=target.text,item=target.self_ref)
                        target.text=candidate
                    else:
                        # Nearby similar text without a matching location is ambiguous, not safe to append.
                        if any(similarity(candidate,x.text)>.8 for x in children):
                            report['review'].append(dict(**entry,reason='possible_duplicate'));continue
                        prov=ProvenanceItem(page_no=n,bbox=BoundingBox(l=anchor[0],t=anchor[1],r=anchor[2],b=anchor[3],coord_origin=CoordOrigin.TOPLEFT),charspan=(0,len(candidate)))
                        target=result.add_text(label=DocItemLabel.TEXT,text=candidate,prov=prov,parent=picture)
                        # Insert by position without reordering any pre-existing items.
                        ref=picture.children.pop()
                        position=next((i for i,r in enumerate(picture.children) if getattr(r.resolve(result),'prov',None) and top_box(r.resolve(result).prov[0],height)[1]>anchor[3]+2),len(picture.children))
                        picture.children.insert(position,ref)
                        children.append(target);entry.update(action='insert',item=target.self_ref)
                    report['accepted'].append(entry)
    except Exception as error:
        report['errors'].append(dict(type=type(error).__name__,message=str(error)[:200]))
        # Fail closed: do not return a partly mutated document on an execution error.
        result=copy.deepcopy(doc);report['rolled_back']=report['accepted'];report['accepted']=[]
    report['seconds']=time.perf_counter()-start
    report['generation']=getattr(provider,'read_audit',[])[audit_start:] if 'audit_start' in locals() else []
    if hasattr(provider,'read_audit'):provider.read_audit.clear()
    return result,report
