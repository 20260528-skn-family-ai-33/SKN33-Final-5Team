"""Preserve explicit execution-time file input across planning and saved runs."""
from copy import deepcopy
import json
import pytest
from agent_builder.design.attachment_precheck import QUESTION
from agent_builder.design.runtime_inputs import runtime_file_context, constrain_runtime_readers
from agent_builder.design.planner_constraints import native_decoder_schema
from agent_builder.design.nodes.conversation import make_conversation
from agent_builder.design.nodes.validation import validate_node
from contracts.models import AgentDraft
from tests.design.test_deep_agent_conversation import draft, state
from tests.workflow_engine.test_workflow import node, bind, edge, saved, run_workflow, FakeLLM, flow

PROMPT='첨부한 계약서 PDF 내용을 검토하고 사람이 확인하도록 구성해줘.'


def deferred():
    return {**state([{'role':'user','content':PROMPT},
                     {'role':'assistant','content':QUESTION},
                     {'role':'user','content':'실행할 때 넣을게요'}]),
            'session_id':'deferred-file-test','draft':draft(questions=[QUESTION])}


@pytest.mark.parametrize('answer',['실행할 때 넣을게요','파일은 나중에 업로드할게요','실행 시 제공할 자료입니다.'])
def test_user_defers_file_with_evidence(answer):
    s=deferred();s['messages'][-1]['content']=answer
    context=runtime_file_context(s)
    assert context['quote']==answer and context['file_delivery']=='at_execution'
    assert 'file_ids=[]' in context['instruction']


@pytest.mark.parametrize('messages',[
    [{'role':'user','content':PROMPT},{'role':'assistant','content':'실행할 때 넣을게요'}],
    [{'role':'user','content':'회의 날짜를 정해줘'},{'role':'user','content':'실행할 때 넣을게요'}],
    [{'role':'user','content':PROMPT},{'role':'user','content':'나중에 사람이 승인할게요'}],
    [{'role':'user','content':PROMPT},{'role':'user','content':'실행할 때 파일을 안 넣을게요'}],
])
def test_deferral_is_not_inferred_from_assistant_or_unrelated_answers(messages):
    assert runtime_file_context({'messages':messages}) is None


def test_new_file_choice_or_upload_supersedes_deferral():
    s=deferred();s['messages'].append({'role':'user','content':'파일은 지금 올릴게요'})
    assert runtime_file_context(s) is None
    s=deferred();s['resources']=[{'id':'actual','kind':'document'}]
    assert runtime_file_context(s) is None


def test_attachment_precheck_remembers_deferral_across_followup_turns():
    from agent_builder.design.attachment_precheck import missing_attachment_reply
    s=deferred()
    s['messages'].append({'role':'user','content':'첨부한 계약서 PDF의 해지 조건도 검토해줘'})
    assert missing_attachment_reply(s) is None
    s['messages'].append({'role':'user','content':'파일을 지금 제공할게요. 첨부한 계약서 PDF를 확인해줘'})
    assert missing_attachment_reply(s)['draft']['questions']==[QUESTION]


def test_only_deferred_readers_exclude_existing_asset_operations():
    schema=native_decoder_schema(AgentDraft.model_json_schema(),[])
    def operations(value):
        return [tool for v in value['$defs']['WorkflowNode']['anyOf'] if 'properties' in v
                for tool in v['properties']['tool']['enum'] if tool in ('read_table','read_document')]
    assert operations(schema)
    constrained=constrain_runtime_readers(schema,deferred())
    assert not operations(constrained)
    assert 'NativeNode_read_document' in constrained['$defs']
    assert operations(constrain_runtime_readers(schema,{'messages':[]}))==operations(schema)


def contract_workflow():
    return {'entry':'read','nodes':[node('read','read_document'),
        node('review','compose_answer',[bind('documents','read','documents')],instructions='계약 내용을 근거와 함께 검토하세요.'),
        node('human','ask_user',[bind('text','review','text')],question='계약 검토 결과를 확인하셨나요?')],
        'edges':[edge('read','review'),edge('review','human')]}


