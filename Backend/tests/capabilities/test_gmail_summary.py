"""The recent-N Gmail recipe is a complete bounded request, never a keyword permit."""
from copy import deepcopy

import pytest
from langgraph.types import Command

from agent_builder.capabilities.capability_gate import require_save_ready
from agent_builder.capabilities.capability_judgement import assess_capability
from agent_builder.design.gmail_summary import gmail_summary_request, gmail_summary_reply, requested_count
from agent_builder.design.graph import build_graph
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.nodes.validation import validate_node
from infrastructure.persistence.storage import Storage
from tests.conditions.test_condition_readiness import state


REQUEST = '지메일에서 최근 5개 메일 내용 요약해서 정리해줘'


class Forbidden:
    def invoke(self,*args,**kwargs):
        raise AssertionError('This registered bounded recipe needs no design model call.')


@pytest.mark.parametrize('text,count',[
    (REQUEST,5), ('Gmail에서 최신 1개의 메일을 요약해줘.',1),
    ('지메일 최근 10건 메일 본문을 요약해서 정리해주세요',10),
])
def test_registered_bounded_request_has_catalog_evidence_and_no_account_claim(text,count):
    current=state(text)
    result=assess_capability(current,Forbidden())
    assert result['decision']=='existing_tools'
    assert result['tool_ids']==['email.list','compose_answer']
    assert result['model_calls']==0 and result['registered_workflow']=='gmail_recent_summary.v1'
    assert result['missing_actions']==result['missing_conditions']==[]
    assert not result['allows_execution'] and not result['allows_generation']
    assert 'Gmail 읽기 권한 연결' in result['runtime_prerequisites']
    assert requested_count(text)==count


@pytest.mark.parametrize('text',[
    '슬랙에서 최근 5개 메시지 내용 요약해서 정리해줘',
    REQUEST+' 그리고 슬랙으로 보내줘', REQUEST+' 그리고 메일로 보내줘',
    '지메일에서 최근 5개 메일 내용 요약해서 정리해줘. 발송도 해줘.',
    '지메일에서 최근 11개 메일 내용 요약해서 정리해줘',
    '지메일에서 최근 0개 메일 내용 요약해서 정리해줘',
    '지메일에서 최근 메일 내용 요약해서 정리해줘',
    '지메일에서 안 읽은 최근 5개 메일 내용 요약해서 정리해줘',
    '지메일에서 최근 5개 메일 내용 요약해서 엑셀로 저장해줘',
    '매일 지메일에서 최근 5개 메일 내용 요약해서 정리해줘',
])
def test_unknown_or_additional_work_never_enters_builtin_recipe(text):
    current=state(text)
    assert gmail_summary_request(current) is None
    assert gmail_summary_reply(current) is None


def test_recipe_requires_real_registered_contract_and_does_not_erase_earlier_work(monkeypatch):
    from workflow_engine.catalog import canonical
    current=state(REQUEST)
    current['messages'].insert(0,dict(role='user',content='자료를 요약한 뒤 Slack으로 보내줘'))
    assert gmail_summary_request(current) is None
    current=state(REQUEST)
    monkeypatch.setitem(canonical.OPERATIONS,'email.list',[
        operation for operation in canonical.OPERATIONS['email.list'] if operation['id']!='read_messages'])
    assert gmail_summary_request(current) is None


def test_summary_requirement_checks_mail_count_content_binding_and_actual_instructions():
    current=state(REQUEST)
    fixed=gmail_summary_reply(current)
    full={**current,**fixed}
    assert validate_node(full)['issues']==[]
    assert coverage_report(full,fixed['draft'])['issues']==[]
    for change in ('count','query','binding','instructions','no_summary'):
        altered=deepcopy(fixed['draft'])
        mail,summary=altered['workflow']['nodes']
        if change=='count':mail['settings']['arguments_json']='{"max_results":1}'
        elif change=='query':mail['settings']['arguments_json']='{"max_results":5,"query":"in:inbox"}'
        elif change=='binding':summary['bindings'][0]['output']='result'
        elif change=='instructions':summary['settings']['instructions']='메일 제목만 그대로 보여줘'
        else:altered['workflow']['nodes'].pop()
        assert coverage_report(full,altered)['issues'],change


def test_full_enforced_graph_builds_readonly_summary_and_requires_confirmation(tmp_path):
    store=Storage(tmp_path)
    current=state(REQUEST)
    forbidden=Forbidden()
    graph,connection=build_graph(store,conversation=lambda _:forbidden.invoke(),
        capability_mode='enforce',capability_transport=forbidden,
        condition_mode='enforce',condition_transport=forbidden,
        requirement_mode='enforce',requirement_transport=forbidden,verifier_mode='off')
    config={'configurable':{'thread_id':current['session_id']}}
    try:
        result=graph.invoke(current,config)
        assert result['status']=='ready' and not result['issues']
        assert result['__interrupt__'][0].value['kind']=='review'
        assert not result['pending_questions'] and not store.agents()
        assert result['requirement_required'] and result['requirement_coverage']['tasks'][0]['status']=='covered'
        assert result['draft']['tool_ids']==['email.list','compose_answer']
        assert result['draft']['document_ids']==[]
        assert len(result['draft']['workflow']['nodes'])==2
        assert not result['confirmed']
        with pytest.raises(ValueError,match='사용자 확인'):
            require_save_ready(result)
        saved=graph.invoke(Command(resume={'type':'confirm'}),config)
        assert saved['status']=='saved'
        assert store.agent(saved['saved_agent_id'])['config']['tool_ids']==['email.list','compose_answer']
    finally:
        connection.close()
        store.close()
