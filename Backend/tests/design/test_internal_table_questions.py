import pytest
from tests.conditions.test_condition_readiness import state
from tests.design.test_builder import draft
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.nodes.supervisor import supervisor_node


def question_state(questions, text='연결한 CSV를 그대로 JSON 파일로 저장해줘.'):
    s = state(text)
    s['resources'] = [{'id': 'sales', 'kind': 'table', 'name': 'sales.csv', 'columns': ['비용']}]
    s['capability_assessment'] = {'decision': 'existing_tools', 'tool_ids': ['read_table', 'write_result']}
    # Gate validity is covered separately; here test validation/supervisor repair behavior.
    from agent_builder.capabilities.capability_gate import input_fingerprint
    s['capability_fingerprint'] = input_fingerprint(s)
    d = draft(questions)
    d.update(tool_ids=[], document_ids=['sales'], requirements=[], workflow=None)
    s['draft'] = d
    s['messages'].append({'role': 'assistant', 'content': '\n'.join(questions)})
    return s


@pytest.mark.parametrize('question', ['CSV 파일의 실제 자료 ID를 알려주세요.', '파일 ID가 뭔가요?',
    'JSON 저장 경로의 폴더명을 입력해주세요.', '저장 위치를 알려주세요.'])
def test_internal_questions_are_repaired_not_asked_or_approved(question):
    s = question_state([question]); r = validate_node(s)
    assert not r['draft']['questions']
    assert r['issues'] and r['status'] != 'ready'
    assert question not in r['messages'][-1]['content']
    assert supervisor_node({**s, **r})['route'] == 'repair'
    assert supervisor_node({**s, **r, 'repair_count': 2})['route'] == 'invalid'


def test_business_question_and_option_alignment_survive_internal_question_repair():
    s = question_state(['파일 ID를 알려주세요.', '결과 파일은 CSV와 JSON 중 무엇으로 저장할까요?'])
    s['draft']['question_options'] = [[], ['CSV', 'JSON']]
    r = validate_node(s)
    assert r['draft']['questions'] == ['결과 파일은 CSV와 JSON 중 무엇으로 저장할까요?']
    assert r['draft']['question_options'] == [['CSV', 'JSON']]
    assert supervisor_node({**s, **r})['route'] == 'repair'


def test_explicit_external_destination_question_is_not_removed():
    s = question_state(['저장 폴더를 알려주세요.'], 'CSV를 S3 저장소의 특정 폴더에 저장해줘.')
    r = validate_node(s)
    assert r['draft']['questions'] == ['저장 폴더를 알려주세요.']


def test_document_extraction_field_questions_are_unchanged():
    s = question_state(['어떤 항목을 추출할까요?'])
    s['capability_assessment']['tool_ids'] = ['read_document', 'extract_information', 'export_result']
    r = validate_node(s)
    assert r['draft']['questions'] == ['어떤 항목을 추출할까요?']


def test_file_id_column_question_is_not_internal():
    s = question_state(['file_id 열에서 어떤 값을 남길까요?'])
    s['resources'][0]['columns'].append('file_id')
    result = validate_node(s)
    assert result['draft']['questions'] == ['file_id 열에서 어떤 값을 남길까요?']
