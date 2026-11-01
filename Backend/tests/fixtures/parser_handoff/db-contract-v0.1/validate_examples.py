"""Validate contract examples locally. Requires jsonschema; no DB or network."""
import json,hashlib,re,copy,sys
from importlib.metadata import version
from pathlib import Path
from collections import Counter
from jsonschema import Draft202012Validator
P=Path(__file__).resolve().parent
load=lambda p:json.loads(p.read_text(encoding='utf8'))
sha=lambda b:hashlib.sha256(b).hexdigest()

def require(condition,message):
    if not condition:raise ValueError(message)

def span(text,start,end,expected):
    require(isinstance(start,int) and isinstance(end,int) and 0<=start<end<=len(text),'invalid code-point range')
    require(text[start:end]==expected,'canonical text slice mismatch')

def validate_payload(p,schema):
    Draft202012Validator(schema).validate(p)
    text=p['canonical_text']
    require(sha(text.encode('utf8'))==p['canonical_text_sha256'],'canonical hash mismatch')
    proc=p['processing'];source=p['source']
    req=set(proc['requested_pages']);done=set(proc['completed_pages']);failed=set(proc['failed_pages'])
    require(done.isdisjoint(failed) and done|failed==req,'requested page accounting mismatch')
    require(all(1<=n<=source['page_count_total'] for n in req),'page outside original')
    require(proc['whole_document_complete']==(proc['scope']=='full_document' and not failed and done==set(range(1,source['page_count_total']+1))),'whole-document completeness mismatch')
    if proc['outcome']=='failed':require(proc['error'] is not None and not done and not text and not p['blocks'] and not p['tables'] and not p['source_locations'],'failed result must not masquerade as usable content')
    elif proc['outcome']=='succeeded':require(not failed and proc['error'] is None,'succeeded scope has errors')
    else:require(bool(done) and bool(failed),'partial scope must have done and failed pages')
    if 'embedding' in proc['config_json']:require(isinstance(proc['config_json']['embedding'],dict),'omit unused embedding config; null violates DB v2 profile')
    if source['mime_type'].startswith('image/'):require(source['page_count_total']==1 and req=={1},'single image uses page1')
    require({x['page_no'] for x in p['pages']}==done,'page metadata mismatch')
    require(len({x['page_no'] for x in p['pages']})==len(p['pages']),'duplicate page metadata')
    def box(b):
        if b is not None:require(0<=b[0]<b[2]<=1 and 0<=b[1]<b[3]<=1,'bad normalized bbox')
    targets={};previous=-1
    for block in p['blocks']:
        require(block['block_key'] not in targets,'duplicate block key')
        require(block['page_no'] in done,'block on unprocessed page')
        span(text,block['char_start'],block['char_end'],block['text']);box(block['bbox'])
        require(block['char_start']>=previous,'reading order reversed');previous=block['char_end']
        targets[block['block_key']]=block
    tablekeys=set()
    for table in p['tables']:
        require(table['table_key'] not in tablekeys,'duplicate table key');tablekeys.add(table['table_key'])
        require(table['block_key'] in targets,'table block missing');block=targets[table['block_key']]
        require(block['table_key']==table['table_key'],'table/block link mismatch')
        occupied=set()
        for cell in table['cells']:
            key=cell['cell_key'];require(key not in targets,'duplicate cell key')
            require(cell['row']+cell['row_span']-1<=table['row_count'] and cell['column']+cell['col_span']-1<=table['column_count'],'cell span outside grid')
            spots={(r,c) for r in range(cell['row'],cell['row']+cell['row_span']) for c in range(cell['column'],cell['column']+cell['col_span'])}
            require(not occupied&spots,'overlapping merged cells');occupied.update(spots)
            if cell['text']:
                span(text,cell['char_start'],cell['char_end'],cell['text'])
                require(block['char_start']<=cell['char_start']<cell['char_end']<=block['char_end'],'cell outside parent block')
                require(cell['location_no'] is not None,'nonempty cell location missing')
            else:require(cell['char_start'] is cell['char_end'] is cell['location_no'] is None,'empty cells must not fabricate evidence')
            box(cell['bbox']);targets[key]=cell
    for b in p['blocks']:
        require((b['type']=='table')==(b['table_key'] in tablekeys),'invalid text/table reference')
    locs={}
    for loc in p['source_locations']:
        require(loc['location_no'] not in locs,'duplicate location number');locs[loc['location_no']]=loc
        require(loc['page_no'] in done,'location on unprocessed page');box(loc['bbox'])
        require(loc['block_key'] in targets,'location target missing');target=targets[loc['block_key']]
        require(target['char_start']==loc['char_start'] and target['char_end']==loc['char_end'],'target/location ranges differ')
        require(target['bbox']==loc['bbox'],'target/location geometry differs')
        require(target['location_no']==loc['location_no'],'target/location number differs')
    for t in targets.values():
        if t.get('location_no') is not None:require(t['location_no'] in locs,'unlinked evidence target')
    return dict(blocks=len(p['blocks']),tables=len(p['tables']),cells=sum(len(t['cells']) for t in p['tables']),source_locations=len(locs),canonical_code_points=len(text))

