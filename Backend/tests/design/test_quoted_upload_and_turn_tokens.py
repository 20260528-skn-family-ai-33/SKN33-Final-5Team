"""A quoted upload name and punctuation inside values stay intact through routing."""

import pytest

from agent_builder.design.attachment_precheck import QUESTION, missing_attachment_reply
from agent_builder.design.file_timing import now_mentions
from agent_builder.design.turn_intent import split_turn


PENDING_THRESHOLD = [{'id': 'threshold', 'text': '선별할 기준 값은 얼마인가요?',
                      'response': {'type': 'number'}}]


def test_decimal_answer_after_a_question_about_the_pending_question_is_not_split():
    turn = split_turn('기준값이 뭐야? 0.5 이상으로 해줘', {'messages': []}, PENDING_THRESHOLD)
    assert turn['kinds'] == ['about_question', 'work']
    assert turn['work'] == '0.5 이상으로 해줘'


def test_email_address_before_an_unrelated_question_is_not_split():
    turn = split_turn('audit@example.com으로 보내줘. 오늘 날씨 어때?', {'messages': []})
    assert turn['kinds'] == ['work', 'side_question']
    assert turn['work'] == 'audit@example.com으로 보내줘.'


@pytest.mark.parametrize('user_text,subject', [
    ("첨부한 '계약서.pdf'를 분석해줘", '계약서pdf'),
    ("첨부한 최신 '계약서.pdf'를 분석해줘", '계약서pdf'),
    ('업로드한 최근 버전 "budget.xlsx"를 검토해줘', 'budgetxlsx'),
    ('업로드한 "budget.xlsx"를 검토해줘', 'budgetxlsx'),
    ('첨부한 “회의록”을 요약해줘', '파일'),
    ('첨부한 최신 “회의록”을 요약해줘', '파일'),
    ("첨부한 '실행할 때 넣을 파일'을 분석해줘", '파일'),
])
def test_quoted_file_after_upload_cue_triggers_missing_attachment(user_text, subject):
    assert now_mentions(user_text) == [subject]
    reply = missing_attachment_reply({'messages': [{'role': 'user', 'content': user_text}], 'resources': []})
    assert reply['draft']['questions'] == [QUESTION]


@pytest.mark.parametrize('user_text', [
    '문장 "첨부한 PDF를 분석해줘"를 번역해줘',
    '문장 "첨부한 최신 계약서.pdf를 분석해줘"를 번역해줘',
])
def test_quoted_instruction_is_not_treated_as_an_upload(user_text):
    assert now_mentions(user_text) == []
    assert missing_attachment_reply({'messages': [{'role': 'user', 'content': user_text}], 'resources': []}) is None


def test_a_quoted_file_like_string_after_a_text_noun_is_not_an_upload():
    request = '첨부한 문장 "계약서.pdf"를 번역해줘'
    assert now_mentions(request) == []
    assert missing_attachment_reply({'messages': [{'role': 'user', 'content': request}], 'resources': []}) is None


def test_quoted_file_name_matches_an_explicit_runtime_choice_on_followup():
    messages = [
        {'role': 'user', 'content': "첨부한 최신 '계약서.pdf'를 검토해줘"},
        {'role': 'user', 'content': '실행할 때 넣을게요'},
        {'role': 'user', 'content': '첨부한 계약서 PDF의 해지 조건도 검토해줘'},
    ]
    assert missing_attachment_reply({'messages': messages, 'resources': []}) is None


def test_unquoted_file_extension_is_a_single_upload_reference():
    assert now_mentions('첨부한 계약서.pdf를 분석해줘') == ['계약서pdf']
