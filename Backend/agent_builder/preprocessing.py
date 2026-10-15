"""Pilot-data adapter. Training/evaluation examples never become user workflows automatically."""
import copy,hashlib,importlib.util,json
from pathlib import Path
from .models import AgentDraft,NodeSettings
from .workflow import workflow_errors
from .scheduling import schedule_errors

CODE_ROOT=Path(__file__).resolve().parents[1]/'integrations/workflow_preprocessing'
DATA_ROOT=Path(__file__).resolve().parents[2]/'docs/examples/workflow_preprocessing'
ROOT=DATA_ROOT
spec=importlib.util.spec_from_file_location('a4b_pilot_preprocessing',CODE_ROOT/'build_dataset.py')
pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot)

def adapt_example(row):
    """Translate IDs/ports, never convert unsupported triggers into manual execution."""
    try:errors=pilot.validate(row)
    except (KeyError,TypeError,ValueError,AttributeError):errors=['malformed_pilot_record']
    if errors:return {'status':'invalid','issues':errors,'draft':None,'resources':[]}
    target=row['target']
    if target['status']=='needs_clarification':return {'status':'needs_clarification','issues':target['missing_fields'],'questions':target['questions'],'draft':None,'resources':[]}
    wf=target['workflow'];trigger=wf['trigger'];unsupported=[]
    if trigger['type'] not in ['manual','schedule']:unsupported.append('현재 실행 어댑터는 manual/schedule만 지원: '+trigger['type'])
    mapping={'read_records':'read_table','filter_records':'filter_sort','aggregate_records':'aggregate_table','save_report':'write_result'}
    for n in wf['nodes']:
        if n['tool_id'] not in mapping:unsupported.append('지원하지 않는 파일럿 도구: '+n['tool_id'])
    if unsupported:return {'status':'unsupported','issues':unsupported,'draft':None,'resources':[]}
    sources={n['inputs']['source_id'] for n in wf['nodes'] if n['tool_id']=='read_records'}
    if len(sources)!=1:return {'status':'unsupported','issues':['현재 표 실행은 단일 소스만 지원합니다.'],'draft':None,'resources':[]}
    source=next(iter(sources));schema=row['context']['sources'][source];asset_id='pilot_'+source
    resources=[{'id':asset_id,'name':source+'.json','kind':'table','format':'json','columns':list(schema),'profile':{},'preview':[],'text':''}]
    plan={'filters':[],'sort':[],'match':'all','missing_policy':'keep','duplicate_policy':'keep','duplicate_field':None,'output_format':'csv'}
    nodes=[]
    for n in wf['nodes']:
        tool=mapping[n['tool_id']];args=n['inputs'];settings=NodeSettings(file_ids=[],query=None,fields=[],instructions=None,format=None,question=None,condition_field=None,condition_operator=None).model_dump();bindings=[]
        if tool=='read_table':settings['file_ids']=[asset_id]
        else:
            ref=args['content' if tool=='write_result' else 'rows']['ref'];bindings=[{'input':'table','node_id':ref['node_id'],'output':'table'}]
        if tool=='filter_sort':
            c=args['condition'];type=schema[c['field']]
            plan['filters'].append({'field':c['field'],'operator':c['operator'],'value':c.get('value'),'compare_field':c.get('other_field'),'value_type':'number' if type in ['number','integer'] else 'boolean' if type=='boolean' else 'text'})
        if tool=='aggregate_table':settings.update(aggregation=args['operation'],sum_field=args['field'] if args['operation']=='sum' else None)
        if tool=='write_result':
            if args['destination_id']!='reports':return {'status':'unsupported','issues':['서버 reports 이외 목적지는 연결 필요'],'draft':None,'resources':[]}
            settings['format']='csv'
        nodes.append({'id':n['id'],'tool':tool,'label':tool,'bindings':bindings,'settings':settings})
    schedule=None
    if trigger['type']=='schedule':
        hour,minute=map(int,trigger['time'].split(':'));schedule={'frequency':trigger['frequency'],'hour':hour,'minute':minute,'timezone':trigger['timezone'],'weekday':trigger.get('weekday')}
    draft=AgentDraft(name=row['id'],purpose=row['request'],instructions=row['request'],tool_ids=list(dict.fromkeys(n['tool'] for n in nodes)),document_ids=[asset_id],requirements=[{'description':'합성 평가 요청','source_id':'user','quote':row['request'][:2000]}],questions=[],unsupported=[],explanation='파일럿 규격을 현재 등록 노드 규격으로 변환. 학습·모델 생성 결과가 아닙니다.',execution_plan=plan,workflow={'entry':nodes[0]['id'],'nodes':nodes,'edges':[{'source':e['from'],'target':e['to'],'branch':'next'} for e in wf['edges']]},schedule=schedule,report_destination='reports').model_dump()
    issues=workflow_errors(draft['workflow'],draft['tool_ids'],resources)+(schedule_errors(schedule) if schedule else [])
    return {'status':'invalid' if issues else 'compatible','issues':issues,'draft':draft,'resources':resources,'adapter_policy':{'missing':'keep','duplicates':'keep','output':'csv','reason':'파일럿에 명시되지 않은 실행 정책. 평가 변환용이며 사용자의 생성 기본값이 아님.'}}

