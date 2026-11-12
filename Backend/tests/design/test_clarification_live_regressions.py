"""Regressions found on the 2026-11-11 RunPod run of the team's 52-case corpus (fine-tuned 9B).

C02: a requirement file question must still be asked (dropping it left no task and no question).
C06: a question that already asks the user must not get a second "…를 알려주세요".
U02/U04/X01 vs C03: a repeated missing_capability verdict is kept; a changed one is used.
"""
import pytest

import agent_builder.capabilities.capability_check as cc
from agent_builder.conditions.clarification_core import register, statement_question


@pytest.mark.parametrize('text', ['중요한 메일을 선별할 조건을 알려주세요.', '처리할 파일을 자료 연결에서 업로드해 주세요.',
                                  '수신자 이메일 주소는 무엇인가요?', '기준 값을 포함할까요', '정렬 기준을 정해 주십시오.'])
def test_a_sentence_that_already_asks_is_kept(text):
    assert statement_question(text) == text


@pytest.mark.parametrize('text,question', [('위험 판단 기준이 미정입니다', '위험 판단 기준을 알려주세요.'),
                                           ('결과 파일 이름', '결과 파일 이름. 이 부분을 알려주세요.')])
def test_a_statement_still_becomes_a_question(text, question):
    assert statement_question(text) == question


def test_requirement_stage_keeps_a_file_question_and_capability_screening_drops_it():
    state = {'messages': [{'role': 'user', 'content': '고객 데이터를 분석해서 보고서를 만들어줘.'}]}
    question = '분석할 자료 ID를 알려주세요.'
    kept, _ = register(state, [question], source='requirement', file_questions_allowed=True)
    assert [q['text'] for q in kept] == [question]
    dropped, reasons = register(state, [question], source='requirement')
    assert dropped == [] and reasons


VERDICT = dict(decision='missing_capability', tool_ids=[], missing_conditions=[],
               missing_actions=['자료 처리와 전송을 확인해 주세요'], reason='처리 단계와 조건을 확인해 주세요', user_quotes=[])
STATE = {'messages': [{'role': 'user', 'content': '결과를 Microsoft Teams 채널로 보내줘.'}]}


def test_a_repeated_missing_capability_verdict_is_kept_not_turned_into_review(monkeypatch):
    calls = []
    monkeypatch.setattr(cc, 'assess_capability', lambda state, transport: calls.append(1) or dict(VERDICT, model_calls=1))
    result = cc._without_ungrounded_actions(STATE, object(), dict(VERDICT, model_calls=1))
    assert calls == [1]
    assert result['decision'] == 'missing_capability'
    assert result['invalid_missing_actions'] == ['자료 처리와 전송을 확인해 주세요'] and result['model_calls'] == 2


def test_a_changed_verdict_after_the_feedback_is_used(monkeypatch):
    changed = dict(decision='existing_tools', tool_ids=['read_table', 'email.send'], missing_conditions=[],
                   missing_actions=[], reason='등록 도구로 가능', user_quotes=[], model_calls=1)
    monkeypatch.setattr(cc, 'assess_capability', lambda state, transport: changed)
    result = cc._without_ungrounded_actions(STATE, object(), dict(VERDICT, model_calls=1))
    assert result['decision'] == 'existing_tools' and result['model_calls'] == 2


def test_without_a_transport_the_verdict_is_kept():
    assert cc._without_ungrounded_actions(STATE, None, dict(VERDICT))['decision'] == 'missing_capability'


# M08: generic check requests are not questions; real questions stay.
M08 = {'messages': [{'role': 'user', 'content': '연결한 표의 qty에 2를 곱한 새 열을 만들어 JSON으로 저장해줘. 새 열 이름은 아직 미정이야.'}]}
GENERIC = dict(decision='clarify', tool_ids=['read_table', 'calculate_columns', 'write_result'], missing_actions=[],
               missing_conditions=['처리 단계와 조건을 확인해 주세요.', '처리 단계와 조건을 확인해 주세요.'], reason='', user_quotes=[])


@pytest.mark.parametrize('question,generic', [('처리 단계와 조건을 확인해 주세요.', True), ('자료 처리와 전송을 확인해 주세요', True),
                                              ('새 열 이름을 알려주세요.', False), ('몇 시에 실행할까요?', False),
                                              ('qty 값을 확인해 주세요.', False)])
def test_generic_check_requests_are_told_apart_from_questions(question, generic):
    assert cc._generic_question(question, M08) is generic


def test_generic_only_questions_are_reassessed_once_and_merged(monkeypatch):
    real = dict(GENERIC, missing_conditions=['새 열 이름을 무엇으로 할까요?'], model_calls=1)
    monkeypatch.setattr(cc, 'assess_capability', lambda state, transport: real)
    result = cc._without_generic_questions(M08, object(), dict(GENERIC, model_calls=1))
    assert result['missing_conditions'] == ['새 열 이름을 무엇으로 할까요?'] and result['model_calls'] == 2
    monkeypatch.setattr(cc, 'assess_capability', lambda state, transport: dict(GENERIC, model_calls=1))
    kept = cc._without_generic_questions(M08, object(), dict(GENERIC, model_calls=1))
    assert kept['decision'] == 'clarify' and kept['missing_conditions'] == ['처리 단계와 조건을 확인해 주세요.']


# R03: a sort key is not a filter without a written comparator or value.
from agent_builder.conditions.condition_readiness import _sort_key_only


@pytest.mark.parametrize('text,expected', [
    ('연결한 표를 amount 숫자 내림차순으로 정렬하고 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.', True),
    ('amount 오름차순으로 정렬해줘', True),
    ('amount가 큰 행만 JSON으로 저장해줘.', False),
    ('total이 20 이상인 행만 total 숫자 내림차순으로 정렬해서 JSON으로 저장해줘.', False),
    ('amount 내림차순 상위 행만 골라줘', False),
])
def test_sort_key_only(text, expected):
    slots = {'filter.field': {'status': 'specified', 'value': 'total' if 'total' in text else 'amount'},
             'filter.operator': {'status': 'missing'}, 'filter.value': {'status': 'missing'}}
    assert _sort_key_only(slots, {}, text) is expected


@pytest.mark.parametrize('question', ['확인 항목 0을 알려주세요.', '확인할 기준을 알려주세요.', '확인 담당자 이메일은 무엇인가요?'])
def test_questions_that_mention_checking_but_ask_for_information_stay(question):
    assert cc._generic_question(question, M08) is False
