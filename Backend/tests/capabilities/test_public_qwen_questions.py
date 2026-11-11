"""Synthetic presentation regressions from development live evidence; no model calls."""
import pytest

from agent_builder.capabilities.capability_check import _question_text


@pytest.mark.parametrize('question', [
    '분석에 사용할 문서의 ID를 알려주세요.',
    '사용할 file_id를 입력해 주세요.',
    '처리할 자료 식별자가 무엇인가요?',
    '연결된 표의 구체적인 ID를 알려주세요.',
])
def test_resource_identifier_sentence_points_to_existing_attachment_ui(question):
    shown = _question_text(question)
    assert '자료 연결' in shown
    assert 'ID' not in shown and 'file_id' not in shown and '식별자' not in shown


@pytest.mark.parametrize('question', [
    'Gmail 인증 정보나 API 키가 필요합니다.',
    'Gmail access token을 알려주세요.',
])
def test_gmail_connection_prompt_does_not_ask_for_credentials_in_chat(question):
    shown = _question_text(question)
    assert 'Gmail 연결' in shown
    assert 'API' not in shown and 'token' not in shown and '인증 정보' not in shown


@pytest.mark.parametrize('question', [
    '고객 ID 열을 기준으로 집계할까요?',
    '파일 ID 열을 기준으로 중복을 제거할까요?',
    'API 키 열은 결과에서 제외할까요?',
    '어느 고객에게 결과를 보낼까요?',
])
def test_business_fields_and_required_recipient_questions_are_preserved(question):
    assert _question_text(question) == question


def test_attachment_guidance_preserves_a_second_required_question():
    shown = _question_text('자료 ID와 결과를 받을 수신자를 알려주세요.')
    assert '자료 연결' in shown and 'ID' not in shown
    assert '수신자' in shown
