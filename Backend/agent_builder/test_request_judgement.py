import json
from copy import deepcopy

import pytest

from agent_builder.experiments.request_judgement import (
    RequestJudgement,example_packets,judge_request,judgement_schema,
    prepare_judgement,quote_catalog,validate_judgement,
)


def state():return {'messages':[{'role':'user','content':'점수가 높은 행을 골라줘. 중복은 유지해.'}], 'resources':[]}


def raw():return {'unsupported_actions':[], 'condition_checks':[{'condition':'점수 기준','status':'missing',
    'evidence_id':'target_e1','question':'몇 점 이상인가요?'}], 'decision':'clarify','questions':['몇 점 이상인가요?'],'reason':'점수 기준이 미정입니다.'}


def test_quote_resolution_is_exact_not_model_rewritten():
    catalog=quote_catalog(state());result=validate_judgement(raw(),catalog)
    assert result['condition_checks'][0]['evidence']=={'source_id':'user','quote':state()['messages'][0]['content']}


def test_foreign_example_id_is_rejected():
    r=raw();r['condition_checks'][0]['evidence_id']='demo1_e1'
    with pytest.raises(ValueError,match='없는 근거'):validate_judgement(r,quote_catalog(state()))


@pytest.mark.parametrize('mutation',['ready_missing','no_checks','no_questions','specified_question','missing_question','unsupported_no_action'])
def test_contradictory_decisions_are_not_silently_accepted(mutation):
    r=raw()
    if mutation=='ready_missing':r['decision']='ready';r['questions']=[]
    if mutation=='no_checks':r.update(decision='ready',condition_checks=[],questions=[])
    if mutation=='no_questions':r['questions']=[]
    if mutation=='specified_question':r['condition_checks'][0]['status']='specified';r.update(decision='ready',questions=[])
    if mutation=='missing_question':r['condition_checks'][0]['question']=None
    if mutation=='unsupported_no_action':r.update(decision='unsupported',questions=[])
    with pytest.raises(ValueError):RequestJudgement.model_validate(r)


def test_demos_have_exact_sources_and_decoder_key_order():
    examples=example_packets()
    for demo in examples:
        catalog=demo['input']['quote_catalog'];out=demo['output']
        assert list(out)==list(judgement_schema(catalog)['properties'])
        validate_judgement(out,catalog)
    ready=examples[1]
    cited=[e['evidence']['quote'] for e in validate_judgement(ready['output'],ready['input']['quote_catalog'])['condition_checks']]
    assert any('45분 이상' in q for q in cited)


def test_actual_history_is_separate_from_examples():
    messages,schema,catalog=prepare_judgement(state())
    target=json.loads(messages[-1]['content'])['target_case']
    assert len(messages)==2
    assert target['messages']==state()['messages']
    assert 'Discord' not in json.dumps(target,ensure_ascii=False)
    ids=schema['$defs']['ConditionCheck']['properties']['evidence_id']['enum']
    assert all(i.startswith('target_') for i in ids)
    assert 'demo1_e1' not in ids


def test_single_call_returns_judgement_without_workflow_or_execution():
    class Fake:
        def __init__(self):self.calls=[]
        def invoke(self,messages):self.calls.append(deepcopy(messages));return {'content':json.dumps(raw())}
    fake=Fake();result=judge_request(state(),'fake',transport=fake)
    assert result['judgement']['decision']=='clarify'
    assert len(fake.calls)==1
    assert 'workflow' not in result and 'confirmed' not in result


def test_final_limits_still_apply_after_decoder_relaxation():
    r=raw();r['reason']='가'*1201
    with pytest.raises(ValueError):validate_judgement(r,quote_catalog(state()))


def test_no_evidence_cannot_become_ready():
    with pytest.raises(ValueError):prepare_judgement({'messages':[],'resources':[]})
