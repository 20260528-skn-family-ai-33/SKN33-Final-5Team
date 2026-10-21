"""팀 연동용 수작업 정답 사례를 JSON으로 내보낸다. 학습/test benchmark가 아님."""
import sys,json,copy,csv
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from a4b_verifier.adapters import load_registry

def draft(field='보증금',operator='gte',value=1000000,version='1'):
    return {'id':'draft-demo','version':version,'document_ids':['transactions'], 'workflow':{'entry':'read','nodes':[
        {'id':'read','tool':'read_table','bindings':[],'settings':{'file_ids':['transactions']}},
        {'id':'filter','tool':'filter_sort','bindings':[{'input':'table','node_id':'read','output':'table'}],
         'settings':{'execution_plan':{'filters':[{'field':field,'operator':operator,'value':value,'value_type':'number','compare_field':None}],
             'sort':[],'match':'all','missing_policy':'keep','duplicate_policy':'keep','duplicate_field':None,'output_format':'json'}}},
        {'id':'save','tool':'write_result','bindings':[{'input':'table','node_id':'filter','output':'table'}],'settings':{'format':'json'}}],
        'edges':[{'source':'read','target':'filter','branch':'next'},{'source':'filter','target':'save','branch':'next'}]}}

def main():
    out=ROOT/'examples';(out/'inputs').mkdir(parents=True,exist_ok=True);(out/'model_outputs').mkdir(exist_ok=True);(out/'source_files').mkdir(exist_ok=True)
    rows=[{'번호':'A','보증금':999999,'임대료':2000001},{'번호':'B','보증금':1000000,'임대료':100},
        {'번호':'C','보증금':2000000,'임대료':2000000},{'번호':'D','보증금':2000001,'임대료':0}]
    with (out/'source_files/transactions.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['번호','보증금','임대료']);w.writeheader();w.writerows(rows)
    (out/'source_files/policy.md').write_text('선별 기준과 대상 열은 사용자 요청 및 명확한 답변을 따른다. 미정 기준은 임의로 채우지 않는다.\n')
    base={'contract_version':'a4b.verifier.v1','request_id':'demo','user_request':'보증금 100만원 이상만 JSON으로 저장해줘.',
        'messages':[],'latest_user_answer_id':None,'documents':[{'id':'transactions','version':'1','parse_status':'ready',
            'chunks':[{'id':'header','text':'열: 번호, 보증금, 임대료. 기준은 사용자 요청에 따른다.','locator':'CSV header'}],
            'tables':[{'id':'main','columns':['번호','보증금','임대료'],'sample_rows':rows}]}],
        'tools':[t.model_dump(mode='json') for t in load_registry()],'previous_draft':None,'candidate_draft':draft()}
    def cite(x):
        if x['latest_user_answer_id']:return {'evidence_id':'message/answer-1','quote':x['messages'][-1]['content']}
        return {'evidence_id':'user_request','quote':x['user_request']}
    def good(x,status='ready',dv='pass',rv='not_applicable',missing=None,issue=None,unsupported=None):
        return {'request_status':status,'evidence_status':'sufficient','missing_conditions':missing or [],'unsupported_actions':unsupported or [],
            'draft_verdict':dv,'reflection_verdict':rv,'evidence':[cite(x)],'issues':issue or [],'summary':'정답 정책에 따라 상태와 초안 조건을 구분했습니다.'}
    def missing(x,code):return [{'code':code,'detail':'사용자에게 '+code+' 조건 확인이 필요합니다.','evidence':[cite(x)]}]
    def mismatch(x,path,expected,actual,reflection=False):return [{'code':'reflection_mismatch' if reflection else 'draft_mismatch','detail':'요청·답변의 조건과 초안이 다릅니다.',
        'path':path,'expected':expected,'actual':actual,'evidence':[cite(x)]}]
    cases=[]
    def add(name,x,y,route,kind='semantic',codes=None,rows_expected=None):
        x['request_id']=name
        (out/'inputs'/(name+'.json')).write_text(json.dumps(x,ensure_ascii=False,indent=2))
        (out/'model_outputs'/(name+'.json')).write_text(json.dumps(y,ensure_ascii=False,indent=2))
        cases.append({'id':name,'kind':kind,'input':'inputs/'+name+'.json','fixture':'model_outputs/'+name+'.json',
            'expected':{'route':route,'required_codes':codes or [],'request_status':y['request_status'],'draft_verdict':y['draft_verdict'],
                'reflection_verdict':y['reflection_verdict'],'missing_codes':[m['code'] for m in y['missing_conditions']],'row_ids':rows_expected}})
    valuepath='/workflow/nodes/1/settings/execution_plan/filters/0/value'
    fieldpath='/workflow/nodes/1/settings/execution_plan/filters/0/field'
    oppath='/workflow/nodes/1/settings/execution_plan/filters/0/operator'
    x=copy.deepcopy(base);add('01_ready',x,good(x),'continue_review',rows_expected=['B','C','D'])
    x=copy.deepcopy(base);x['user_request']='보증금이 큰 거래를 골라 JSON으로 저장해줘.'
    add('02_missing_selection',x,good(x,'clarify','fail',missing=missing(x,'selection_rule'),issue=mismatch(x,valuepath,'미확정','1000000')),'ask_user')
    x=copy.deepcopy(base);x['user_request']='금액 100만원 이상만 JSON으로 저장해줘.'
    add('03_ambiguous_column',x,good(x,'clarify','fail',missing=missing(x,'field'),issue=mismatch(x,fieldpath,'미확정','보증금')),'ask_user')
    x=copy.deepcopy(base);x['candidate_draft']=draft(field='임대료')
    add('04_wrong_column',x,good(x,dv='fail',issue=mismatch(x,fieldpath,'보증금','임대료')),'repair_plan')
    def follow(operator='gte',field='보증금',value=2000000,answer='보증금 기준으로 200만원 초과만 골라줘.'):
        x=copy.deepcopy(base);x['previous_draft']=draft();x['candidate_draft']=draft(field,operator,value,'2')
        x['messages']=[{'id':'ask-1','role':'assistant','content':'변경할 기준을 알려주세요.'},{'id':'answer-1','role':'user','content':answer}]
        x['latest_user_answer_id']='answer-1';return x
    x=follow();add('05_reflection_wrong_operator',x,good(x,dv='fail',rv='fail',issue=mismatch(x,oppath,'gt','gte',True)),'repair_plan')
    x=follow('gt');add('06_reflection_correct',x,good(x,rv='pass'),'continue_review',rows_expected=['D'])
    x=follow('gt','임대료');add('07_unrelated_field_changed',x,good(x,dv='fail',rv='fail',issue=mismatch(x,fieldpath,'보증금','임대료',True)),'repair_plan')
    x=follow(answer='좀 더 큰 거래만 골라줘.');x['user_request']='보증금이 큰 거래를 골라줘.'
    add('08_still_ambiguous',x,good(x,'clarify','fail','fail',missing(x,'selection_rule'),mismatch(x,valuepath,'미확정','2000000',True)),'ask_user')
    x=copy.deepcopy(base);x['user_request']='보증금 100만원 이상 거래를 이메일 발송까지 완료해줘. 발송이 필수야.';x['candidate_draft']['workflow']['nodes'][2]['tool']='send_email'
    add('09_unsupported_email',x,good(x,'unsupported','fail',issue=mismatch(x,'/workflow/nodes/2/tool','지원 불가','send_email'),unsupported=['필수 이메일 발송 도구 없음']),
        'unsupported',codes=['unavailable_tool'])
    x=copy.deepcopy(base);x['documents'][0].update(parse_status='failed',chunks=[],tables=[]);x['candidate_draft']=None
    y=good(x,status=None,dv='not_applicable');y['evidence_status']='insufficient'
    y['issues']=[{'code':'evidence_insufficient','detail':'파서 결과가 없어 실제 열과 문서 근거를 확인하지 못했습니다.','path':None,'expected':None,'actual':None,'evidence':[cite(x)]}]
    add('10_parser_failed',x,y,'manual_review',codes=['evidence_incomplete'])
    x=copy.deepcopy(base);x['candidate_draft']=draft(value=None)
    add('11_missing_filter_value',x,good(x,dv='fail',issue=mismatch(x,valuepath,'1000000','null')),'repair_plan',codes=['missing_filter_value'])
    x=copy.deepcopy(base);y=good(x);y['evidence']=[{'evidence_id':'document/not-real','quote':'가짜 원문'}]
    add('12_forged_evidence_guard',x,y,'manual_review','guard',['invalid_quote'])
    x=copy.deepcopy(base);x['candidate_draft']['workflow']['edges'].append({'source':'save','target':'read','branch':'next'})
    add('13_cycle_guard',x,good(x),'repair_plan','guard',['cycle'])
    (out/'cases.json').write_text(json.dumps({'purpose':'연동 계약·검증 예시. 학습/독립 test가 아닙니다. guard는 잘못된 모델 응답을 의도적으로 넣습니다.','cases':cases},ensure_ascii=False,indent=2))
    print(json.dumps({'cases':len(cases),'semantic':sum(c['kind']=='semantic' for c in cases),'guard':sum(c['kind']=='guard' for c in cases)},ensure_ascii=False))
if __name__=='__main__':main()
