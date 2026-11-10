"""A request that points at an uploaded file with nothing attached is answered without the model."""
import pytest
from langgraph.types import Command
from agent_builder.design.attachment_precheck import refers_to_attached_file, missing_attachment_reply, QUESTION
from agent_builder.design.graph import build_graph
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import initial, draft

RECEIPT='올려준 영수증 이미지 파일 내용을 \n엑셀파일로 정리해줘'


@pytest.mark.parametrize('text',[RECEIPT,'첨부한 PDF에서 금액만 뽑아줘','업로드한 엑셀 표를 지역별로 합계 내줘','보낸 사진 속 글자를 정리해줘'])
def test_upload_references_are_recognised(text):
    assert refers_to_attached_file(text)


@pytest.mark.parametrize('text',['매일 10시에 들어오는 매출 파일을 합계 내줘','실행할 때 넣을게요','파일을 올리면 금액을 정리해줘',
                                 '나중에 첨부한 파일 기준으로 정리해줘','지역별 매출 합계를 구해줘','엑셀 판매 표에서 지역별 합계를 구해줘'])
def test_runtime_or_plain_requests_go_to_the_planner(text):
    assert not refers_to_attached_file(text)


def test_gmail_attachment_is_distinct_from_an_a4b_upload():
    assert not refers_to_attached_file('Gmail 메일의 본문과 첨부파일을 읽어줘.')
    assert not refers_to_attached_file('Gmail 보낸 사람에게 답장 초안을 작성해줘. 파일 출력은 필요 없어.')
    assert refers_to_attached_file('메일의 첨부파일을 읽고 올린 PDF와 비교해줘.')


def state(text,resources=()):
    return {**initial(),'messages':[{'role':'user','content':text}],'resources':list(resources)}


def test_attached_file_or_switch_off_skips_the_shortcut(monkeypatch):
    assert missing_attachment_reply(state(RECEIPT,[{'id':'a','kind':'document'}])) is None
    monkeypatch.setenv('A4B_ATTACHMENT_PRECHECK','off')
    assert missing_attachment_reply(state(RECEIPT)) is None


def test_graph_asks_for_the_file_without_calling_the_planner(tmp_path):
    store=Storage(tmp_path);s=state(RECEIPT);cfg={'configurable':{'thread_id':s['session_id']}}
    graph,connection=build_graph(store,conversation=lambda st:pytest.fail('planner must not run without the file'))
    result=graph.invoke(s,cfg)
    assert result['status']=='needs_clarification'
    assert result['__interrupt__'][0].value['kind']=='clarification'
    assert result['draft']['questions']==[QUESTION] and result['messages'][-1]['role']=='assistant'
    assert not store.agents()
    connection.close();store.close()


def test_answer_for_a_later_run_continues_to_the_planner(tmp_path):
    store=Storage(tmp_path);s=state(RECEIPT);cfg={'configurable':{'thread_id':s['session_id']}}
    calls=[]
    def planner(st):
        calls.append(st['messages'][-1]['content'])
        return {'draft':draft(['어떤 열로 정리할까요?']),'confirmed':False,'saved_agent_id':None}
    graph,connection=build_graph(store,conversation=planner)
    graph.invoke(s,cfg)
    graph.invoke(Command(resume={'type':'message','content':'실행할 때 넣을게요'}),cfg)
    assert calls==['실행할 때 넣을게요']
    connection.close();store.close()


def test_later_run_choice_is_preserved_when_the_user_adds_a_contract_condition(tmp_path):
    store=Storage(tmp_path)
    s=state('첨부한 계약서 PDF를 검토해줘')
    cfg={'configurable':{'thread_id':s['session_id']}}
    calls=[]
    def planner(st):
        calls.append(st['messages'][-1]['content'])
        return {'draft':draft(['검토 결과는 어떤 형식으로 정리할까요?']),
                'confirmed':False,'saved_agent_id':None}
    graph,connection=build_graph(store,conversation=planner)
    try:
        graph.invoke(s,cfg)
        graph.invoke(Command(resume={'type':'message','content':'실행할 때 넣을게요'}),cfg)
        result=graph.invoke(Command(resume={'type':'message','content':'첨부한 계약서 PDF의 해지 조건도 검토해줘'}),cfg)
        assert calls==['실행할 때 넣을게요','첨부한 계약서 PDF의 해지 조건도 검토해줘']
        assert QUESTION not in result['draft']['questions']
        assert not store.agents()
    finally:
        connection.close();store.close()