def test_deepagent_repairs_repeated_upload_question(monkeypatch):
    calls=[]
    def factory(**options):
        assert '실행 입력' in options['system_prompt']
        class Agent:
            def invoke(self,payload,config):
                calls.append(payload)
                context=json.loads(payload['messages'][0]['content'].split('\n',1)[1])
                assert context['runtime_file_input']['file_delivery']=='at_execution'
                assert context['current_draft'] is None
                if len(calls)==1:return {'structured_response':draft(questions=[QUESTION])}
                assert '파일은 실행할 때' in payload['messages'][-1]['content']
                return {'structured_response':draft(workflow=contract_workflow())}
        return Agent()
    monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    s=deferred();result=make_conversation('','test',model_factory=lambda:object())(s)
    assert len(calls)==2
    checked=validate_node({**s,**result})
    assert checked['issues']==[] and checked['status']=='ready'
    assert result['draft']['workflow']['nodes'][0]['settings']['file_ids']==[]
    assert result['draft']['workflow']['nodes'][-1]['tool']=='ask_user'


def test_deferral_keeps_real_missing_business_questions(monkeypatch):
    class Agent:
        def invoke(self,*args,**kwargs):return {'structured_response':draft(questions=['어떤 조항을 위험으로 판단할까요?'])}
    monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kw:Agent())
    result=make_conversation('','test',model_factory=lambda:object())(deferred())
    assert result['draft']['questions']==['어떤 조항을 위험으로 판단할까요?']
    assert result['draft']['workflow'] is None


def test_direct_repairs_repeated_file_request(monkeypatch):
    from tests.design.test_direct_conversation import Fake
    valid=draft(workflow=contract_workflow())
    valid['requirements']=[{'description':'계약 검토','source_id':'user','quote':PROMPT}]
    wrong=deepcopy(valid);wrong['questions']=['계약서 PDF 파일은 무엇인가요?'];wrong['workflow']=None
    fake=Fake(wrong,valid)
    monkeypatch.setenv('A4B_PLANNER_MODE','direct');monkeypatch.setenv('A4B_PLANNER_PROMPT','v8')
    s=deferred();result=make_conversation('','test',model_factory=lambda:fake)(s)
    assert len(fake.calls)==2 and not validate_node({**s,**result})['issues']
    assert '실행할 때' in fake.calls[-1][-1]['content']


def test_saved_document_workflow_accepts_file_only_at_execution(tmp_path):
    store,agent_id,asset=saved(tmp_path,flow())
    assert store.agent(agent_id)['config']['document_ids']==[]
    assert store.agent(agent_id)['config']['workflow']['nodes'][0]['settings']['file_ids']==[]
    result=run_workflow(store,agent_id,'실행 때 받은 문서를 처리','unused',file_ids=[asset['id']],llm=FakeLLM())
    assert result['status']=='succeeded' and result['artifact']
    assert json.loads(result['artifact']['content'])[0]['value']=='테스트'
    assert store.agent(agent_id)['config']['document_ids']==[]
    store.close()


@pytest.mark.parametrize('mode',['direct','deepagent'])
@pytest.mark.parametrize('question',[
    'CSV 파일에서 집계할 열을 선택해 주세요.',
    '계약서에서 검토할 기준을 알려주세요.',
])
def test_deferred_file_allows_business_questions_that_mention_the_file(monkeypatch,mode,question):
    from tests.design.test_direct_conversation import Fake
    answer=draft(questions=[question])
    if mode=='direct':answer['requirements']=[{'description':'계약 검토','source_id':'user','quote':PROMPT}]
    fake=Fake(answer)
    class Agent:
        def invoke(self,*args,**kwargs):return {'structured_response':answer}
    monkeypatch.setenv('A4B_PLANNER_MODE',mode)
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v8')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kw:Agent())
    result=make_conversation('','test',model_factory=lambda:fake)(deferred())
    assert result['draft']['questions']==[question]


@pytest.mark.parametrize('question',[
    'CSV 파일을 선택해 주세요.', '계약서 PDF를 첨부해 주세요.',
    '파일 경로를 알려주세요.', '파일의 ID를 제공해주세요.',
    '계약서 PDF 파일은 무엇인가요?',
    '집계할 열을 선택해 주세요. 파일도 업로드해 주세요.',
])
def test_deferred_file_still_rejects_file_or_locator_requests(question):
    from agent_builder.design.runtime_inputs import runtime_file_question_issues
    assert runtime_file_question_issues({'questions':[question]},deferred())