def main():
    schema=load(P/'parser-result.schema.json');Draft202012Validator.check_schema(schema)
    p=load(P/'samsung-p67.parser-result.json');counts=validate_payload(p,schema)
    require((P/'samsung-p67.canonical.txt').read_bytes()==p['canonical_text'].encode('utf8'),'canonical file encoding differs')
    # Source cell text is preserved, including the note moved out of its old table.
    raw=load(P/'sources/docling-p67-document.json')
    old=Counter(c['text'] for t in raw['tables'] for c in t['data']['table_cells'] if c['text'])
    new=Counter(c['text'] for t in p['tables'] for c in t['cells'] if c['text'])
    new.update(b['text'] for b in p['blocks'] if b['role']=='footnote' and b['parser_source_ref'].startswith('#/tables/'))
    require(old==new,'source cell text changed')
    normalize=lambda s:re.sub(r'\s+','',s)
    label=lambda s:re.sub(r'\d+\)$','',normalize(s))
    refs=load(P/'sources/multilevel-reference.json')['checks'];passed=0
    for ch in refs:
        values=[]
        for t in p['tables'][2:]:
            rows={c['row'] for c in t['cells'] if c['column']==1 and label(c['text'])==label(ch['row'])}
            for c in t['cells']:
                path=list(map(normalize,c['header_path'] or []))
                expected=[normalize(ch['year'])]+([normalize(ch['tier'])] if ch['tier'] else [])
                if c['row'] in rows and path==expected:values.append(c['text'])
        require(values==[ch['value']],'composite header mismatch: '+str(ch));passed+=1
    chunk=load(P/'chunk-example.json');require(chunk['example_only'] and chunk['strategy_status']=='proposal_not_current_runtime','chunk status missing')
    require(sha(chunk['content'].encode('utf8'))==chunk['content_hash'],'chunk hash mismatch')
    require(chunk['canonical_text_sha256']==p['canonical_text_sha256'],'chunk references another canonical artifact')
    require(chunk['source_document_sha256']==p['source']['sha256'],'chunk source document mismatch')
    locs={x['location_no']:x for x in p['source_locations']}
    for segment in chunk['segments']:
        a,b=segment['source_char_start'],segment['source_char_end'];cs,ce=segment['content_start'],segment['content_end']
        span(p['canonical_text'],a,b,chunk['content'][cs:ce]);loc=locs[segment['location_no']]
        require(loc['char_start']==a and loc['char_end']==b,'chunk source link differs')
    require(chunk['location_nos']==list(dict.fromkeys(s['location_no'] for s in chunk['segments'])),'chunk link ordering differs')
    # Meaningful rejection cases; no model inference and no database mutation.
    negative=[]
    def reject(name,change):
        bad=copy.deepcopy(p);change(bad)
        try:validate_payload(bad,schema)
        except Exception:negative.append(name);return
        raise ValueError('invalid payload accepted: '+name)
    reject('bbox out of range',lambda b:b['blocks'][0].update(bbox=[0,0,1.2,1]))
    reject('wrong code-point offset',lambda b:b['blocks'][0].update(char_end=b['blocks'][0]['char_end']+1))
    reject('merged-cell overlap',lambda b:b['tables'][2]['cells'][0].update(col_span=7))
    reject('unknown location target',lambda b:b['source_locations'][0].update(block_key='missing'))
    reject('null embedding profile',lambda b:b['processing']['config_json'].update(embedding=None))
    span('a😀b',1,2,'😀')
    try:span('a😀b',1,3,'😀')
    except ValueError:negative.append('UTF16 offset mistaken for code points')
    else:raise ValueError('UTF16 mismatch accepted')
    failed=load(P/'failed.parser-result.example.json');validate_payload(failed,schema)
    report=dict(status='passed',schema='JSON Schema2020-12 plus semantic checks',validation_runtime=dict(python=sys.version.split()[0],jsonschema=version('jsonschema')),counts=counts,composite_relations=dict(passed=passed,total=len(refs)),source_cell_text_preserved=True,chunk_segments=len(chunk['segments']),failure_example_valid=True,rejection_cases=negative,limits=['No live RDS/S3 write or SQL migration.','Not a production parser API integration.','Coordinates are converted from stored Docling provenance, not independently human adjudicated.','175 value mappings cover the two compliance tables, not every flag or field in the page.'])
    (P/'validation-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
