"""Synthetic pilot dataset. No production integrations or LLM inference.
Run: python build_dataset.py. Outputs are reproducible and local only.
"""
import copy
import csv
import hashlib
import html
import json
import random
import re
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = 'workflow-draft-v0.1'

def dump(name, obj):
    (ROOT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def jsonl(name, rows):
    (ROOT / name).write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')

TOOLS = {
    'read_records': {'description': '등록된 시험 데이터 소스의 레코드를 읽는다', 'inputs': {'source_id':'string'}, 'outputs': {'rows':'records'}, 'side_effect': False},
    'filter_records': {'description': '열과 상수 또는 다른 열을 비교해 행을 필터한다', 'inputs': {'rows':'records','condition':'condition'}, 'outputs': {'rows':'records'}, 'side_effect': False},
    'aggregate_records': {'description': '등록된 숫자 열의 합계 또는 레코드 수를 계산한다', 'inputs': {'rows':'records','operation':'string','field':'string'}, 'outputs': {'result':'object'}, 'side_effect': False},
    'save_report': {'description': '결과를 허용된 보고서 목적지에 저장한다', 'inputs': {'content':'object_or_records','destination_id':'string'}, 'outputs': {'artifact_id':'string'}, 'side_effect': True},
    'notify': {'description': '결과를 연결된 수신 대상에 알린다', 'inputs': {'content':'object_or_records','recipient_id':'string'}, 'outputs': {'delivery_id':'string'}, 'side_effect': True},
    'search_document': {'description': '등록된 문서 범위에서 질문의 근거를 검색한다', 'inputs': {'document_id':'string','query':'string'}, 'outputs': {'evidence':'records'}, 'side_effect': False}
}
SOURCES = {
    'inventory': {'schema': {'product':'string','stock':'integer','minimum':'integer'}, 'rows':[{'product':'A','stock':3,'minimum':10},{'product':'B','stock':10,'minimum':10},{'product':'C','stock':20,'minimum':10},{'product':'D','stock':0,'minimum':5}]},
    'sales': {'schema': {'product':'string','amount':'number'}, 'rows':[{'product':'A','amount':120},{'product':'B','amount':80}]},
    'applications': {'schema': {'id':'string','status':'string'}, 'rows':[{'id':'APP-1','status':'pending'},{'id':'APP-2','status':'approved'}]},
    'expenses': {'schema': {'id':'string','amount':'number'}, 'rows':[{'id':'E-1','amount':100},{'id':'E-2','amount':101},{'id':'E-3','amount':90}]},
    'attendance': {'schema': {'id':'string','absent':'boolean'}, 'rows':[{'id':'U-1','absent':True},{'id':'U-2','absent':False}]},
    'invoices': {'schema': {'id':'string','status':'string'}, 'rows':[{'id':'I-1','status':'unpaid'},{'id':'I-2','status':'paid'}]},
    'contacts': {'schema': {'id':'string','email':'string'}, 'rows':[{'id':'C-1','email':''},{'id':'C-2','email':'example@example.invalid'}]}
}
DOCS = {'manual_demo': {'description':'팀 작성 시험용 매뉴얼 조각. 실제 제품 안내 아님.', 'evidence':[{'page':1,'text':'시험 오류 E01: 연결 상태를 확인한다.'}]}}
DESTINATIONS = ['reports']
RECIPIENTS = ['test_team']

def ref(node, output):
    return {'ref': {'node_id':node,'output':output}}

def node(node_id, tool_id, **inputs):
    return {'id':node_id,'kind':'tool','tool_id':tool_id,'inputs':inputs}

def ready(trigger, nodes):
    return {'status':'ready','workflow':{'version':VERSION,'trigger':trigger,'nodes':nodes,'edges':[{'from':a['id'],'to':b['id']} for a,b in zip(nodes,nodes[1:])],'failure_policy':'stop_and_record'},'missing_fields':[],'questions':[]}

def filter_flow(source, condition, trigger, action='save_report'):
    end = node('n3', action, content=ref('n2','rows'), **({'destination_id':'reports'} if action=='save_report' else {'recipient_id':'test_team'}))
    return ready(trigger,[node('n1','read_records',source_id=source),node('n2','filter_records',rows=ref('n1','rows'),condition=condition),end])

def schedule(time, frequency='daily'):
    return {'type':'schedule','frequency':frequency,'time':time,'timezone':'Asia/Seoul'}

def cond(field, op, value=None, other=None):
    return {'field':field,'operator':op, **({'other_field':other} if other else {'value':value})}

SCENARIOS = [
 ('inventory_daily', 'inventory', [
 '한국 시간 매일 오전 9시에 inventory 재고표에서 stock이 minimum보다 작은 상품을 reports 폴더에 보고서로 저장해줘.',
 'inventory를 매일 09:00 Asia/Seoul에 확인해 stock < minimum인 행을 reports에 보고서로 남겨줘.',
 '매일 오전 9시(한국 시간), inventory의 현재 재고(stock)가 기준(minimum)보다 적은 상품만 추려 reports에 저장해줘.'
 ], filter_flow('inventory',cond('stock','lt',other='minimum'),schedule('09:00'))),
 ('sales_weekly','sales',[
 '매주 월요일 한국 시간 10시에 sales의 amount 합계를 계산해 reports에 저장해줘.',
 'sales 매출 합계 보고서를 매주 월요일 10:00 Asia/Seoul에 reports로 저장해줘. 합계 열은 amount야.',
 '한국 시간 월요일 오전 10시마다 sales 표의 amount를 모두 더해서 reports에 결과를 남겨줘.'
 ],ready({**schedule('10:00','weekly'),'weekday':0},[node('n1','read_records',source_id='sales'),node('n2','aggregate_records',rows=ref('n1','rows'),operation='sum',field='amount'),node('n3','save_report',content=ref('n2','result'),destination_id='reports')])),
 ('applications_event','applications',[
 'applications_changed 웹훅이 오면 applications 목록에서 status가 pending인 신청서를 reports에 저장해줘.',
 'applications_changed 이벤트 수신 시 applications의 pending 상태 행만 골라 reports에 보고서를 저장해줘.',
 'applications_changed가 들어올 때 applications를 조회해서 status = pending인 신청 내역을 reports에 남겨줘.'
 ],filter_flow('applications',cond('status','eq','pending'),{'type':'webhook','event_name':'applications_changed'})),
 ('expenses_manual','expenses',[
 '실행 버튼을 누르면 expenses에서 amount가 100보다 큰 내역만 reports에 저장해줘.',
 '수동 실행으로 expenses를 읽고 amount > 100인 행을 reports 보고서로 만들어줘.',
 '버튼 실행 시 expenses의 금액(amount)이 100을 초과한 항목만 reports에 남겨줘.'
 ],filter_flow('expenses',cond('amount','gt',100),{'type':'manual'})),
 ('attendance_daily','attendance',[
 '매일 한국 시간 오후 6시에 attendance에서 absent가 true인 항목을 test_team에 알려줘.',
 'attendance의 absent = true인 행을 매일 18:00 Asia/Seoul에 test_team으로 알림 보내줘.',
 '한국 시간 매일 18시에 attendance를 확인해 absent가 참인 항목만 test_team에게 전달해줘.'
 ],filter_flow('attendance',cond('absent','eq',True),schedule('18:00'),'notify')),
 ('invoices_event','invoices',[
 'invoices_updated 웹훅이 들어오면 invoices에서 status가 unpaid인 건을 test_team에게 알려줘.',
 'invoices_updated 이벤트마다 invoices의 unpaid 상태 내역을 test_team에 전달해줘.',
 'invoices_updated 수신 시 invoices를 읽어 status = unpaid인 행만 test_team으로 알림 보내줘.'
 ],filter_flow('invoices',cond('status','eq','unpaid'),{'type':'webhook','event_name':'invoices_updated'},'notify')),
 ('contacts_manual','contacts',[
 '실행 버튼을 누르면 contacts에서 email이 빈 문자열인 행을 reports에 저장해줘. 빈칸을 채우지는 마.',
 '수동으로 contacts의 email = 빈 문자열인 항목을 찾아 reports에 저장하고 값은 수정하지 마.',
 '버튼 실행 시 contacts 중 email이 비어 있는 행만 reports로 정리해줘. 원본은 유지해줘.'
 ],filter_flow('contacts',cond('email','eq',''),{'type':'manual'})),
 ('manual_chat','manual_demo',[
 '대화로 질문이 오면 manual_demo에서 근거를 검색해줘. 질문은 message에서 받아.',
 'message로 입력된 직원 질문을 manual_demo에서 검색하는 대화형 흐름을 만들어줘.',
 '대화 입력 message를 검색어로 사용해 manual_demo의 근거를 찾아줘.'
 ],ready({'type':'chat','input_schema':{'message':'string'}},[node('n1','search_document',document_id='manual_demo',query={'trigger_input':'message'})])),
 ('missing_schedule','inventory',[
 '매일 inventory에서 stock < minimum인 상품 보고서를 reports에 저장해줘.',
 'inventory를 매일 확인해 stock이 minimum보다 적은 항목을 reports에 남겨줘.',
 '매일 inventory의 부족 상품(stock < minimum)을 reports에 저장하도록 만들어줘.'
 ],{'status':'needs_clarification','workflow':None,'missing_fields':['schedule.time','schedule.timezone'],'questions':['매일 몇 시에 실행할까요?','어느 시간대를 사용할까요?']}),
 ('missing_document','none',[
 '대화로 들어온 질문을 매뉴얼에서 검색해줘. 아직 매뉴얼은 연결하지 않았어.',
 '직원 질문을 검색하는 대화형 흐름을 만들어줘. 사용할 매뉴얼이 아직 없어.',
 '질문을 받으면 매뉴얼 근거를 찾아줘. 문서는 아직 등록하지 않았어.'
 ],{'status':'needs_clarification','workflow':None,'missing_fields':['document_id'],'questions':['검색에 사용할 매뉴얼을 등록하거나 연결해 주세요.']})
]

def normalize(text):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFC',text)).strip()

def context_for(domain):
    return {'tool_registry_version':'tools-v0.1','tools':TOOLS,'sources':{domain:SOURCES[domain]['schema']} if domain in SOURCES else {},'documents':{domain:DOCS[domain]['description']} if domain in DOCS else {},'destinations':DESTINATIONS,'recipients':RECIPIENTS,'defaults':{}}

def validate(row):
    errors=[]
    if not isinstance(row.get('request'),str) or not row['request'].strip(): errors.append('empty_request')
    target=row.get('target',{})
    if target.get('status')=='needs_clarification':
        if target.get('workflow') is not None or not target.get('missing_fields') or not target.get('questions'): errors.append('invalid_clarification')
        return errors
    if target.get('status')!='ready': return errors+['invalid_status']
    wf=target.get('workflow')
    if not isinstance(wf,dict): return errors+['missing_workflow']
    trigger=wf.get('trigger',{})
    if trigger.get('type') not in ['manual','schedule','chat','webhook']: errors.append('invalid_trigger')
    if trigger.get('type')=='schedule':
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(trigger.get('time',''))): errors.append('invalid_time')
        if trigger.get('timezone')!='Asia/Seoul': errors.append('invalid_timezone')
        if trigger.get('frequency') not in ['daily','weekly']: errors.append('invalid_frequency')
        if trigger.get('frequency')=='weekly' and trigger.get('weekday') not in range(7): errors.append('invalid_weekday')
    if trigger.get('type')=='webhook' and not trigger.get('event_name'): errors.append('missing_event')
    nodes=wf.get('nodes',[])
    ids=[n.get('id') for n in nodes]
    if not nodes or len(ids)!=len(set(ids)): errors.append('invalid_node_ids')
    outputs={}; row_schemas={}; valid_edges=[]
    for n in nodes:
        tool=n.get('tool_id'); args=n.get('inputs',{})
        if tool not in TOOLS: errors.append('unknown_tool'); continue
        if set(args)!=set(TOOLS[tool]['inputs']): errors.append('input_keys_mismatch')
        for key,value in args.items():
            if isinstance(value,dict) and 'ref' in value:
                r=value['ref']; parent=r.get('node_id'); out=r.get('output')
                if parent not in outputs or out not in outputs.get(parent,{}): errors.append('invalid_reference'); continue
                source_type=outputs[parent][out]; need=TOOLS[tool]['inputs'].get(key)
                if source_type!=need and not (need=='object_or_records' and source_type in ['object','records']): errors.append('reference_type_mismatch')
                if key=='rows': row_schemas[n['id']]=row_schemas.get(parent,{})
            elif isinstance(value,dict) and 'trigger_input' in value:
                if value['trigger_input'] not in trigger.get('input_schema',{}): errors.append('invalid_trigger_input')
            elif TOOLS[tool]['inputs'].get(key)=='string' and not isinstance(value,str): errors.append('literal_type_mismatch')
        if tool=='read_records':
            sid=args.get('source_id')
            if sid not in row['context']['sources']: errors.append('unknown_source')
            row_schemas[n['id']]=row['context']['sources'].get(sid,{})
        if tool=='filter_records':
            c=args.get('condition',{}); schema=row_schemas.get(n['id'],{}); field=c.get('field')
            if field not in schema: errors.append('unknown_column')
            if c.get('operator') not in ['lt','gt','eq']: errors.append('invalid_operator')
            if ('value' in c)==('other_field' in c): errors.append('invalid_condition_operand')
            if 'other_field' in c and (c['other_field'] not in schema or schema.get(field)!=schema.get(c['other_field'])): errors.append('condition_field_type_mismatch')
            if c.get('operator') in ['lt','gt'] and schema.get(field) not in ['integer','number']: errors.append('non_numeric_comparison')
            if 'value' in c:
                v=c['value']; t=schema.get(field)
                if (t=='string' and not isinstance(v,str)) or (t=='boolean' and not isinstance(v,bool)) or (t in ['integer','number'] and (not isinstance(v,(int,float)) or isinstance(v,bool))): errors.append('condition_value_type_mismatch')
        if tool=='aggregate_records':
            if args.get('operation') not in ['sum','count']: errors.append('invalid_aggregation')
            if args.get('operation')=='sum' and row_schemas.get(n['id'],{}).get(args.get('field')) not in ['integer','number']: errors.append('invalid_aggregation_field')
        if tool=='save_report' and args.get('destination_id') not in row['context']['destinations']: errors.append('unknown_destination')
        if tool=='notify' and args.get('recipient_id') not in row['context']['recipients']: errors.append('unknown_recipient')
        if tool=='search_document' and args.get('document_id') not in row['context']['documents']: errors.append('unknown_document')
        outputs[n['id']]=TOOLS[tool]['outputs']
    valid_edges=[{'from':a,'to':b} for a,b in zip(ids,ids[1:])]
    if wf.get('edges')!=valid_edges: errors.append('invalid_linear_edges')
    if wf.get('version')!=VERSION or wf.get('failure_policy')!='stop_and_record': errors.append('invalid_contract')
    return errors

