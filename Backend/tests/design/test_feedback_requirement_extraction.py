"""Regression inputs observed with the deployed model, with strict save guards."""
import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from agent_builder.design.requirement_ledger import extract_plan, make_requirement_check
from agent_builder.design.output_selection_request import output_selection
from tests.design.test_feedback_service_scenarios import ORDER_REQUEST, ORDER_ANSWER, COLUMNS, order_specs


def state():
    return {'messages':[{'role':'user','content':ORDER_REQUEST},
                        {'role':'assistant','content':'계산식과 기준을 알려주세요.'},
                        {'role':'user','content':ORDER_ANSWER}],
            'resources':[{'id':'table-source','name':'주문내역.csv','kind':'table','columns':COLUMNS}]}


def packet():
    return {'tasks':[dict(source_message=1,description=s['kind'],spec=s)
                     for s in deepcopy(order_specs('table-source'))],'unresolved':[]}


def test_literal_threshold_is_bound_to_requested_column():
    wrong=packet()
    wrong['tasks'][2]['spec']['filters'][1]['field']='매출'
    with pytest.raises(ValueError,match='주문금액 100000 이상'):
        extract_plan(wrong,state())
    assert extract_plan(packet(),state())['tasks'][2]['spec']['filters'][1]['field']=='주문금액'


@pytest.mark.parametrize('change',['rename','reorder'])
def test_named_export_preserves_explicit_projection(change):
    wrong=packet()
    if change=='omit':del wrong['tasks'][4]
    elif change=='rename':wrong['tasks'][4]['spec']['fields'][1]='매출'
    else:wrong['tasks'][4]['spec']['fields'].reverse()
    with pytest.raises(ValueError,match='최종 출력 열'):
        extract_plan(wrong,state())


def test_extractor_cannot_delete_a_rejected_numeric_filter_instead_of_fixing_it():
    wrong=packet();wrong['tasks'][2]['spec']['filters'].pop()
    with pytest.raises(ValueError,match='숫자 선별 조건'):
        extract_plan(wrong,state())


@pytest.mark.parametrize('text,expected',[
    (ORDER_ANSWER,['주문번호','주문금액']),
    ('완료 행만 남기고, 고객·총액을 결과.csv로 저장해줘.',['고객','총액']),
    ('고객·총액을 내보내줘.',None),
    ('A·B를 one.csv로 저장하고 C·D를 two.csv로 저장해줘.',None),
    ('전체 열로 저장하도록 바꿔줘.',None),
])
def test_output_list_does_not_consume_sort_clause_or_restore_old_request(text,expected):
    current=state();current['messages'].append(dict(role='user',content=text))
    assert output_selection(current)==expected


def test_invalid_model_packet_is_retried_without_weakening_guards():
    invalid=packet();invalid['tasks'][2]['spec']['filters'][0].update(operator='is_empty',value=None)
    class Transport:
        def __init__(self):self.calls=[]
        def invoke(self,messages):
            self.calls.append(deepcopy(messages))
            return {'content':json.dumps(invalid if len(self.calls)==1 else packet(),ensure_ascii=False)}
    transport=Transport()
    result=make_requirement_check('fixture',transport,'enforce')(state())
    assert result['requirement_route']=='plan'
    assert len(transport.calls)==2
    context=json.loads(transport.calls[0][-1]['content'])
    assert context['explicit_output_columns']==['주문번호','주문금액']
    Draft202012Validator(context['response_contract']).validate(packet())
    assert json.loads(transport.calls[1][-2]['content'])==invalid
    assert '무효' in transport.calls[1][-1]['content']
    assert result['requirement_ledger']['tasks'][2]['spec']['filters'][0]['operator']=='eq'


def test_literal_output_selection_is_owned_without_losing_other_work():
    raw=packet();del raw['tasks'][4]
    actual=extract_plan(raw,state())
    assert [t['spec'] for t in actual['tasks']]==order_specs('table-source')


