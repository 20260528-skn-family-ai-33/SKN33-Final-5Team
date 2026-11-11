"""Meaning/compiler contracts with injected extraction, not claims of LLM accuracy."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.gmail_summary import gmail_summary_reply, gmail_summary_request, UNREAD_QUERY
from agent_builder.design.request_meaning import (
    extract_meaning, gmail_selection, sealed_meaning, validate_meaning, make_meaning_node, MeaningExtractionError,
)
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.temporal_context import ensure_temporal_context
from tests.conditions.test_condition_readiness import state


class Extractor:
    def __init__(self, *responses, reviews=None):
        self.responses = list(responses)
        self.reviews = list(reviews) if reviews is not None else [dict(faithful=True, issues=[])]
        self.messages = []
        self.schemas = []

    def invoke(self, messages):
        self.messages.append(deepcopy(messages))
        self.schemas.append(deepcopy(self.schema))
        packets = self.reviews if 'faithful' in self.schema.get('properties', {}) else self.responses
        value = packets.pop(0) if len(packets) > 1 else packets[0]
        if isinstance(value, Exception):
            raise value
        return {'content': json.dumps(value, ensure_ascii=False)}


def meaning(count=5, evidence='5개', period='all', filters=None, actions=None, **task_changes):
    task = dict(source='gmail', actions=actions or ['read', 'summarize'],
                selection=dict(order='newest_first', limit=count, limit_evidence=evidence,
                               limit_source_message=0 if count is not None else None,
                               period=period, period_evidence={'yesterday':'어제', 'today':'오늘'}.get(period),
                               period_source_message=0 if period != 'all' else None,
                               filters=filters or []),
                schedule=None, summary_focus=[], other_requirements=[], source_message=0)
    task.update(task_changes)
    return dict(tasks=[task], unresolved=[])


def interpreted(text, raw=None, now='2026-11-09T15:00:00Z'):
    current = state(text)
    current['user_timezone'] = 'Asia/Seoul'
    current.update(ensure_temporal_context(current, now=now))
    transport = Extractor(raw if raw is not None else meaning())
    current.update(extract_meaning(current, transport))
    return current, transport


def mail_arguments(reply):
    mail = next(n for n in reply['draft']['workflow']['nodes'] if n['tool'] == 'email.list')
    return json.loads(mail['settings']['arguments_json'])


def test_extraction_normalizes_sets_and_preserves_provenance_and_clock():
    raw = meaning(filters=['unread', 'unread'], actions=['summarize', 'read', 'summarize'])
    current, transport = interpreted('내 Gmail에서 안 읽은 최근 5개 메일을 요약해줘', raw)
    normalized = sealed_meaning(current)
    assert normalized['tasks'][0]['actions'] == ['read', 'summarize']
    assert normalized['tasks'][0]['selection']['filters'] == ['unread']
    assert normalized['tasks'][0]['selection']['limit_evidence'] == '5개'
    assert normalized['tasks'][0]['source_message'] == 0
    payload = json.loads(transport.messages[0][1]['content'])
    assert payload['user_messages'] == [current['messages'][0]['content']]
    assert payload['temporal_context']['relative_days']['yesterday']['date'] == '2026-11-09'
    assert raw['tasks'][0]['actions'] == ['summarize', 'read', 'summarize']
    assert current['request_meaning_model_calls'] == 2
    assert current['request_meaning_review']['meaning_digest'] == current['request_meaning_digest']
    review_payload = json.loads(transport.messages[1][1]['content'])
    assert review_payload['user_messages'] == payload['user_messages']
    assert review_payload['candidate'] == normalized


@pytest.mark.parametrize(('text', 'evidence'), [
    ('내 gmail 에서 최근 5개 메일을 요약 정리해줘', '5개'),
    ('제 Gmail에서 최신 메일 5개를 요약해 주세요.', '5개'),
    ('메일 다섯 건만 최신순으로 골라 Gmail에서 핵심 내용을 정리해 줘.', '다섯 건'),
    ('Gmail 메일 중 최신 5통의 내용을 간단히 요약 부탁해.', '5통'),
])
def test_equal_meanings_compile_to_same_workflow_as_bare_recipe(text, evidence):
    baseline = gmail_summary_reply(state('gmail 에서 최근 5개 메일을 요약 정리해줘'))
    current, _ = interpreted(text, meaning(evidence=evidence))
    result = gmail_summary_reply(current)
    assert result is not None
    assert result['draft']['workflow'] == baseline['draft']['workflow']
    assert result['draft']['questions'] == []
    assert result['draft']['requirements'][0]['quote'] == text
    assert coverage_report({**current, **result}, result['draft'])['issues'] == []


def test_different_counts_change_executable_argument_and_summary_scope():
    five, _ = interpreted('내 Gmail의 최근 5개 메일을 요약해줘')
    fifty, _ = interpreted('내 Gmail의 최근 50개 메일을 요약해줘', meaning(count=50, evidence='50개'))
    first, second = gmail_summary_reply(five), gmail_summary_reply(fifty)
    assert mail_arguments(first)['max_results'] == 5
    assert mail_arguments(second)['max_results'] == 50
    assert first['draft']['workflow'] != second['draft']['workflow']
    assert '50' in second['draft']['workflow']['nodes'][1]['settings']['instructions']


@pytest.mark.parametrize(('period', 'word'), [('yesterday', '어제'), ('today', '오늘')])
def test_relative_day_uses_request_anchor_and_preserves_query_in_draft(period, word):
    current, _ = interpreted(f'내 Gmail에서 {word} 온 메일을 요약해줘',
                             meaning(count=None, evidence=None, period=period))
    day = current['temporal_context']['user_message_anchors'][0]['relative_days'][period]
    expected = f"after:{day['start_epoch']} before:{day['end_epoch']} -in:sent -in:drafts"
    assert gmail_selection(current)['query'] == expected
    result = gmail_summary_reply(current)
    assert mail_arguments(result) == dict(max_results=100, query=expected)
    assert result['draft']['schedule'] is None and result['draft']['questions'] == []
    assert coverage_report({**current, **result}, result['draft'])['issues'] == []
    current.update(ensure_temporal_context(current, now='2026-11-10T16:00:00Z'))
    assert gmail_selection(current)['query'] == expected


def test_unread_filter_survives_both_selection_and_workflow():
    current, _ = interpreted('내 Gmail에서 어제 온 안 읽은 메일 5개를 요약해줘',
                             meaning(period='yesterday', filters=['unread']))
    selection = gmail_selection(current)
    assert selection['query'].endswith(UNREAD_QUERY)
    result = gmail_summary_reply(current)
    assert mail_arguments(result)['query'] == selection['query']
    assert coverage_report({**current, **result}, result['draft'])['issues'] == []


def test_followup_count_change_keeps_original_relative_day_across_midnight():
    current, _ = interpreted('내 Gmail에서 어제 온 메일 5개를 요약해줘', meaning(period='yesterday'))
    original_query = gmail_selection(current)['query']
    current['messages'].append(dict(role='user', content='50개로 늘려줘'))
    current.update(ensure_temporal_context(current, now='2026-11-10T15:00:00Z'))
    revised = meaning(count=50, evidence='50개', period='yesterday')
    revised['tasks'][0]['selection']['limit_source_message'] = 1
    current.update(extract_meaning(current, Extractor(revised)))
    selected = gmail_selection(current)
    assert selected['count'] == 50 and selected['query'] == original_query
    result = gmail_summary_reply(current)
    assert mail_arguments(result) == dict(max_results=50, query=original_query)
    assert coverage_report({**current, **result}, result['draft'])['issues'] == []


def test_semantic_update_cannot_erase_custom_work_in_saved_workflow():
    current, _ = interpreted('내 Gmail에서 최근 5개 메일을 요약해줘')
    existing = gmail_summary_reply(current)['draft']
    extra = deepcopy(existing['workflow']['nodes'][1])
    extra.update(id='export', tool='export_result', label='파일로 저장',
                 bindings=[dict(input='text', node_id='summary', output='text')])
    extra['settings'].update(instructions=None, format='md')
    existing['workflow']['nodes'].append(extra)
    existing['workflow']['edges'].append(dict(source='summary', target='export', branch='next'))
    existing['tool_ids'].append('export_result')
    current.update(base_agent_id='saved-agent', draft=existing)
    current.update(extract_meaning(current, Extractor(meaning())))
    assert gmail_summary_request(current) is None
    assert gmail_summary_reply(current) is None


@pytest.mark.parametrize(('suffix', 'changes'), [
    ('그리고 동료에게 메일로 보내줘', dict(actions=['read', 'summarize', 'send'])),
    ('그리고 CSV 파일로 저장해줘', dict(actions=['read', 'summarize', 'save'])),
    ('첨부파일도 요약해줘', dict(other_requirements=['첨부파일도 요약'])),
    ('그 후 삭제해줘', dict(actions=['read', 'summarize', 'delete'])),
    ('영어로 번역해줘', dict(actions=['read', 'summarize', 'transform'])),
    ('긴급 요청만 따로 정리해줘', dict(summary_focus=['긴급 요청'])),
])
def test_additional_work_never_enters_bounded_summary_recipe(suffix, changes):
    current, _ = interpreted('내 Gmail에서 최근 5개 메일을 요약해줘. ' + suffix, meaning(**changes))
    assert gmail_selection(current) is None
    assert gmail_summary_request(current) is None
    assert gmail_summary_reply(current) is None


def test_independent_task_and_unsupported_filters_are_not_discarded():
    raw = meaning()
    second = deepcopy(raw['tasks'][0])
    second.update(source='slack', other_requirements=['Slack 대화 요약'])
    raw['tasks'].append(second)
    current, _ = interpreted('내 Gmail 최근 5개 메일과 Slack 대화를 요약해줘', raw)
    assert gmail_selection(current) is None and gmail_summary_reply(current) is None
    for filters in (['read'], ['발신자가 특정 고객'], ['제목에 결제 포함']):
        current, _ = interpreted('내 Gmail 최근 5개 메일을 조건에 맞게 요약해줘', meaning(filters=filters))
        assert gmail_selection(current) is None and gmail_summary_reply(current) is None


@pytest.mark.parametrize('change', ['meaning', 'user', 'clock', 'resource'])
def test_tampered_or_stale_meaning_is_not_reused(change):
    current, _ = interpreted('내 Gmail에서 최근 5개 메일을 요약해줘')
    if change == 'meaning':
        current['request_meaning']['tasks'][0]['selection']['limit'] = 50
    elif change == 'user':
        current['messages'][0]['content'] = '내 Gmail에서 최근 50개 메일을 요약해줘'
    elif change == 'clock':
        current['temporal_context']['user_message_anchors'][0]['relative_days']['today']['date'] = '2030-01-01'
    else:
        current['resources'].append(dict(id='new', kind='document', name='추가 자료'))
    assert sealed_meaning(current) is None
    assert gmail_selection(current) is None


def test_quantity_must_be_grounded_and_invalid_response_gets_one_repair():
    current = state('내 Gmail에서 최근 5개 메일을 요약해줘')
    invalid = meaning(count=50)
    with pytest.raises(ValueError, match='조회 개수'):
        validate_meaning(invalid, current)
    transport = Extractor(invalid, meaning())
    update = extract_meaning(current, transport)
    assert update['request_meaning']['tasks'][0]['selection']['limit'] == 5
    assert len(transport.messages) == 3 and update['request_meaning_model_calls'] == 3
    assert '추출 계약 오류' in transport.messages[1][-1]['content']
    with pytest.raises(ValueError):
        validate_meaning(meaning(count=True, evidence='5개'), current)


def test_quantity_cannot_borrow_evidence_from_a_different_source_turn():
    current = state('표에서 최근 50개 행을 정리해줘')
    current['messages'].append(dict(role='user', content='내 Gmail의 최근 5개 메일도 요약해줘'))
    raw = meaning(count=50, evidence='50개', source_message=1)
    raw['tasks'][0]['selection']['limit_source_message'] = 1
    with pytest.raises(ValueError):
        validate_meaning(raw, current)


def test_relative_period_needs_matching_literal_evidence_in_source_turn():
    current = state('내 Gmail에서 최근 5개 메일을 요약해줘')
    with pytest.raises(ValueError):
        validate_meaning(meaning(period='yesterday'), current)
    current = state('내 Gmail에서 오늘 온 메일 5개를 요약해줘')
    raw = meaning(period='yesterday')
    raw['tasks'][0]['selection']['period_evidence'] = '오늘'
    with pytest.raises(ValueError):
        validate_meaning(raw, current)


@pytest.mark.parametrize('field', ['query', 'arguments_json', 'tool', 'workflow'])
def test_extractor_cannot_supply_executable_query_or_tool_settings(field):
    current = state('내 Gmail에서 최근 5개 메일을 요약해줘')
    raw = meaning()
    raw['tasks'][0][field] = '모델이 만든 실행 설정'
    with pytest.raises(ValueError):
        validate_meaning(raw, current)


@pytest.mark.parametrize(('text', 'candidate', 'issue'), [
    ('내 Gmail 최근 5개 메일을 요약하고 동료에게 보내줘', meaning(), '요약 결과 발송 요청이 누락됨'),
    ('내 Gmail 최근 5개 메일만 요약해줘. 발송하지 마', meaning(actions=['read', 'summarize', 'send']),
     '금지한 발송 동작이 추가됨'),
])
def test_independent_review_refuses_omitted_or_invented_actions(text, candidate, issue):
    transport = Extractor(candidate, reviews=[dict(faithful=False, issues=[issue])])
    with pytest.raises(MeaningExtractionError, match=issue) as caught:
        extract_meaning(state(text), transport)
    assert caught.value.model_calls == 4
    assert ['faithful' in schema['properties'] for schema in transport.schemas] == [False, True, False, True]
    for index in (1, 3):
        review = transport.messages[index]
        assert len(review) == 2
        assert json.loads(review[1]['content'])['user_messages'] == [text]


def test_review_failure_can_repair_full_meaning_but_cannot_drop_extra_work():
    text = '내 Gmail 최근 5개 메일을 요약하고 CSV 파일로 저장해줘'
    transport = Extractor(meaning(), meaning(actions=['read', 'summarize', 'save']),
                          reviews=[dict(faithful=False, issues=['파일 저장 누락']), dict(faithful=True, issues=[])])
    current = state(text)
    current.update(extract_meaning(current, transport))
    assert current['request_meaning_model_calls'] == 4
    assert 'save' in sealed_meaning(current)['tasks'][0]['actions']
    assert gmail_summary_request(current) is None
    assert ['tasks' in schema['properties'] for schema in transport.schemas] == [True, False, True, False]


@pytest.mark.parametrize('change', ['missing', 'digest', 'request', 'faithful', 'issues'])
def test_meaning_requires_current_successful_review(change):
    current, _ = interpreted('내 Gmail 최근 5개 메일을 요약해줘')
    if change == 'missing':
        current.pop('request_meaning_review')
    elif change == 'digest':
        current['request_meaning_review']['meaning_digest'] = 'old-candidate'
    elif change == 'request':
        current['request_meaning_review']['request_fingerprint'] = 'old-request'
    elif change == 'faithful':
        current['request_meaning_review']['faithful'] = False
    else:
        current['request_meaning_review']['issues'] = ['누락된 조건']
    assert sealed_meaning(current) is None
    assert gmail_summary_request(current) is None


@pytest.mark.parametrize('review', [dict(faithful=True, issues=['누락된 조건']), dict(faithful=False, issues=[]),
                                  dict(faithful=False, issues=[' ']), dict(faithful='true', issues=[])])
def test_inconsistent_review_is_never_sealed(review):
    with pytest.raises(MeaningExtractionError):
        extract_meaning(state('내 Gmail 최근 5개 메일을 요약해줘'), Extractor(meaning(), reviews=[review]))


def test_failed_audit_falls_back_to_general_planner_without_trusted_meaning():
    current = state('내 Gmail 최근 5개 메일을 요약하고 보내줘')
    transport = Extractor(meaning(), reviews=[dict(faithful=False, issues=['발송 누락'])])
    update = make_meaning_node('test', transport=transport)(current)
    assert update['request_meaning'] is None and update['request_meaning_review'] is None
    assert update['request_meaning_model_calls'] == 4
    assert update['trace'][-1]['model_calls'] == 4
    assert '일반 설계 경로' in update['trace'][-1]['detail']
    assert gmail_summary_request({**current, **update}) is None


def test_review_transport_failure_records_attempts_and_never_reuses_old_review():
    current, _ = interpreted('내 Gmail 최근 5개 메일을 요약해줘')
    current['messages'].append(dict(role='user', content='요약 후 보내줘'))
    update = make_meaning_node('test', transport=Extractor(meaning(), reviews=[OSError('review unavailable')]))(current)
    assert update['request_meaning'] is None and update['request_meaning_review'] is None
    assert update['request_meaning_model_calls'] == 2


@pytest.mark.parametrize('change', ['count', 'query', 'binding'])
def test_coverage_rejects_changed_count_query_or_content_binding(change):
    current, _ = interpreted('내 Gmail에서 어제 온 메일 5개를 요약해줘', meaning(period='yesterday'))
    result = gmail_summary_reply(current)
    assert result is not None
    altered = deepcopy(result['draft'])
    mail, summary = altered['workflow']['nodes']
    arguments = json.loads(mail['settings']['arguments_json'])
    if change == 'count':
        arguments['max_results'] = 50
    elif change == 'query':
        arguments.pop('query')
    else:
        summary['bindings'][0]['output'] = 'result'
    mail['settings']['arguments_json'] = json.dumps(arguments)
    assert coverage_report({**current, **result}, altered)['issues']