def mock_run(row):
    if row['target']['status']!='ready': return {'status':'not_run_clarification'}
    outputs={}; history=[]
    for n in row['target']['workflow']['nodes']:
        args=copy.deepcopy(n['inputs'])
        for k,v in list(args.items()):
            if isinstance(v,dict) and 'ref' in v: args[k]=outputs[v['ref']['node_id']][v['ref']['output']]
            elif isinstance(v,dict) and 'trigger_input' in v: args[k]='시험 E01'
        tool=n['tool_id']
        if tool=='read_records': out={'rows':copy.deepcopy(SOURCES[args['source_id']]['rows'])}
        elif tool=='filter_records':
            c=args['condition']; selected=[]
            for record in args['rows']:
                a=record[c['field']]; b=record[c['other_field']] if 'other_field' in c else c['value']
                if {'lt':lambda:a<b,'gt':lambda:a>b,'eq':lambda:a==b}[c['operator']](): selected.append(record)
            out={'rows':selected}
        elif tool=='aggregate_records': out={'result':{'value':sum(x[args['field']] for x in args['rows']) if args['operation']=='sum' else len(args['rows'])}}
        elif tool=='save_report': out={'artifact_id':'MOCK-ARTIFACT','content':args['content']}
        elif tool=='notify': out={'delivery_id':'MOCK-NO-SEND','content':args['content']}
        elif tool=='search_document': out={'evidence':DOCS[args['document_id']]['evidence'],'mock_search':True}
        outputs[n['id']]=out; history.append({'node_id':n['id'],'output':out})
    return {'status':'mock_completed','steps':history}

