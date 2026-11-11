"""Public draft text explains executable steps without publishing model prose."""
from copy import deepcopy
import json

from agent_builder.design.presentation import public_draft_explanation


def node(identifier, tool, **settings):
    return dict(id=identifier, tool=tool, label='비공개 분석과 추론', settings=settings, bindings=[])


def draft(nodes, edges, entry):
    return dict(explanation='먼저 의도를 분석해야 한다.', purpose='비공개 추론',
                instructions='사용자의 요청을 분석한 내부 지침', questions=[], unsupported=[],
                workflow=dict(entry=entry, nodes=nodes, edges=edges))


def edge(source, target, branch='next'):
    return dict(source=source, target=target, branch=branch)


def test_mail_summary_explains_actual_count_and_path_without_model_prose():
    original = draft([
        node('summary', 'compose_answer', instructions='비공개 분석과 추론'),
        node('mail', 'email.list', operation='read_messages',
             arguments_json=json.dumps(dict(max_results=5, query='from:private@example.com'))),
    ], [edge('mail', 'summary')], 'mail')
    before = deepcopy(original)
    text = public_draft_explanation(original)['explanation']
    assert '메일 본문 읽기 (최대 5건, 검색 조건 적용) → 근거 기반 답변' in text
    for hidden in ('비공개', '의도를 분석', 'private@example.com', '실행했습니다', '검증을 통과'):
        assert hidden not in text
    assert original == before


def test_nonmail_draft_explains_actual_filter_count_and_output_format():
    original = draft([
        node('save', 'write_result', format='csv'),
        node('filter', 'filter_sort', execution_plan=dict(filters=[{}], sort=[{}])),
        node('read', 'read_table'),
    ], [edge('read', 'filter'), edge('filter', 'save')], 'read')
    text = public_draft_explanation(original)['explanation']
    assert '표 파일 읽기 → 조건에 따라 정리 (선별 조건 1개, 정렬 기준 1개) → 결과 파일 만들기 (CSV)' in text


def test_clarification_keeps_questions_and_marks_existing_graph_as_draft():
    original = draft([node('summary', 'compose_answer')], [], 'summary')
    original['questions'] = ['어떤 항목을 요약할까요?']
    result = public_draft_explanation(original)
    assert result['explanation'].startswith('아래 항목을 확인해 주세요.\n초안의 처리 순서:')
    assert result['questions'] == original['questions']
    original['unsupported'] = ['등록되지 않은 외부 동작']
    assert public_draft_explanation(original)['explanation'] == '요청한 작업의 지원 범위를 확인해 주세요.'


def test_branch_does_not_describe_both_outcomes_as_sequential_execution():
    original = draft([node('choose', 'condition'), node('yes', 'compose_answer'), node('no', 'finish')],
                     [edge('choose', 'yes', 'yes'), edge('choose', 'no', 'no')], 'choose')
    text = public_draft_explanation(original)['explanation']
    assert '초안의 처리 순서: 조건 분기' in text and '분기별 후속 단계' in text
    assert '근거 기반 답변' not in text and '완료 안내' not in text


def test_invalid_or_unknown_graph_never_echoes_generated_text_or_claims_success():
    cyclic = draft([node('one', 'read_table')], [edge('one', 'one')], 'one')
    assert '처리 순서를 확인하지 못했습니다' in public_draft_explanation(cyclic)['explanation']
    unknown = draft([node('one', '비공개 추론', operation='비공개 추론')], [], 'one')
    text = public_draft_explanation(unknown)['explanation']
    assert '지원 확인이 필요한 단계' in text and '비공개 추론' not in text
    invalid_args = draft([node('one', 'email.list', operation='read_messages', arguments_json='["비공개 추론"]')], [], 'one')
    text = public_draft_explanation(invalid_args)['explanation']
    assert '설정 확인 필요' in text and '비공개 추론' not in text


def test_operation_label_and_typed_settings_are_checked_against_catalogue():
    original = draft([node('one', 'compose_answer', operation='summarize',
                           arguments_json=json.dumps(dict(max_results=5, format='pdf')))], [], 'one')
    text = public_draft_explanation(original)['explanation']
    assert '근거 자료 요약' in text and '최대 5건' not in text and 'PDF' not in text
    original['workflow']['nodes'][0]['settings']['operation'] = '비공개 추론'
    text = public_draft_explanation(original)['explanation']
    assert '동작 확인 필요' in text and '비공개 추론' not in text
