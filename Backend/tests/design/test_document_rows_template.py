"""Document/photo -> one row per item -> CSV, fixed in code instead of planned by the model."""
import csv
import io
import pytest
from langgraph.types import Command
from agent_builder.design.document_rows_template import requested_fields, QUESTION
from agent_builder.design.graph import build_graph
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import initial
from workflow_engine.executors.document import extract_information
from workflow_engine.executors.output import export_result
from workflow_engine.executors.schemas import Rows

REQUEST='올려준 영수증 이미지 파일 내용을 엑셀파일로 정리해줘'
RECEIPT='001P굿모닝우유900ML\n[2.150]\n*8801104210645\n1.350\n1\n002 P양파\n1.350\n1\n1.350\n판매총액:\n8,560'
DOC={'id':'receipt','kind':'document','name':'receipt.jpg','format':'jpg','text':RECEIPT,'pages':[RECEIPT],'version':1}


def no_planner(state):
    pytest.fail('the fixed flow must not call the planning model')


def test_field_answers_are_split_and_long_sentences_are_rejected():
    assert requested_fields('상품명, 단가, 수량, 금액')==['상품명','단가','수량','금액']
    assert requested_fields('상품명·금액')==['상품명','금액']
    assert requested_fields('상품명 및 금액')==['상품명','금액']
    assert requested_fields('음 잘 모르겠는데 알아서 영수증에 있는 거 전부 다 뽑아주면 좋겠어요')==[]


def test_attached_document_asks_fields_then_builds_the_fixed_flow_without_the_model(tmp_path):
    store=Storage(tmp_path);s={**initial(),'messages':[{'role':'user','content':REQUEST}],'resources':[DOC]}
    cfg={'configurable':{'thread_id':s['session_id']}}
    graph,connection=build_graph(store,conversation=no_planner)
    first=graph.invoke(s,cfg)
    assert first['status']=='needs_clarification' and first['draft']['questions']==[QUESTION]
    done=graph.invoke(Command(resume={'type':'message','content':'상품명, 단가, 수량, 금액'}),cfg)
    nodes=done['draft']['workflow']['nodes']
    assert [n['tool'] for n in nodes]==['read_document','extract_information','export_result']
    assert nodes[1]['settings']['fields']==['상품명','단가','수량','금액'] and nodes[1]['settings']['extraction_mode']=='rows'
    assert nodes[2]['settings']['format']=='csv' and nodes[0]['settings']['file_ids']==['receipt']
    assert not done.get('issues') and done['__interrupt__'][0].value['kind']=='review'
    assert not store.agents()
    connection.close();store.close()


def test_enforced_condition_check_delegates_the_fixed_flow_without_a_model_call(tmp_path):
    class NoConditionModel:
        def invoke(self, messages): pytest.fail('the fixed flow must not call the condition model')
    store=Storage(tmp_path);s={**initial(),'messages':[{'role':'user','content':REQUEST}],'resources':[DOC]}
    cfg={'configurable':{'thread_id':s['session_id']}}
    graph,connection=build_graph(store,conversation=no_planner,condition_transport=NoConditionModel(),condition_mode='enforce',capability_mode='off')
    first=graph.invoke(s,cfg)
    assert first['draft']['questions']==[QUESTION]
    done=graph.invoke(Command(resume={'type':'message','content':'상품명, 수량, 단가, 금액'}),cfg)
    assert done['draft']['workflow']['nodes'][1]['settings']['fields']==['상품명','수량','단가','금액']
    assert not done.get('issues') and done['__interrupt__'][0].value['kind']=='review'
    checks=[t for t in done['trace'] if t['node']=='condition_check' and t.get('model_calls')==0 and 'outside_scope' in t['detail']]
    assert len(checks)==2 and done['condition_enforced'] is False
    connection.close();store.close()