class DatasetStore:
    def __init__(self,storage):
        self.db=storage.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS preprocessing_batches(id TEXT PRIMARY KEY,source_path TEXT NOT NULL,contract_version TEXT NOT NULL,provenance TEXT NOT NULL,human_review TEXT NOT NULL,summary_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preprocessing_examples(batch_id TEXT NOT NULL,example_id TEXT NOT NULL,scenario_group TEXT NOT NULL,split TEXT NOT NULL,content_hash TEXT NOT NULL,raw_json TEXT NOT NULL,clean_json TEXT NOT NULL,target_json TEXT NOT NULL,adapted_json TEXT NOT NULL,compatibility TEXT NOT NULL,issues_json TEXT NOT NULL,PRIMARY KEY(batch_id,example_id),UNIQUE(batch_id,content_hash),FOREIGN KEY(batch_id) REFERENCES preprocessing_batches(id));
        CREATE INDEX IF NOT EXISTS preprocessing_split ON preprocessing_examples(batch_id,split,compatibility);
        ''');self.db.commit()
    def import_directory(self,directory=ROOT):
        directory=Path(directory);split_by_id={};group_split={}
        for split in ['train','validation','test']:
            for line in (directory/(split+'.jsonl')).read_text(encoding='utf-8').splitlines():
                row=json.loads(line);group=row['scenario_group']
                if row['id'] in split_by_id or group in group_split and group_split[group]!=split:raise ValueError('데이터 분할 간 중복 또는 시나리오 누출이 있습니다.')
                split_by_id[row['id']]=(split,row);group_split[group]=split
        raw_path=directory/'raw_requests.jsonl'
        rows=[json.loads(l) for l in (raw_path if raw_path.exists() else directory/'clean_requests.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        originals={};fingerprints=set();examples=[];duplicates=0
        for raw in rows:
            clean=copy.deepcopy(raw);clean['request']=pilot.normalize(clean.get('request',''))
            fingerprint=hashlib.sha256(json.dumps([clean['request'],clean['context'],clean['target']],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if fingerprint in fingerprints:duplicates+=1;continue
            fingerprints.add(fingerprint)
            if clean['id'] in originals:raise ValueError('서로 다른 사례가 같은 ID를 사용합니다.')
            if clean['id'] not in split_by_id:raise ValueError('분할에 없는 사례: '+clean['id'])
            split,split_row=split_by_id[clean['id']]
            clean['split']=split
            if clean!=split_row:raise ValueError('원본 정제 결과와 분할 파일이 다릅니다: '+clean['id'])
            originals[clean['id']]=raw;adapted=adapt_example(clean);examples.append((clean,raw,split,fingerprint,adapted))
        if set(originals)!=set(split_by_id):raise ValueError('분할 파일과 사례 집합이 다릅니다.')
        batch=hashlib.sha256(json.dumps([e[0] for e in examples],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        from collections import Counter
        summary={'raw_rows':len(rows),'clean_rows':len(examples),'duplicates_removed':duplicates,'compatibility':dict(Counter(e[4]['status'] for e in examples)),'split_rows':dict(Counter(e[2] for e in examples)),'cross_split_group_overlap':0,'actual_llm_runs':0,'human_review':'pending','provenance':'synthetic','batch_id':batch}
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT INTO preprocessing_batches VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING',(batch,str(directory),'workflow-draft-v0.1','synthetic','pending',json.dumps(summary,ensure_ascii=False)))
            for clean,raw,split,fingerprint,adapted in examples:
                dump=lambda v:json.dumps(v,ensure_ascii=False)
                self.db.execute('INSERT INTO preprocessing_examples VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(batch_id,example_id) DO NOTHING',(batch,clean['id'],clean['scenario_group'],split,fingerprint,dump(raw),dump(clean),dump(clean['target']),dump(adapted),adapted['status'],dump(adapted['issues'])))
            self.db.commit()
        except Exception:self.db.rollback();raise
        return summary
