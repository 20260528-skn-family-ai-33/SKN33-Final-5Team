"""The extractor declares controls for any requirement, before workflow generation."""
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agent_builder.design.requirement_contracts import RequirementPlan, RequirementQuestion
from agent_builder.design.requirement_ledger import extraction_schema, make_requirement_check
from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest
from agent_builder.design.requirement_coverage import coverage_report
from tests.design.test_builder import initial


def question(**changes):
    return {'key':'report.layout','text':'결과를 어떤 형식으로 정리할까요?', 'group':'결과 정리',
            'options':['항목별 표','핵심 내용 목록'],'response':{'type':'single_choice'},
            'required':True,'reason':'선택한 구성에 맞춰 결과의 항목 배치를 결정합니다.',**changes}


def wire(questions):
    return {'tasks':[],'unresolved':questions}


class Extractor:
    def __init__(self,value):self.value=value;self.calls=[]
    def invoke(self,messages):
        self.calls.append(deepcopy(messages))
        return {'content':json.dumps(self.value,ensure_ascii=False)}


def test_current_decoder_requires_structured_questions_while_legacy_ledgers_still_load():
    validator=Draft202012Validator(extraction_schema(1,[]))
    validator.validate(wire([question()]))
    assert list(validator.iter_errors(wire(['결과 형식을 선택해주세요.'])))
    assert RequirementPlan.model_validate(wire(['기존 질문'])).unresolved==['기존 질문']
    legacy=RequirementPlan.model_validate(wire([{'key':'existing.question','text':'기존 질문'}]))
    assert legacy.unresolved[0].response.type=='text'


def test_unrelated_requirement_types_keep_their_declared_controls_and_group_titles():
    questions=[question(),
        question(key='review.sections',text='검토할 항목을 선택해주세요.',group='검토 범위',
                 options=['누락','중복','표현'],response={'type':'multi_choice'}),
        question(key='run.limit',text='한 번에 몇 건을 처리할까요?',group='처리 범위',options=[],
                 response={'type':'number','minimum':0,'maximum':500,'step':1,'unit':'건'}),
        question(key='writing.instructions',text='추가 작성 기준을 알려주세요.',group='작성 기준',options=[],
                 response={'type':'text'},required=False)]
    current={**initial(),'execution_mode':'manual','messages':[{'role':'user','content':'자료를 검토하고 결과를 정리해줘.'}]}
    model=Extractor(wire(questions))
    result=make_requirement_check('fixture',model,'enforce')(current)
    assert result['status']=='needs_clarification'
    assert len(result['pending_questions'])==4
    for expected,actual in zip(questions,result['pending_questions']):
        assert {key:actual[key] for key in expected}==expected
        assert len(actual['id'])==24 and actual['source']=='requirements'
    payload=json.loads(model.calls[0][1]['content'])
    assert payload['available_capabilities']['registered_tools']
    assert 'current_draft' not in payload


def test_question_only_plan_is_valid_but_an_empty_plan_does_not_erase_work():
    assert RequirementPlan.model_validate(wire([question()])).tasks==[]
    with pytest.raises(ValidationError):RequirementPlan.model_validate(wire([]))


@pytest.mark.parametrize('changes',[
    {'options':[]},
    {'options':['항목별 표','항목별 표']},
    {'options':['항목별 표',' ']},
    {'response':{'type':'number'}},
    {'response':{'type':'number','minimum':10,'maximum':0},'options':[]},
])
def test_incoherent_controls_are_rejected_for_repair(changes):
    with pytest.raises(ValidationError):RequirementQuestion.model_validate(question(**changes))


def test_duplicate_question_keys_are_not_accepted_as_separate_decisions():
    with pytest.raises(ValidationError):RequirementPlan.model_validate(wire([question(),question(text='다른 문장')]))


def test_optional_preference_and_numeric_zero_are_preserved_in_contract():
    candidate=question(options=[],response={'type':'number','minimum':0,'step':0.5},required=False)
    result=RequirementQuestion.model_validate(candidate).model_dump(exclude_none=True)
    assert result['required'] is False
    assert result['response']['minimum']==0
    assert result['response']['step']==0.5


def test_typed_questions_remain_visible_to_the_save_guard_without_raw_type_errors():
    state={**initial(),'requirement_required':True,'requirement_ledger':wire([question()])}
    state['requirement_fingerprint']=ledger_fingerprint(state)
    state['requirement_digest']=plan_digest(state['requirement_ledger'])
    report=coverage_report(state,{'workflow':None})
    assert any('결과를 어떤 형식' in issue for issue in report['issues'])
    assert not any('expected str instance' in issue for issue in report['issues'])


@pytest.mark.parametrize('key',['gmail_access_token','oauth.refresh_token','service.api_key'])
def test_server_managed_credentials_cannot_become_structured_customer_questions(key):
    with pytest.raises(ValidationError,match='인증 정보'):
        RequirementQuestion.model_validate(question(key=key))


def test_new_decoder_requires_a_nonempty_reason_while_saved_questions_remain_compatible():
    validator=Draft202012Validator(extraction_schema())
    missing=question();missing.pop('reason')
    assert list(validator.iter_errors(wire([missing])))
    assert list(validator.iter_errors(wire([question(reason='')])))
    assert RequirementQuestion.model_validate(missing).reason is None


def test_vague_question_is_not_accepted_even_when_a_reason_is_present():
    with pytest.raises(ValidationError,match='실제로 빠진 조건'):
        RequirementQuestion.model_validate(question(text='업무 내용을 더 구체적으로 설명해주세요.'))


def test_confirmed_time_is_not_repeated_as_a_missing_question():
    from agent_builder.design.requirement_ledger import validate_plan
    current={**initial(),'messages':[{'role':'user','content':'매일 오전 9시에 보고서를 보여줘.'}]}
    repeated=question(key='schedule.hour',text='몇 시에 실행할까요?',options=[],response={'type':'number'})
    with pytest.raises(ValueError,match='이미 확인한 조건'):
        validate_plan(wire([repeated]),current)


def test_valid_choice_answer_is_not_repeated_as_missing():
    from agent_builder.design.requirement_ledger import validate_plan
    candidate=question();content='결과 형식: 항목별 표'
    current={**initial(),'messages':[{'role':'user','content':'보고서를 만들어줘.'},{'role':'user','content':content}],
             'requirement_answers':[{'questions':[candidate],'value':'항목별 표',
                                     'answer':'항목별 표','message_content':content,'turn':1}]}
    with pytest.raises(ValueError,match='이미 확인한 조건'):
        validate_plan(wire([candidate]),current)
