"""Fresh development regressions for row calculations and real missing inputs."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.requirement_ledger import extract_plan, make_requirement_check


def state(*messages):
    return {'messages':[{'role':'user','content':text} for text in messages], 'trace':[],
            'resources':[{'id':'fixture-table','kind':'table','columns':['units','rate','region','매출','원가']}]}


def task(kind, **fields):
    return {'source_message':0,'description':kind,'spec':{'kind':kind,**fields}}


def packet(*extra):
    return {'tasks':[task('read',file_ids=['fixture-table']),*extra,task('export',format='csv')], 'unresolved':[]}


class Transport:
    def __init__(self, raw):self.raw=raw;self.calls=0
    def invoke(self, _messages):
        self.calls+=1
        if isinstance(self.raw,Exception):raise self.raw
        return {'content':self.raw if isinstance(self.raw,str) else json.dumps(self.raw,ensure_ascii=False)}


def missing(plan):
    return {q['key'] for q in plan['unresolved'] if isinstance(q,dict)}


def test_missing_calculation_inputs_become_two_typed_questions_without_losing_read_export():
    current=state('배송 표에 계산 열을 추가하고 결과를 CSV로 저장해줘.')
    raw=packet();update=make_requirement_check('fixture',Transport(raw),mode='enforce')(current)
    assert update['status']=='needs_clarification'
    assert {q['key'] for q in update['pending_questions']}=={'compute.formula','compute.output_column'}
    assert [t['spec']['kind'] for t in update['requirement_ledger']['tasks']]==['read','export']


def test_plausible_column_names_in_the_attachment_do_not_authorize_a_guessed_calculation():
    current=state('표에 새 계산 열을 추가해줘. 계산식과 결과 열 이름은 아직 정하지 않았어. CSV로 저장해.')
    raw=packet(task('compute',formula='units * rate',output_column='derived_total'))
    plan=extract_plan(deepcopy(raw),current)
    assert missing(plan)=={'compute.formula','compute.output_column'}
    assert all(t['spec']['kind']!='compute' for t in plan['tasks'])


@pytest.mark.parametrize(('prompt','formula','column','unknown'),[
    ('units * 3으로 새 계산 열을 만들어줘. 열 이름은 미정이야. CSV로 저장해.', 'units * 3','result','compute.output_column'),
    ('units에 곱할 배율은 아직 미정이야. weighted라는 새 열을 만들고 CSV로 저장해.', 'units * rate','weighted','compute.formula'),
])
def test_only_the_missing_input_is_asked(prompt,formula,column,unknown):
    plan=extract_plan(packet(task('compute',formula=formula,output_column=column)),state(prompt))
    assert missing(plan)=={unknown}


def test_compute_question_preserves_an_independent_operation_and_an_unrelated_open_question():
    current=state('계산 열 하나를 추가하고 region 오름차순으로 정렬한 뒤 CSV로 저장해줘.')
    sorting=task('sort',sort=[{'field':'region','direction':'ascending','value_type':'text'}])
    raw=packet(sorting);raw['unresolved']=['별도 전달 대상은 어디인가요?']
    plan=extract_plan(raw,current)
    assert missing(plan)=={'compute.formula','compute.output_column'}
    assert [t['spec']['kind'] for t in plan['tasks']]==['read','sort','export']
    assert any(q=='별도 전달 대상은 어디인가요?' or isinstance(q,dict) and '전달 대상' in q['text'] for q in plan['unresolved'])


@pytest.mark.parametrize(('prompt','formula','column'),[
    ('units에 3을 곱한 값을 weighted라는 열에 넣고 CSV로 저장해줘.', 'units * 3','weighted'),
    ('매출에서 원가를 뺀 값을 마진 열로 계산해서 CSV에 저장해줘.', '매출 - 원가','마진'),
    ('새 계산 열 net = (units * rate) - 3을 추가하고 CSV로 저장해줘.', '(units * rate) - 3','net'),
])
def test_explicit_symbolic_and_sentence_formulas_are_preserved(prompt,formula,column):
    plan=extract_plan(packet(task('compute',formula=formula,output_column=column)),state(prompt))
    assert plan['unresolved']==[]
    assert [t['spec'] for t in plan['tasks'] if t['spec']['kind']=='compute']==[{'kind':'compute','formula':formula,'output_column':column}]


def test_factor_reply_supplies_only_factor_and_preserves_the_original_column():
    current=state('units에 곱할 배율은 미정이야. weighted라는 열을 만들고 CSV로 저장해.', 'units에 2를 곱해줘.')
    plan=extract_plan(packet(task('compute',formula='units * 2',output_column='weighted')),current)
    assert plan['unresolved']==[]


def test_quantity_in_an_unrelated_followup_is_not_an_answer_to_a_missing_multiplier():
    current=state('units에 곱할 배율은 미정이야. weighted라는 열을 만들고 CSV로 저장해.', '표에서 10개만 보여줘.')
    plan=extract_plan(packet(task('compute',formula='units * 10',output_column='weighted')),current)
    assert missing(plan)=={'compute.formula'}


def test_short_name_reply_uses_the_actual_question_event_and_preserves_the_formula():
    current=state('units * 2로 새 열을 계산해줘. 열 이름은 미정이야. CSV로 저장해.', 'normalized')
    current['pending_questions']=[{'key':'compute.output_column','id':'fixture-question','text':'새 열 이름은 무엇인가요?'}]
    current['condition_answers']=[{'turn':1,'answer':'normalized','candidate_keys':['compute.output_column'],'question_ids':['fixture-question']}]
    plan=extract_plan(packet(task('compute',formula='units * 2',output_column='normalized')),current)
    assert plan['unresolved']==[]


@pytest.mark.parametrize(('prompt','spec'),[
    ('region별 units 합계를 계산해 CSV로 저장해줘.',task('aggregate',fields=['region'],aggregation='sum',sum_field='units')),
    ('units 열 이름을 quantity로 바꾸고 CSV로 저장해줘.',task('rename',source='units',target='quantity')),
    ('표에 계산 열 추가는 하지 말고 원본을 CSV로 저장해줘.',None),
    ('원래 계산 열은 취소하고 표를 CSV로 저장해줘.',None),
])
def test_aggregate_rename_and_cancelled_calculations_do_not_require_new_formula_inputs(prompt,spec):
    plan=extract_plan(packet(*([spec] if spec else [])),state(prompt))
    assert plan['unresolved']==[]


@pytest.mark.parametrize('bad',[OSError('synthetic transport failure'),'{broken',{'tasks':'invalid','unresolved':[]}])
def test_transport_and_packet_errors_never_become_user_missing_inputs(bad):
    update=make_requirement_check('fixture',Transport(bad),mode='enforce')(state('표에 새 계산 열을 추가해줘.'))
    assert update['status']=='validation_failed' and not update.get('pending_questions')
