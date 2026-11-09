from copy import deepcopy
import pytest
from agent_builder.design.explicit_processing import explicit_processing_issues, literal_products
from tests.workflow_engine.test_workflow import node, bind, edge


def table_case():
    state={'messages':[{'role':'user','content':'수량×단가를 계산한 뒤 고객별로 합산해 JSON으로 저장해줘.'}],
           'resources':[{'kind':'table','columns':['수량','단가','고객']}]}
    nodes=[node('read','read_table'),node('calc','calculate_columns',[bind('table','read','table')],
        table_operation='compute',calculation_formula='단가 * 수량',output_column='금액'),
        node('sum','aggregate_table',[bind('table','calc','table')],fields=['고객'],aggregation='sum',sum_field='금액'),
        node('out','write_result',[bind('table','sum','table')],format='json')]
    return state,{'workflow':{'entry':'read','nodes':nodes,'edges':[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]},'instructions':state['messages'][0]['content']}


def test_product_must_be_executed_on_result_path_not_merely_described():
    s,d=table_case();before=deepcopy(d)
    assert explicit_processing_issues(s,d)==[]
    assert d==before
    d['workflow']['nodes'][2]['bindings']=[bind('table','read','table')]
    assert explicit_processing_issues(s,d)
    d['workflow']['nodes'][1]['settings']['calculation_formula']='단가 + 수량'
    assert explicit_processing_issues(s,d)


@pytest.mark.parametrize('message',['계산하지 말고 원문만 보여줘.','수량×단가가 무슨 뜻인가요?'])
def test_cancelled_or_non_request_products_do_not_force_calculation(message):
    s,d=table_case()
    if message.startswith('계산'):s['messages'].append({'role':'user','content':message})
    else:s['messages']=[{'role':'user','content':message}]
    assert literal_products(s)==[]


def test_product_is_not_inferred_from_assistant_or_file_text():
    s,d=table_case();s['messages'][0]['role']='assistant'
    s['resources'][0]['text']='수량×단가를 계산해줘'
    assert literal_products(s)==[]


def document_case():
    request='계약서의 모순을 분석하고 수정 제안을 작성한 뒤 사람이 확인하게 해줘.'
    nodes=[node('read','read_document'),node('extract','extract_information',[bind('documents','read','documents')],fields=['기간']),
           node('analysis','compose_answer',[bind('documents','read','documents')],instructions=request),
           node('human','ask_user',[bind('text','analysis','text')],question='검토 결과를 확인했나요?')]
    return {'messages':[{'role':'user','content':request}],'resources':[]}, {'instructions':request,'workflow':{'entry':'read','nodes':nodes,'edges':[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]}}


def test_extraction_is_not_analysis_and_human_must_receive_analysis_output():
    s,d=document_case();assert explicit_processing_issues(s,d)==[]
    d['workflow']['nodes'][-1]['bindings']=[bind('records','extract','records')]
    assert '사람 확인' in explicit_processing_issues(s,d)[0]
    d['workflow']['nodes']=[n for n in d['workflow']['nodes'] if n['id']!='analysis']
    assert '분석' in explicit_processing_issues(s,d)[0]


def test_clarification_and_cancelled_review_do_not_force_a_graph():
    s,d=document_case();d['questions']=['어떤 조건을 검토할까요?']
    assert explicit_processing_issues(s,d)==[]
    d.pop('questions');s['messages'].append({'role':'user','content':'분석하지 말고 기간만 추출해줘.'})
    d['workflow']['nodes']=[d['workflow']['nodes'][0]]
    assert explicit_processing_issues(s,d)==[]