def test_fixed_flow_scope_excludes_cancellations_tables_and_other_requests():
    from agent_builder.design.document_rows_template import document_rows_applies, ASK_EXPLANATION
    asked=lambda answer,resources=[DOC]:{**initial(),'resources':resources,'messages':[{'role':'user','content':REQUEST},
        {'role':'assistant','content':ASK_EXPLANATION+'\n• '+QUESTION},{'role':'user','content':answer}]}
    assert document_rows_applies(asked('상품명, 금액'))
    assert document_rows_applies(asked('다 뽑아줘')) and document_rows_applies(asked('전체 다'))
    assert not document_rows_applies(asked('취소해줘'))
    assert not document_rows_applies(asked('상품명, 금액',[DOC,{**DOC,'id':'t','kind':'table'}]))
    assert not document_rows_applies({**initial(),'resources':[DOC],'messages':[{'role':'user','content':'이 문서 내용을 요약해줘'}]})


@pytest.mark.parametrize('answer',['취소해줘','취소','그만할래','다른 걸로 바꿀래','아니, 요약으로 바꿔줘','아니, 요약만 해줘','아니요','처음부터 다시 할게요'])
def test_cancel_or_change_after_field_question_returns_to_the_planner(answer):
    from agent_builder.design.document_rows_template import document_rows_reply, is_request_change, ASK_EXPLANATION
    assert is_request_change(answer)
    state={**initial(),'resources':[DOC],'messages':[{'role':'user','content':REQUEST},
                                                    {'role':'assistant','content':ASK_EXPLANATION+'\n• '+QUESTION},
                                                    {'role':'user','content':answer}]}
    assert document_rows_reply(state) is None


@pytest.mark.parametrize('answer',['상품명, 단가, 수량, 금액','거래처, 금액','취소 여부, 금액','주소'])
def test_field_answers_are_not_mistaken_for_commands(answer):
    from agent_builder.design.document_rows_template import is_request_change
    assert not is_request_change(answer)


def asked_state(answer,request=REQUEST,doc=DOC):
    from agent_builder.design.document_rows_template import ASK_EXPLANATION
    return {**initial(),'resources':[doc],'messages':[{'role':'user','content':request},
                                                      {'role':'assistant','content':ASK_EXPLANATION+'\n• '+QUESTION},
                                                      {'role':'user','content':answer}]}


@pytest.mark.parametrize('answer',['전체 다','전체','다','전부 다','모두','모든 항목','알아서','다 뽑아줘','전부 다 정리해주세요','영수증에 있는 거 다','전체 다요.'])
def test_all_fields_answer_on_a_receipt_uses_the_receipt_columns(answer):
    from agent_builder.design.document_rows_template import document_rows_reply, wants_all_fields, RECEIPT_FIELDS
    assert wants_all_fields(answer)
    reply=document_rows_reply(asked_state(answer))
    fields=reply['draft']['workflow']['nodes'][1]['settings']['fields']
    assert fields==RECEIPT_FIELDS and answer not in fields
    assert '영수증 기본 항목' in reply['messages'][-1]['content']


def test_all_fields_answer_without_a_receipt_asks_for_field_names_again():
    from agent_builder.design.document_rows_template import document_rows_reply, ALL_FIELDS_NOTE
    doc={**DOC,'id':'contract','name':'contract.pdf'}
    reply=document_rows_reply(asked_state('전체 다',request='계약서 내용을 엑셀로 정리해줘',doc=doc))
    assert reply['draft']['questions']==[QUESTION] and ALL_FIELDS_NOTE in reply['messages'][-1]['content']
    assert reply['messages'][-1]['content'].endswith(QUESTION)


@pytest.mark.parametrize('answer',['전체 금액','다른 걸로 바꿀래','모델명, 금액','상품명'])
def test_field_names_that_start_like_all_are_not_all_fields(answer):
    from agent_builder.design.document_rows_template import wants_all_fields
    assert not wants_all_fields(answer)


