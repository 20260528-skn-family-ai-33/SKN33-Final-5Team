import json

import pytest

from agent_builder.capabilities.capability_judgement import assess_capability, validate_assessment_result
from agent_builder.capabilities.capability_check import make_capability_check
from agent_builder.design.question_language import normalize_question_language
from tests.conditions.test_condition_readiness import state


class Transport:
    def __init__(self,values):self.values=iter(values);self.calls=[]
    def invoke(self,messages):
        self.calls.append(messages)
        value=next(self.values)
        if isinstance(value,Exception):raise value
        return {'content':json.dumps(value,ensure_ascii=False)}


def packet(current,condition='금액 기준을 알려주세요.',reason='작업에 필요한 금액 기준이 미정입니다.'):
    return {'decision':'clarify','tool_ids':['read_table'],'missing_conditions':[condition],
            'missing_actions':[],'reason':reason,'user_quotes':[current['messages'][0]['content']]}


def test_a_non_time_chinese_question_is_rewritten_once():
    current=state();good=packet(current);transport=Transport([packet(current,'金额 기준을 알려주세요.'),good])
    result=assess_capability(current,transport)
    assert result['missing_conditions']==good['missing_conditions']
    assert len(transport.calls)==2 and result['model_calls']==2
    assert '한국어' in transport.calls[1][-1]['content']


def test_two_invalid_language_results_keep_the_original_missing_condition():
    current=state();bad=packet(current,'金额 기준을 알려주세요.');transport=Transport([bad,bad])
    result=assess_capability(current,transport)
    assert len(transport.calls)==2
    assert result['decision']=='clarify'
    assert result['missing_conditions']==bad['missing_conditions']
    assert result['question_language_rewrite_required'] is True


def test_normal_korean_assessment_keeps_its_original_call_count():
    current=state();transport=Transport([packet(current)])
    assert assess_capability(current,transport)['model_calls']==1
    assert len(transport.calls)==1


def test_user_supplied_han_column_name_is_preserved():
    current=state();current['messages'][0]['content']='年月 열로 자료를 정리해줘.'
    result=validate_assessment_result(packet(current,'年月 열의 처리 기준은 무엇인가요?'),current)
    assert result['missing_conditions']==['年月 열의 처리 기준은 무엇인가요?']


def test_model_written_missing_action_also_uses_korean_validation():
    current=state();value=packet(current)
    value.update(decision='missing_capability',missing_conditions=[],missing_actions=['邮件 발송'])
    result=validate_assessment_result(value,current)
    assert result['missing_actions']==value['missing_actions']
    assert result['question_language_rewrite_required'] is True


def test_a_supported_assessment_does_not_fail_or_retry_because_of_its_reason_language():
    current=state();value=packet(current,reason='支持 업무를 구성할 수 있습니다.')
    value.update(decision='existing_tools',missing_conditions=[])
    transport=Transport([value]);result=assess_capability(current,transport)
    assert result['decision']=='existing_tools' and result['tool_ids']==value['tool_ids']
    assert result['reason']=='등록된 도구로 요청한 업무를 구성할 수 있습니다.'
    assert len(transport.calls)==1 and result['model_calls']==1


def test_a_wording_repair_cannot_change_the_capability_decision():
    current=state();bad=packet(current,'金额 기준을 알려주세요.')
    changed=packet(current);changed.update(decision='existing_tools',missing_conditions=[])
    result=assess_capability(current,Transport([bad,changed]))
    assert result['decision']=='clarify' and result['missing_conditions']==bad['missing_conditions']


def test_a_wording_repair_network_failure_preserves_the_missing_condition():
    current=state();bad=packet(current,'金额 기준을 알려주세요.')
    result=assess_capability(current,Transport([bad,OSError('offline')]))
    assert result['decision']=='clarify' and result['missing_conditions']==bad['missing_conditions']


def test_a_bad_question_is_not_reported_as_a_capability_system_error():
    current=state();bad=packet(current,'金额 기준을 알려주세요.')
    update=make_capability_check('fixture',transport=Transport([bad,bad]),mode='enforce')(current)
    assert update['capability_route']=='await_capability' and update['status']=='needs_clarification'
    assert len(update['pending_questions'])==1
    public=normalize_question_language({**current,**update})
    assert public['requirement_failure']=={'kind':'question_language_error','retryable':True}
