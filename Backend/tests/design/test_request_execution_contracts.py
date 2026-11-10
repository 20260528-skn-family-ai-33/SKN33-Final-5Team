from copy import deepcopy

import pytest

from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from agent_builder.design.requirement_generation import generation_requirements
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.operation_steps import compile_workflow
from tests.design.test_deep_agent_conversation import draft, state
from tests.workflow_engine.test_workflow import node, bind, edge


def document_state():
    request='문서의 누락과 모순을 분석하고 근거와 수정 제안을 작성한 뒤 사람이 확인하게 해줘.'
    s=state([{'role':'user','content':request}])
    raw={'tasks':[{'source_message':0,'description':'문서 분석','spec':{'kind':'document_analysis'}},
                  {'source_message':0,'description':'사람 확인','spec':{'kind':'human_review'}}], 'unresolved':[]}
    plan=extract_plan(raw,s)
    s.update(requirement_required=True,requirement_ledger=plan,requirement_fingerprint=ledger_fingerprint(s),requirement_digest=plan_digest(plan))
    nodes=[node('read','read_document'),node('analysis','compose_answer',[bind('documents','read','documents')],instructions=request),
           node('human','ask_user',[bind('text','analysis','text')],question='분석 결과를 확인하셨나요?')]
    s['draft']=draft(workflow={'entry':'read','nodes':nodes,'edges':[edge('read','analysis'),edge('analysis','human')]})
    return s


def test_document_analysis_keeps_the_whole_request_and_connected_human_check():
    s=document_state()
    assert s['requirement_ledger']['tasks'][0]['spec']['request']==s['messages'][0]['content']
    assert not coverage_report(s,s['draft'])['issues']
    for mutation in ('instructions','disconnected','auto_skip'):
        bad=deepcopy(s['draft'])
        if mutation=='instructions':bad['workflow']['nodes'][1]['settings']['instructions']='문서 요약'
        if mutation=='disconnected':bad['workflow']['nodes'][2]['bindings']=[]
        if mutation=='auto_skip':bad['workflow']['nodes'][2]['settings']['instructions']='이미 요청했으면 확인 생략'
        assert coverage_report(s,bad)['issues']


def test_unknown_requirement_does_not_hide_known_calculation_contract():
    s=state([{'role':'user','content':'수량과 단가를 곱하고 별도 업무도 해줘.'}])
    raw={'tasks':[{'source_message':0,'description':'계산','spec':{'kind':'compute','formula':'수량*단가','output_column':'금액'}},
                  {'source_message':0,'description':'별도 업무','spec':{'kind':'other','action':'별도 업무'}}],'unresolved':[]}
    plan=extract_plan(raw,s)
    s.update(requirement_required=True,requirement_ledger=plan,requirement_fingerprint=ledger_fingerprint(s),requirement_digest=plan_digest(plan))
    specs,order=generation_requirements(s)
    assert [spec['kind'] for spec in specs]==['compute','other'] and order==[]
    assert coverage_report(s,draft())['issues']  # other is not waived


def test_ordered_output_must_exist_on_the_selected_predecessor():
    workflow={'first_step':{'tool':'read_document','label':'읽기','settings':{'file_ids':[]}},'next_steps':[
        {'tool':'extract_information','label':'추출','settings':{'fields':['제목'],'extraction_mode':'fields'},
         'bindings':[{'input':'documents','step':1,'output':'documents'}]},
        {'tool':'ask_user','label':'확인','settings':{'question':'확인'},
         'bindings':[{'input':'documents','step':2,'output':'documents'}]}]}
    with pytest.raises(ValueError,match='없는 출력 documents'):compile_workflow(workflow)
    workflow['next_steps'][1]['bindings']=[{'input':'records','step':2,'output':'records'}]
    assert compile_workflow(workflow)['nodes'][-1]['bindings'][0]['output']=='records'


def test_human_check_shows_the_actual_analysis_text_and_keeps_it_after_confirmation(monkeypatch):
    from workflow_engine.executors.control import ask_user
    seen=[]
    def respond(question):
        seen.append(question)
        return '확인했습니다'
    monkeypatch.setattr(ask_user,'interrupt',respond)
    n=node('human','ask_user',[bind('text','analysis','text')],question='검토 결과를 확인해주세요.')
    result=ask_user.execute(n,{'outputs':{'analysis':{'text':'시험 분석 결과'}},'messages':[]},None,{},None)
    assert seen[0]['preview']==[{'text':'시험 분석 결과'}]
    assert result=={'text':'시험 분석 결과','answer':'확인했습니다'}