def test_reserved_row_field_is_asked_again_instead_of_overwritten():
    from agent_builder.design.document_rows_template import document_rows_reply, ASK_EXPLANATION
    state={**initial(),'resources':[DOC],'messages':[{'role':'user','content':REQUEST},
                                                    {'role':'assistant','content':ASK_EXPLANATION+'\n• '+QUESTION},
                                                    {'role':'user','content':'상품명, status'}]}
    reply=document_rows_reply(state)
    assert reply['draft']['questions']==[QUESTION] and 'status' in reply['messages'][-1]['content']
    assert reply['messages'][-1]['content'].endswith(QUESTION)


def test_rows_mode_rejects_fields_that_collide_with_record_keys():
    node={'tool':'extract_information','bindings':[{'input':'documents','node_id':'read','output':'documents'}],
          'settings':{'fields':['상품명','status'],'instructions':None,'extraction_mode':'rows'}}
    part={'source_id':'s1','file_id':'receipt','file_name':'receipt.jpg','text':RECEIPT}
    with pytest.raises(ValueError,match='status'):
        extract_information.execute(node,{'outputs':{'read':{'documents':[part]}},'request':REQUEST},None,{},RowsLLM([]))


def test_table_files_and_summary_requests_keep_the_model_planner():
    from agent_builder.design.document_rows_template import document_rows_reply
    base={**initial(),'messages':[{'role':'user','content':REQUEST}]}
    assert document_rows_reply({**base,'resources':[{**DOC,'kind':'table'}]}) is None
    assert document_rows_reply({**base,'resources':[DOC],'messages':[{'role':'user','content':'이 문서 내용을 요약해줘'}]}) is None


class RowsLLM:
    def __init__(self,rows):self.rows=rows
    def with_structured_output(self,schema,**kwargs):
        assert schema is Rows;return self
    def invoke(self,messages):return Rows.model_validate({'rows':self.rows})


def cell(field,value,quote,source='s1'):return {'field':field,'value':value,'source_id':source,'quote':quote}


def test_rows_mode_returns_one_verified_record_per_item_and_drops_invented_values():
    part={'source_id':'s1','file_id':'receipt','file_name':'receipt.jpg','text':RECEIPT}
    llm=RowsLLM([
        {'cells':[cell('상품명','굿모닝우유900ML','001P굿모닝우유900ML'),cell('금액','1.350','*8801104210645 1.350')]},  # quote spans an OCR line break
        {'cells':[cell('상품명','양파','002 P양파'),cell('금액','9,999','9,999')]},  # amount not in the receipt
        {'cells':[cell('상품명','바나나','바나나'),cell('금액',None,None,None)]},  # whole row invented
    ])
    node={'tool':'extract_information','bindings':[{'input':'documents','node_id':'read','output':'documents'}],
          'settings':{'fields':['상품명','금액'],'instructions':None,'extraction_mode':'rows'}}
    records=extract_information.execute(node,{'outputs':{'read':{'documents':[part]}},'request':REQUEST},None,{},llm)['records']
    assert [(r['row'],r['상품명'],r['금액'],r['status']) for r in records]==[(1,'굿모닝우유900ML','1.350','verified'),(2,'양파',None,'partial')]
    assert records[0]['evidence']['금액']['quote']=='*8801104210645 1.350'

    saved=export_result.execute({'tool':'export_result','bindings':[{'input':'records','node_id':'extract','output':'records'}],'settings':{'format':'csv'}},
                                {'outputs':{'extract':{'records':records}}},None,{},None)['artifact']
    table=list(csv.reader(io.StringIO(saved['content'].lstrip('﻿'))))
    assert table[0]==['file_name','row','상품명','금액','status','evidence']
    assert table[1][:5]==['receipt.jpg','1','굿모닝우유900ML','1.350','verified'] and table[2][:5]==['receipt.jpg','2','양파','','partial']


def test_explicit_xlsx_request_delegates_to_registered_spreadsheet_planner():
    from agent_builder.design.document_rows_template import document_rows_applies,document_rows_reply
    state={'resources':[{'id':'receipt','kind':'document','name':'영수증.png'}],
           'messages':[{'role':'user','content':'영수증 상품을 xlsx 파일로 만들어줘'}]}
    assert not document_rows_applies(state)
    assert document_rows_reply(state) is None
