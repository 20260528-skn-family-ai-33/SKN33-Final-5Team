import pytest

from agent_builder.capabilities.capability_check import make_capability_check
from tests.conditions.test_condition_readiness import Fake, state


def check(text):
    current = state(text)
    packet = {'decision': 'existing_tools', 'tool_ids': ['read_table', 'write_result'],
              'missing_conditions': [], 'missing_actions': [], 'reason': '표를 두 형식으로 쓸 수 있습니다.',
              'user_quotes': [text]}
    transport = Fake([packet])
    return make_capability_check('fixture', transport, 'enforce')(current)


@pytest.mark.parametrize('request_text,formats', [
    ('연결한 창고정산 원본에서 CSV 한 파일과 JSON 한 파일을 둘 다 만들어 이번 실행 화면에 각각 독립적으로 다운로드 가능한 링크 두 개로 줘. 압축이나 한 파일만 주는 방식은 안 돼.', ('CSV', 'JSON')),
    ('PDF와 CSV로 저장하고 실행 결과 화면에 각 파일의 다운로드 링크를 하나씩 제공해줘.', ('PDF', 'CSV')),
    ('실행 결과 화면에 두 개의 다운로드 링크를 표시해줘. 결과 형식은 JSON과 CSV야.', ('JSON', 'CSV')),
    ('이번 실행 결과 화면에 XLSX와 CSV의 다운로드 링크 두 개를 게시해 주세요.', ('XLSX', 'CSV')),
])
def test_distinct_download_links_are_an_unsupported_publication_contract(request_text, formats):
    result = check(request_text)
    assessment = result['capability_assessment']
    assert result.get('status') == 'unsupported'
    assert not result['pending_questions'] and not assessment['missing_conditions']
    assert all(token in assessment['missing_actions'][0] for token in (*formats, '다운로드'))
    assert assessment['model_calls'] == 0


@pytest.mark.parametrize('text', [
    'CSV 원본을 읽고 JSON 결과 파일 하나를 다운로드하게 해줘.',
    '다운로드 링크 두 개에서 CSV와 JSON을 읽어 하나의 파일로 저장해줘.',
    'CSV와 JSON을 처리해줘. 다운로드 링크 두 개는 필요 없어. 결과는 하나면 돼.',
    'CSV와 JSON의 다운로드 링크 두 개가 필요한지 먼저 물어봐.',
    'CSV 파일과 JSON 파일을 만들어 묶어서 링크 하나로 줘.',
    'CSV 하나는 다운로드하게 하고 JSON은 메일로 보내줘.',
    'CSV와 JSON 설명서의 기존 다운로드 링크 두 개를 줘.',
    'CSV와 JSON은 실행 화면에 다운로드 링크 두 개로 줘. 아, 링크 하나만 줘.',
    'CSV와 JSON은 실행 화면에 다운로드 링크 두 개로 줘. 두 링크로 제공하는 요구는 취소하고 CSV만 저장해줘.',
    'CSV와 JSON은 실행 화면에 다운로드 링크 두 개로 줘. JSON만 저장해줘.',
    '실행 결과 화면에 CSV와 JSON의 다운로드 링크 두 개가 필요한지 물어봐줘.',
    '"실행 화면에 CSV와 JSON 다운로드 링크 두 개로 줘"라는 문구를 PDF에 넣어줘.',
])
def test_inputs_negations_alternatives_and_separate_destinations_are_not_a_publication_gap(text):
    assert check(text)['capability_assessment']['decision'] == 'existing_tools'
