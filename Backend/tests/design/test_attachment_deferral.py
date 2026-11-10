"""An explicit runtime-upload choice survives edits to that same file's task."""
import pytest

from agent_builder.design.attachment_precheck import missing_attachment_reply, QUESTION


ORIGINAL = '첨부한 계약서 PDF를 검토해줘'
FOLLOWUP = '첨부한 계약서 PDF의 해지 조건도 검토해줘'


def dialogue(*texts):
    return {'messages': [{'role': 'user', 'content': text} for text in texts], 'resources': []}


@pytest.mark.parametrize('choice', [
    '실행할 때 넣을게요', '실행 시 파일을 업로드할게요',
    '실행할 때 자료를 제공하겠습니다', '실행할 때 PDF를 첨부할게요',
])
def test_runtime_choice_survives_followup_about_the_same_file(choice):
    assert missing_attachment_reply(dialogue(ORIGINAL, choice, FOLLOWUP)) is None


def test_followup_can_use_a_single_generic_reference_to_the_deferred_file():
    assert missing_attachment_reply(dialogue(
        ORIGINAL, '실행할 때 넣을게요', '요약은 짧게 해줘', '첨부한 PDF의 해지 조건도 검토해줘')) is None


@pytest.mark.parametrize('followup', [
    '첨부한 견적서 PDF도 검토해줘',
    '첨부한 매출 CSV도 검토해줘',
    '지금 첨부한 계약서 PDF를 검토해줘',
    '새로 첨부한 계약서 PDF를 검토해줘',
])
def test_runtime_choice_does_not_cover_a_new_or_immediate_upload(followup):
    result = missing_attachment_reply(dialogue(ORIGINAL, '실행할 때 넣을게요', followup))
    assert result['draft']['questions'] == [QUESTION]


@pytest.mark.parametrize('choice', [
    '실행할 때 넣지 않을게요', '지금 넣을게요', '실행 시간이 궁금해요',
    '"실행할 때 넣을게요"라는 문구를 찾아줘',
])
def test_an_unrelated_or_negative_answer_does_not_create_a_runtime_choice(choice):
    assert missing_attachment_reply(dialogue(ORIGINAL, choice, FOLLOWUP))['draft']['questions'] == [QUESTION]


@pytest.mark.parametrize('change', ['다른 파일로 바꿀게요', '새로운 PDF로 바꿀게요'])
def test_changing_to_an_unnamed_file_does_not_reuse_the_old_file_choice(change):
    result = missing_attachment_reply(dialogue(
        ORIGINAL, '실행할 때 넣을게요', change, '실행할 때 넣을게요', FOLLOWUP))
    assert result['draft']['questions'] == [QUESTION]


def test_a_generic_reference_does_not_pick_between_different_deferred_pdfs():
    result = missing_attachment_reply(dialogue(
        '첨부한 계약서 PDF와 첨부한 견적서 PDF를 비교해줘', '실행할 때 넣을게요',
        '첨부한 PDF의 해지 조건도 검토해줘'))
    assert result['draft']['questions'] == [QUESTION]


def test_upload_guard_still_runs_when_no_runtime_choice_was_made():
    assert missing_attachment_reply(dialogue(ORIGINAL, FOLLOWUP))['draft']['questions'] == [QUESTION]


@pytest.mark.parametrize('prefix', ['첨부파일 ', '첨부 파일 ', '첨부파일인 '])
def test_named_attachment_marker_does_not_defer_a_different_document(prefix):
    result = missing_attachment_reply(dialogue(
        prefix + '계약서 PDF를 검토해줘', '실행할 때 넣을게요',
        prefix + '견적서 PDF도 검토해줘'))
    assert result['draft']['questions'] == [QUESTION]


@pytest.mark.parametrize('prefix', ['첨부파일 ', '첨부 파일 ', '첨부파일인 '])
def test_named_attachment_marker_keeps_the_same_document_deferred(prefix):
    assert missing_attachment_reply(dialogue(
        prefix + '계약서 PDF를 검토해줘', '실행할 때 넣을게요',
        prefix + '계약서 PDF의 해지 조건도 검토해줘')) is None


def test_generic_attachment_marker_keeps_a_single_runtime_file_deferred():
    assert missing_attachment_reply(dialogue(
        '첨부파일을 검토해줘', '실행할 때 넣을게요',
        '첨부파일의 해지 조건도 검토해줘')) is None


@pytest.mark.parametrize('prefix', ['첨부한 ', '첨부파일 '])
def test_particle_like_filename_prefix_remains_part_of_the_file_reference(prefix):
    result = missing_attachment_reply(dialogue(
        prefix + '인사팀 PDF를 검토해줘', '실행할 때 넣을게요',
        prefix + '사팀 PDF도 검토해줘'))
    assert result['draft']['questions'] == [QUESTION]
