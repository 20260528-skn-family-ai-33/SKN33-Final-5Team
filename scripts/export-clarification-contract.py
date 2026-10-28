"""Export proposed team schemas/examples. No model, server, installation or DB."""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Backend'))
from agent_builder.clarification_contract import (
    CONTRACT,ClarificationInput,ClarificationOutput,catalog_fingerprint,tool_catalog,to_builder_state,to_handoff,
)
from agent_builder.condition_readiness import source_catalog
from agent_builder.nodes.condition_check import make_condition_check

DEST=ROOT/'docs/clarification-handoff'

class Fixture:
    def __init__(self,raw):self.raw=raw;self.calls=0
    def invoke(self,messages):
        self.calls+=1
        return {'content':json.dumps(self.raw,ensure_ascii=False)}


def request(identifier,text,resources=None):
    return {'contract_version':CONTRACT,'request_id':identifier,'session_id':'sample-session','revision':0,
        'messages':[{'role':'user','content':text}],'resources':resources or [],
        'source_health':{'db_status':'ok','messages_loaded':True},'tool_catalog_fingerprint':catalog_fingerprint(),
        'ml_advice':{'status':'not_requested','ml_candidates':[],'model_revision':None}}


def run(payload, values=None, operation='filter', unsupported=None, conflict_keys=()):
    state=to_builder_state(payload,expected_session_id=payload['session_id'],expected_revision=payload['revision'])
    catalog,_=source_catalog(state)
    evidence=next(e['id'] for e in reversed(catalog) if e['source_id']=='user')
    raw={'operation':operation,'conditions':[{'key':k,'status':'conflict' if k in conflict_keys else 'missing' if v is None else 'specified',
        'value':None if v is None else str(v),'evidence_ids':
        [e['id'] for e in catalog if e['source_id']!='user'][:4] if k in conflict_keys else [] if v is None else [evidence]} for k,v in (values or {}).items()],
        'unsupported_actions':unsupported or []}
    fake=Fixture(raw)
    after={**state,**make_condition_check(transport=fake,mode='enforce')(state)}
    return to_handoff(payload,after).model_dump(mode='json'),after,fake.calls


def write(name,value):
    p=DEST/name;p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def export():
    write('input.schema.json',ClarificationInput.model_json_schema())
    write('output.schema.json',ClarificationOutput.model_json_schema())
    write('tool-catalog.json',{'fingerprint':catalog_fingerprint(),'tools':tool_catalog()})
    values={'filter.field':'비용','filter.operator':'gte','filter.value':'100000','missing.policy':'error','duplicate.policy':'keep','output.format':'json'}
    entries=[]
    def record(name,payload,values=None,**kwargs):
        result,state,calls=run(payload,values,**kwargs)
        write('examples/'+name+'.input.json',payload);write('examples/'+name+'.output.json',result)
        entries.append({'name':name,'decision':result['decision'],'fixture_transport_calls':calls,'real_model_calls':0})
        return state
    ready=request('ready','비용이 10만원 이상인 행만 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    record('01-ready',ready,values)
    missing=request('clarify','비용이 큰 행을 JSON으로 저장해줘. 어느 금액부터 큰지는 아직 정하지 않았어. 빈 값은 오류, 중복은 유지해.')
    unresolved={**values,'filter.operator':None,'filter.value':None}
    first=record('02-clarify',missing,unresolved)
    partial=request('partial','문서 기준으로 비용을 선별해줘.',[{'id':'doc-1','name':'규정.pdf','kind':'document','format':'pdf',
        'text':'일부 규정','pages':['일부 규정'],'parser_status':'partial','processing_status':'ready','version':1}])
    record('03-parser-error',partial)
    db=request('db-error','비용을 선별해줘.');db['source_health']={'db_status':'timeout','messages_loaded':False}
    record('04-db-error',db)
    record('05-unsupported',request('unsupported','비용 결과 파일을 이메일로 자동 발송해줘.'),unsupported=['등록 도구에 외부 이메일 자동 발송이 없습니다.'])
    deferred=deepcopy(missing);deferred.update(request_id='defer',revision=1)
    deferred['messages']=first['messages']+[{'role':'user','content':'나중에 결정할게'}]
    deferred['previous_state']={'session_id':deferred['session_id'],'revision':1,'ledger':first['condition_ledger'],
        'pending_questions':first['pending_questions'],'answer_events':first.get('condition_answers',[])}
    record('06-deferred',deferred)
    record('07-manual-review',request('scope','업무를 정리해줘.'),operation='other')
    answer=deepcopy(deferred);answer.update(request_id='answer',revision=1)
    answer['messages']=first['messages']+[{'role':'user','content':'20만원 초과로 해줘.'}]
    answer['answer_question_id']=next(q['id'] for q in first['pending_questions'] if q['key']=='filter.value')
    record('08-answer',answer,{'filter.operator':'gt','filter.value':'200000'})
    conflict=request('conflict','두 규정의 비용 기준은 모두 이상 조건인데 금액이 달라 아직 선택하지 않았어. JSON으로 저장하고 빈 값은 오류, 중복은 유지해.',[
        {'id':'policy-1','name':'규정1.txt','kind':'document','format':'txt','text':'비용 기준은 300000원 이상이다.','pages':['비용 기준은 300000원 이상이다.'],'version':1},
        {'id':'policy-2','name':'규정2.txt','kind':'document','format':'txt','text':'비용 기준은 500000원 이상이다.','pages':['비용 기준은 500000원 이상이다.'],'version':1}])
    record('09-conflict',conflict,{**values,'filter.value':None},conflict_keys=['filter.value'])
    write('examples/index.json',{'purpose':'연결 규격 예시, 실제 모델 성능 평가가 아님','human_agreement':'pending',
        'real_model_calls':0,'database_writes':False,'examples':entries})
    return entries

if __name__=='__main__':print(json.dumps(export(),ensure_ascii=False,indent=2))