def test_non_string_response_never_becomes_invalid_assistant_message():
    class Transport:
        calls=0
        def invoke(self,messages):
            self.calls+=1
            assert all(isinstance(m['content'],str) for m in messages)
            return {'content':None if self.calls==1 else json.dumps(packet())}
    assert make_requirement_check('fixture',Transport(),'enforce')(state())['requirement_route']=='plan'


def test_review_decoder_keeps_analysis_and_human_answer_on_distinct_ports():
    from tests.design.test_beta_feedback import sealed_specs,native_schema,DOC
    from tests.design.test_feedback_service_scenarios import CONTRACT_REQUEST
    from agent_builder.design.requirement_generation import constrain_review_steps
    from agent_builder.design.operation_steps import schema_for
    current=sealed_specs(CONTRACT_REQUEST,[dict(kind='document_analysis',request=CONTRACT_REQUEST),dict(kind='human_review')],[DOC])
    original=schema_for(native_schema())
    schema=constrain_review_steps(original,current)
    slots=schema['$defs']['WorkflowDefinition']['properties']['next_steps']['properties']
    validator=Draft202012Validator(slots['step_4']['anyOf'][0]['properties']['bindings'])
    correct=[dict(input='documents',step=1,output='documents'),dict(input='text',step=2,output='text'),dict(input='answer',step=3,output='answer')]
    validator.validate(correct)
    assert not validator.is_valid(correct[:1]+[dict(input='answer',step=3,output='text')])
    wrong=deepcopy(correct);wrong[-1]['output']='text'
    assert not validator.is_valid(wrong)
    assert not validator.is_valid([correct[0],correct[0],correct[2]])
    stale={**current,'requirement_digest':'stale'}
    assert constrain_review_steps(original,stale)==original


def test_projection_decoder_uses_real_rows_adapters_and_preserves_literal_conditions(monkeypatch):
    from tests.design.test_beta_feedback import native_schema,sealed_specs
    from agent_builder.design.operation_steps import schema_for
    from agent_builder.design.requirement_generation import required_operation_variants
    from agent_builder.design.projected_table_decoder import constrain_projected_table
    from contracts.models import AgentDraft
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    current=sealed_specs(ORDER_ANSWER,deepcopy(order_specs('table-source')),state()['resources'])
    base=native_schema();original=AgentDraft.model_json_schema()
    base['$defs']['WorkflowNode']['anyOf']+=required_operation_variants(order_specs('table-source'),original['$defs']['WorkflowNode'],original['$defs']['Binding'],set(COLUMNS))
    wire=schema_for(base)
    constrained=constrain_projected_table(wire,current)
    slots=constrained['$defs']['WorkflowDefinition']['properties']['next_steps']['properties']
    assert len(slots)==6  # compute, filter, sort, table rows, projection, CSV
    filtered=slots['step_3']['anyOf'][0]['properties']['settings']['properties']['execution_plan']
    checker=Draft202012Validator(filtered)
    good=deepcopy(order_specs('table-source')[2]['filters'])
    value=dict(filters=good,sort=[],match='all',missing_policy='keep',duplicate_policy='keep',duplicate_field=None,output_format='json')
    checker.validate(value)
    bad=deepcopy(value);bad['filters']=[]
    assert not checker.is_valid(bad)
    selection=slots['step_6']['anyOf'][0]['properties']['settings']['properties']
    assert selection['operation']['enum']==['select_columns']
    args=Draft202012Validator(selection['arguments_json'])
    args.validate({'columns':['주문번호','주문금액']})
    assert not args.is_valid({'columns':['주문번호','매출']})
    assert constrain_projected_table(wire,{**current,'requirement_digest':'stale'})==wire