EXPECTED={'inventory_daily':['A','D'],'sales_weekly':200,'applications_event':['APP-1'],'expenses_manual':['E-2'],'attendance_daily':['U-1'],'invoices_event':['I-1'],'contacts_manual':['C-1'],'manual_chat':['시험 E01']}

def check_expected(group,result):
    if group=='manual_chat': return result['steps'][-1]['output']['evidence']==DOCS['manual_demo']['evidence']
    content=result['steps'][-1]['output']['content']
    if group=='sales_weekly': return content['value']==EXPECTED[group]
    return [r.get('product',r.get('id')) for r in content]==EXPECTED[group]

def main():
    raw=[]
    for group,domain,requests,target in SCENARIOS:
        for i,request in enumerate(requests):
            # Formatting variants are author-created, not discovered production defects.
            text=('  '+request+'\n') if i==0 else request
            raw.append({'id':f'{group}-{i+1}','scenario_group':group,'domain':domain,'request':text,'context':context_for(domain),'target':copy.deepcopy(target),'provenance':{'kind':'assistant_authored_synthetic','date':'2026-10-12','human_team_review':'pending','backend_alignment':'pending'}})
    for i in [0,3]:
        duplicate=copy.deepcopy(raw[i]); duplicate['id']+='-duplicate'; raw.append(duplicate)
    jsonl('raw_requests.jsonl',raw)
    changes=[]; clean=[]; seen=set()
    for r in raw:
        item=copy.deepcopy(r); item['request']=normalize(r['request'])
        if item['request']!=r['request']: changes.append({'id':r['id'],'action':'normalize_whitespace','before':r['request'],'after':item['request']})
        key=json.dumps({k:item[k] for k in ['request','context','target']},ensure_ascii=False,sort_keys=True)
        if key in seen: changes.append({'id':r['id'],'action':'drop_exact_duplicate'}); continue
        seen.add(key)
        errors=validate(item)
        if errors: raise AssertionError((item['id'],errors))
        clean.append(item)
    groups=[s[0] for s in SCENARIOS]; random.Random(42).shuffle(groups)
    split_map={g:('train' if i<6 else 'validation' if i<8 else 'test') for i,g in enumerate(groups)}
    for r in clean: r['split']=split_map[r['scenario_group']]
    jsonl('clean_requests.jsonl',clean)
    for split in ['train','validation','test']: jsonl(split+'.jsonl',[r for r in clean if r['split']==split])
    dump('change_log.json',changes); dump('tool_registry.json',{'version':'tools-v0.1','tools':TOOLS}); dump('mock_sources.json',SOURCES)
    dump('contract.json',{'version':VERSION,'status':['ready','needs_clarification'],'trigger_types':['schedule','manual','webhook','chat'],'reference_syntax':{'ref':{'node_id':'previous_node_id','output':'output_name'}},'scope':'linear DAG only; no branching, loop, approval or pause/resume in this pilot','ready_required':['workflow.version','workflow.trigger','workflow.nodes','workflow.edges','workflow.failure_policy'],'clarification_required':['missing_fields','questions'],'not_production_schema':True})
    mock=[]
    for r in clean:
        result=mock_run(r)
        if result['status']=='mock_completed': result['expected_match']=check_expected(r['scenario_group'],result); assert result['expected_match']
        mock.append({'id':r['id'],'scenario_group':r['scenario_group'],**result})
    dump('mock_execution_results.json',mock)
    # Separate intentionally corrupted fixtures test the validator; excluded from raw/clean counts.
    base=next(r for r in clean if r['scenario_group']=='inventory_daily')
    cases=[]
    for label,mutation in [
        ('unknown_tool',lambda r:r['target']['workflow']['nodes'][0].update(tool_id='invented_tool')),
        ('unknown_source',lambda r:r['target']['workflow']['nodes'][0]['inputs'].update(source_id='missing')),
        ('broken_reference',lambda r:r['target']['workflow']['nodes'][1]['inputs']['rows']['ref'].update(node_id='n99')),
        ('unknown_column',lambda r:r['target']['workflow']['nodes'][1]['inputs']['condition'].update(field='no_column')),
        ('invalid_time',lambda r:r['target']['workflow']['trigger'].update(time='25:61')),
        ('missing_input',lambda r:r['target']['workflow']['nodes'][2]['inputs'].pop('destination_id')),
        ('broken_edge',lambda r:r['target']['workflow'].update(edges=[])),
        ('empty_request',lambda r:r.update(request=''))
    ]:
        altered=copy.deepcopy(base); mutation(altered); errors=validate(altered); assert errors
        cases.append({'fixture':label,'intentionally_corrupted':True,'excluded_from_dataset':True,'detected_errors':errors})
    dump('validator_negative_tests.json',cases)
    summary={'raw_rows':len(raw),'clean_rows':len(clean),'exact_duplicates_removed':sum(x['action']=='drop_exact_duplicate' for x in changes),'whitespace_normalized_raw_rows':sum(x['action']=='normalize_whitespace' for x in changes),'empty_requests_after_cleaning':sum(not r['request'] for r in clean),'structural_errors_after_cleaning':sum(bool(validate(r)) for r in clean),'ready':sum(r['target']['status']=='ready' for r in clean),'clarification':sum(r['target']['status']=='needs_clarification' for r in clean),'split_rows':dict(Counter(r['split'] for r in clean)),'split_groups':{s:[g for g in groups if split_map[g]==s] for s in ['train','validation','test']},'cross_split_group_overlap':0,'mock_passed':sum(x.get('expected_match',False) for x in mock),'negative_tests_detected':len(cases),'actual_llm_runs':0,'production_integrations_tested':0,'human_review':'pending','schema':'proposed v0.1, not aligned with existing backend'}
    dump('summary.json',summary)
    with (ROOT/'dataset_review.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['id','scenario_group','split','request','status','human_review','target_json']);w.writeheader()
        for r in clean:w.writerow({'id':r['id'],'scenario_group':r['scenario_group'],'split':r['split'],'request':r['request'],'status':r['target']['status'],'human_review':'pending','target_json':json.dumps(r['target'],ensure_ascii=False)})
    # Model-neutral source of truth; selected tokenizer/chat template must be applied later.
    jsonl('sft_prompt_completion_draft.jsonl',[{'id':r['id'],'split':r['split'],'prompt':{'request':r['request'],'context':r['context'],'contract_version':VERSION},'completion':r['target']} for r in clean])
    readme='''# 워크플로우 생성 데이터 1차 전처리 초안\n\n## 목적\n사용자 요청과 실제로 제공 가능한 자원 명세를 입력으로 받아, 단계 연결을 포함한 워크플로우 초안 또는 확인 질문을 생성하는 학습 과제의 파일럿입니다. 특정 모델은 확정하지 않았습니다.\n\n## 출처와 한계\n모든 요청, 정답 설정, 도구 명세, 업무 레코드는 이 작업에서 새로 작성한 합성 자료입니다. 사용자 실제 로그나 기존 A4B 코드에서 추출한 자료가 아닙니다. 외부 자료 수집·개인정보 포함 없음. 이름·ID·주소는 시험용입니다. 원본의 공백과 중복도 작성 과정에서 만든 것이며 실제 서비스에서 발견한 결함으로 해석하면 안 됩니다. 팀의 사람 검수와 기존 백엔드 규격 대조는 미완료입니다.\n\n## 범위\n10개 시나리오 × 표현 3개 = 30개 고유 사례. 이 중 실행 구성 24개, 정보 부족 확인 6개. 예약·이벤트·수동·대화 시작을 포함합니다. 선형 단계 연결만 지원하며, 분기·반복·대기/재개·권한 집행·외부 연결·멀티 에이전트 실행은 이 파일럿에서 구현하지 않았습니다. 생성 도우미를 학습하는 데이터이며 모든 업무 실행에 에이전트가 있어야 하는 것은 아닙니다.\n\n## 정제 기준\nUnicode NFC 및 연속 공백 정리. 요청·자원·정답이 동일한 완전 중복은 첫 사례만 유지. 필수 요청·출력, 시간, 도구/자료/목적지 참조, 인자 키, 일부 리터럴 타입, 단계 참조의 출력 타입, 조건 열, 집계 열, 선형 연결을 검사합니다. 임의 코드나 조건 문자열 eval은 사용하지 않습니다. 요청에 정보가 없는 것은 불량이 아니라 needs_clarification 정답으로 유지합니다. 오탈자·의미 충돌·출처 정확성을 자동으로 완전히 검증하는 시스템은 아닙니다.\n\n## 분할\nseed 42, 시나리오 그룹 단위 6/2/2 분할, 각 18/6/6행. 같은 요청의 세 표현은 한 분할에만 들어갑니다. 이 분할은 작업 예시이며 표본이 작아 신뢰할 성능평가나 범용성 증명에 충분하지 않습니다. 테스트 결과를 보고 튜닝하지 말고, 규모 확장 전 업무와 도구 조합 분할을 다시 검토해야 합니다.\n\n## 모의 실행\n메모리 레코드 조회·필터·집계만 실제 Python 연산. 보고서 저장과 알림은 반환값만 만드는 mock이며 전송·생성 파일 없음. 문서 검색은 고정 근거 반환으로 검색 정확도 미검증. 실제 시간 예약, 웹훅 수신, 사용자 대화, LLM 추론, 모델 학습, 백엔드 실행을 수행하지 않았습니다.\n\n## 파일\nraw_requests.jsonl: 합성 원본 32행. clean_requests.jsonl: 정제된 30행. train/validation/test.jsonl: 그룹별 분리. dataset_review.csv: Excel로 열 수 있는 팀 검수표. tool_registry.json: 제안 도구 명세. contract.json: 임시 규약. change_log.json: 실제 정제 이력. summary.json: 계산된 통계. mock_execution_results.json: 모의 실행 결과. validator_negative_tests.json: 별도 의도적 오류 검증. sft_prompt_completion_draft.jsonl: 모델 중립 입력/정답 초안이며 토크나이저 적용 전.\n\n## 재현\nPython 표준 라이브러리만 사용. python build_dataset.py 실행. 정답은 생성 시나리오에 대해 검토 가능한 예시이며 자동 검사 통과가 사람이 검수한 정답을 의미하지 않습니다. 기존 백엔드 규약 확정 후 모든 설정과 도구 명세를 대조·변환하고 팀 검수해야 합니다. 모델 확정 후 해당 토크나이저·대화 템플릿·길이 제한·loss masking을 적용합니다.\n'''
    (ROOT/'README.md').write_text(readme,encoding='utf-8')
    rows=''.join('<tr><td>'+html.escape(str(k))+'</td><td>'+html.escape(str(v))+'</td></tr>' for k,v in summary.items())
    examples=''.join('<article><h3>'+html.escape(r['id'])+'</h3><p>'+html.escape(r['request'])+'</p><pre>'+html.escape(json.dumps(r['target'],ensure_ascii=False,indent=2))+'</pre></article>' for r in [clean[0],clean[6],clean[21],clean[24]])
    report=f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>워크플로우 생성 데이터 전처리 결과</title><style>body{{font-family:system-ui,sans-serif;max-width:1040px;margin:40px auto;padding:0 24px;color:#162334;line-height:1.7}}h1,h2{{color:#164c76}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccd5df;padding:9px;text-align:left}}pre{{white-space:pre-wrap;background:#eef3f8;padding:20px;font-size:13px}}.note{{background:#fff3d6;padding:18px;border-left:5px solid #e5a628}}article{{border-top:1px solid #ddd;margin-top:24px}}@media print{{body{{margin:0}}article{{break-inside:avoid}}}}</style><h1>워크플로우 생성 데이터<br>1차 전처리 결과</h1><p>2026-10-12 · 제안 규약 v0.1 · 합성 파일럿</p><div class="note">실제 정제·구조 검사와 모의 실행까지 수행했습니다. 모델 학습, 실제 백엔드 연결, 예약/이벤트 동작, 외부 API·알림 전송은 미검증입니다. 팀의 사람 검수와 백엔드 명세 대조가 필요합니다.</div><h2>1. 학습 과제</h2><p>입력: 사용자 요청 + 도구 명세 + 연결 자료 구조. 출력: 시작 조건·도구·인자·단계 연결을 포함한 설정, 또는 누락 정보와 확인 질문.</p><h2>2. 데이터 출처</h2><p>이 작업에서 작성한 합성 요청·정답 30건과 완전 중복 2건. 10개 시나리오의 표현 변형 3개씩입니다. 실제 사용자 로그·기존 데모 데이터가 아닙니다. 공백·중복은 합성 작성 과정에 포함된 특성입니다. 의도적 오류 검사 fixture는 데이터 통계에 포함하지 않습니다.</p><h2>3. 처리 기준</h2><p>공백·Unicode 정규화 → 완전 중복 제거 → 요청/정답 필수값 확인 → 도구·자원 참조 및 단계 연결 검사 → 그룹별 분할 → 모의 실행. 부족한 사용자 정보는 추측하지 않고 확인 질문으로 유지했습니다. 가짜 이상치를 추가해 정제 실적으로 계산하지 않았습니다.</p><h2>4. 실제 계산 결과</h2><table><tr><th>항목</th><th>결과</th></tr>{rows}</table><h2>5. 의미·실행 검증 범위</h2><p>정답의 의미는 작성된 요청과 대조 가능한 상태이며 팀 검수 대기입니다. 24건의 구성은 Python 시험 레코드와 mock 도구로 예상 결과를 확인했습니다. 검색은 고정 근거, 알림/저장은 모의 반환이므로 실제 검색·외부 연동 성공률이 아닙니다. 분기·반복·대기/재개·권한 분리는 본 검사 범위 밖입니다.</p><h2>6. 대표 사례</h2>{examples}<h2>7. 남은 작업</h2><p>백엔드 명세에 맞게 규약 조정 → 팀 정답 검수 → 도구·업무·표현 다양성 확대 → 선택 모델별 토큰화 → 기본/학습 모델 비교 → 실제 실행 시험. 표본 30건으로 모델 성능이나 범용성을 주장하지 않습니다.</p></html>'''
    (ROOT/'preprocessing_report.html').write_text(report,encoding='utf-8')
    archive=ROOT.parent/'workflow_preprocessing_2026-10-12.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for file in sorted(ROOT.iterdir()):
            if file.is_file(): z.write(file,'workflow_preprocessing/'+file.name)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print('Archive:',archive)

if __name__=='__main__': main()