def test_explicit_review_sequence_cannot_be_lost_by_read_only_model_extraction():
    from tests.design.test_feedback_service_scenarios import CONTRACT_REQUEST
    from tests.design.test_beta_feedback import DOC
    current={'messages':[dict(role='user',content=CONTRACT_REQUEST)],'resources':[DOC]}
    raw=dict(tasks=[dict(source_message=0,description='문서 읽기',spec=dict(kind='read',file_ids=[DOC['id']]))],unresolved=[])
    actual=extract_plan(raw,current)
    assert [t['spec']['kind'] for t in actual['tasks']]==['read','document_analysis','human_review']
    assert actual['tasks'][1]['spec']['request']==CONTRACT_REQUEST
    assert actual['tasks'][2]['depends_on']==[actual['tasks'][1]['id']]
    # Additional requirements must remain visible, never silently disappear.
    raw['tasks'].append(dict(source_message=0,description='추가 업무',spec=dict(kind='other',action='사용자가 추가한 별도 업무')))
    assert any(t['spec']['kind']=='other' for t in extract_plan(raw,current)['tasks'])
    raw['tasks'].append(dict(source_message=0,description='잘못된 합계',spec=dict(kind='aggregate',fields=['text'],aggregation='sum',sum_field=None)))
    with pytest.raises(ValueError,match='합계'):
        extract_plan(raw,current)


def test_explicit_review_projection_does_not_infer_order_for_revisions_or_tables():
    from agent_builder.design.explicit_review_contract import explicit_review_contract
    from tests.design.test_feedback_service_scenarios import CONTRACT_REQUEST
    from tests.design.test_beta_feedback import DOC
    current={'messages':[dict(role='user',content=CONTRACT_REQUEST)],'resources':[DOC]}
    assert explicit_review_contract(current)
    assert explicit_review_contract({**current,'resources':state()['resources']}) is None
    assert explicit_review_contract({**current,'messages':current['messages']+[dict(role='user',content='사람 검토는 생략해줘.')]}) is None


def test_explicit_review_decoder_excludes_only_unrequested_aggregation_and_schedule():
    from agent_builder.design.explicit_review_contract import unrequested_review_kinds
    from tests.design.test_feedback_service_scenarios import CONTRACT_REQUEST
    from tests.design.test_beta_feedback import DOC
    current={'messages':[dict(role='user',content=CONTRACT_REQUEST)],'resources':[DOC]}
    assert unrequested_review_kinds(current)=={'aggregate','schedule'}
    current['messages'][0]['content']+=' 추가로 건수를 집계하고 매일 예약 실행해줘.'
    assert not unrequested_review_kinds(current)


def test_unique_literal_calculation_uses_the_requested_new_output_column():
    from agent_builder.design.output_selection_request import literal_output_compute,constrain_literal_selection
    from agent_builder.design.requirement_ledger import extraction_schema
    current=state()
    assert literal_output_compute(current)==dict(kind='compute',formula='수량*단가',output_column='주문금액')
    schema=constrain_literal_selection(extraction_schema(2,['table-source']),current)
    validator=Draft202012Validator(schema['$defs']['Compute'])
    validator.validate(dict(kind='compute',formula='수량*단가',output_column='주문금액'))
    assert not validator.is_valid(dict(kind='compute',formula='수량*단가',output_column='매출'))
    assert not validator.is_valid(dict(kind='compute',formula='수량+단가',output_column='주문금액'))
    invalid=packet();invalid['tasks'][1]['spec']['output_column']='매출'
    with pytest.raises(ValueError,match='계산식의 결과'):
        extract_plan(invalid,current)


@pytest.mark.parametrize('replacement',[
    ORDER_ANSWER.replace('매출은 수량×단가','매출=수량×단가'),
    ORDER_ANSWER+' 다른 계산으로 수량+단가도 처리해줘.',
    ORDER_ANSWER.replace('주문번호·주문금액을','주문번호·주문금액·세금을'),
])
def test_calculated_output_correspondence_is_not_guessed_when_ambiguous(replacement):
    from agent_builder.design.output_selection_request import literal_output_compute
    current=state();current['messages'][-1]['content']=replacement
    assert literal_output_compute(current) is None
