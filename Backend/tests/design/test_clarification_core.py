"""Clarification core: user decisions wait as questions, system failures stay retryable.

Real LangGraph + SQLite through the service entry; model replies are fixed fixtures.
"""
import json
import pytest

from agent_builder.conditions.clarification_core import design_time_file_request, statement_question, open_required
from agent_builder.conditions.condition_readiness import policy_clauses, judge_conditions, source_catalog
from agent_builder.design.document_rows_template import document_rows_reply, document_rows_applies
from agent_builder.design.file_timing import refers_to_attached_file
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from service.worker import handle
from tests.conditions.test_condition_readiness import state as table_state, Fake as ConditionFake
from tests.design.test_builder import initial
from tests.design.test_clarification_turns import Fake, assessment


class RequirementTransport:
    """Fixed requirement-extraction replies; the real extract_plan/validate_plan/coverage run."""
    schema = None
    def __init__(self, replies=()): self.replies = list(replies); self.calls = 0
    def invoke(self, messages):
        self.calls += 1
        return {'content': json.dumps(self.replies.pop(0), ensure_ascii=False)}


def extraction(*unresolved):
    return {'tasks': [{'source_message': 0, 'description': '계약서 위험 조항 검토', 'spec': {'kind': 'document_analysis'}}],
            'unresolved': list(unresolved)}


def analysis_planner(calls, request):
    """A valid document-analysis draft that covers the extracted requirement."""
    from tests.design.test_runtime_file_questions import question_draft
    from tests.workflow_engine.test_workflow import node, bind, edge
    def planner(s):
        calls.append(s['messages'][-1]['content'])
        nodes = [node('read', 'read_document'),
                 node('answer', 'compose_answer', [bind('documents', 'read', 'documents')], instructions=request)]
        d = question_draft('unused')
        d.update(name='계약서 검토', purpose='위험 조항 확인', tool_ids=[n['tool'] for n in nodes], questions=[],
                 question_options=[], requirements=[dict(description='검토', source_id='user', quote=request)],
                 workflow=dict(entry='read', nodes=nodes, edges=[edge('read', 'answer')]))
        return dict(draft=d, confirmed=False, repair_count=s.get('repair_count', 0) + 1)
    return planner


@pytest.fixture
def service(tmp_path, monkeypatch):
    import service.handlers.design as worker
    original = worker.build_graph
    box = {'requirement': RequirementTransport()}
    def wrapped(*args, **kwargs):
        kwargs.update(capability_mode='enforce', capability_transport=box['cap'], condition_mode='off',
                      requirement_mode='enforce', requirement_transport=box['requirement'], verifier_mode='off')
        return original(*args, **kwargs)
    monkeypatch.setattr(worker, 'build_graph', wrapped)
    session = handle({'directory': str(tmp_path), 'action': 'new_session', 'model': 'test', 'credential_id': uid()})
    current = {'s': session}
    def act(name, planner=None, **fields):
        result = handle({'directory': str(tmp_path), 'id': current['s']['id'], 'revision': current['s']['revision'],
                         'action': name, **fields}, conversation=planner)
        current['s'] = result
        return result
    def saved():
        store = Storage(tmp_path)
        try: return len(store.agents())
        finally: store.close()
    return box, act, saved


REQUEST = '계약서를 검토하고 위험한 조항만 근거를 붙여 답해줘'


def test_unresolved_requirement_waits_as_a_question_and_resumes_after_the_answer(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(REQUEST), assessment(REQUEST)])
    # main's requirement clarification (2026-11-12) shows the model's question as written.
    box['requirement'].replies = [extraction('위험 판단 기준을 알려주세요.'), extraction()]
    calls = []
    first = act('message', analysis_planner(calls, REQUEST), content=REQUEST)
    assert first['status'] == 'needs_clarification' and calls == []
    assert [q['text'] for q in first['pending_questions']] == ['위험 판단 기준을 알려주세요.']
    assert first['pending_questions'][0]['source'] in ('requirement', 'requirements')
    qid = first['pending_questions'][0]['id']
    with pytest.raises(ValueError):
        act('confirm')
    answered = act('message', analysis_planner(calls, REQUEST), content='위약금이 있는 조항을 위험으로 봐줘', question_id=qid)
    assert answered['status'] == 'ready' and calls == ['위약금이 있는 조항을 위험으로 봐줘']
    assert not answered['pending_questions'] and saved() == 0
    assert act('confirm')['status'] == 'saved' and saved() == 1


def test_requirement_question_id_is_stable_and_side_question_keeps_it(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(REQUEST)])
    box['requirement'].replies = [extraction('위험 판단 기준이 미정입니다')]
    first = act('message', analysis_planner([], REQUEST), content=REQUEST)
    side = act('message', analysis_planner([], REQUEST), content='오늘 날씨 어때?')
    assert [q['id'] for q in side['pending_questions']] == [q['id'] for q in first['pending_questions']]
    assert side['status'] == 'needs_clarification' and saved() == 0 and box['requirement'].calls == 1


def test_requirement_model_failure_is_retryable_and_never_a_question(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(REQUEST), assessment(REQUEST)])
    box['requirement'].replies = [{}, {}, extraction()]  # two contract failures, then a valid plan
    calls = []
    failed = act('message', analysis_planner(calls, REQUEST), content=REQUEST)
    # main records the retryable failure as requirement_failure (2026-11-12).
    assert failed['status'] == 'validation_failed' and failed['requirement_failure']['retryable'] is True
    assert not failed['pending_questions'] and calls == []
    retried = act('message', analysis_planner(calls, REQUEST), content=REQUEST)
    assert [m['content'] for m in retried['messages'] if m['role'] == 'user'] == [REQUEST]  # no duplicate request
    assert retried['status'] == 'ready' and not retried.get('requirement_failure') and len(calls) == 1


def test_capability_contract_failure_is_retryable(service):
    box, act, saved = service
    class Broken:
        schema = None
        def __init__(self): self.calls = 0
        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return {'content': '{"decision":"existing_tools"'}  # truncated JSON
            return {'content': json.dumps(assessment(REQUEST), ensure_ascii=False)}
    box['cap'] = Broken()
    box['requirement'].replies = [extraction()]
    calls = []
    failed = act('message', analysis_planner(calls, REQUEST), content=REQUEST)
    assert failed['system_failure']['stage'] == 'capability_check' and '다시 보내면' in failed['messages'][-1]['content']
    assert calls == [] and not failed['pending_questions']
    retried = act('message', analysis_planner(calls, REQUEST), content=REQUEST)
    assert retried['status'] == 'ready' and [m['content'] for m in retried['messages'] if m['role'] == 'user'] == [REQUEST]


def test_design_time_file_request_is_not_shown_as_a_capability_question(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(REQUEST, ['계약서 파일 또는 문서 ID 연결 필요']), assessment(REQUEST)])
    box['requirement'].replies = [extraction()]
    calls = []
    result = act('message', analysis_planner(calls, REQUEST), content=REQUEST)
    assert box['cap'].calls == 2 and result['status'] == 'ready'
    assert not any('파일' in q['text'] for q in result['pending_questions'])


@pytest.mark.parametrize('text,expected', [
    ('계약서 파일 또는 문서 ID 연결 필요', True), ('처리할 PDF를 업로드해 주세요', True),
    ('CSV 파일에서 집계할 열을 알려주세요', False), ('문서에서 추출할 항목 제공 필요', False),
    ('어떤 위험 기준을 적용할까요?', False)])
def test_design_time_file_request_detection_keeps_questions_about_file_content(text, expected):
    assert design_time_file_request(text) is expected


def test_missing_statement_becomes_a_question():
    assert statement_question('선별 기준 금액이 미정입니다') == '선별 기준 금액을 알려주세요.'
    assert statement_question('최대 행 수가 미정') == '최대 행 수를 알려주세요.'
    assert statement_question('어떤 열을 쓸까요?') == '어떤 열을 쓸까요?'
    assert open_required({'pending_questions': [{'id': 'a'}, {'id': 'b', 'required': False}]}) == [{'id': 'a'}]


@pytest.mark.parametrize('text,key,value', [
    ('빈 값은 상관없으니 그대로 유지해줘', 'missing.policy', 'keep'),
    ('빈 값은 상관없어', 'missing.policy', 'conflict'),
    ('빈 값은 알아서 제외해', 'missing.policy', 'exclude'),
    ('빈 값은 평균으로 채워서 유지해', 'missing.policy', 'conflict'),
    ('빈 값은 나중에 정할게', 'missing.policy', 'conflict'),
    ('중복은 상관없으니 모두 유지해', 'duplicate.policy', 'keep'),
    ('중복은 알아서 해줘', 'duplicate.policy', 'conflict'),
])
def test_indifference_does_not_override_one_explicit_policy(text, key, value):
    assert (key, value) in [(k, v) for k, v, _ in policy_clauses(text, ['비용'])]


SCHEDULED = '매일 오전 9시 한국 시간에 비용이 100만원 초과인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'
TABLE = [{'id': 't', 'name': '매출.csv', 'kind': 'table', 'format': 'csv', 'columns': ['비용', '지역'],
          'profile': {'비용': {'types': ['number']}}, 'rows': 3}]


@pytest.mark.parametrize('follow,decision,schedule', [
    (None, 'ready', True), ('예약 없이 한 번만 실행할게요', 'ready', False),
    ('예약은 취소해줘', 'ready', False), ('작업 취소해줘', 'manual_review', True),
    ('반복 실행은 필요 없어요', 'ready', False), ('스케줄 없이 해줘', 'ready', False),
    ('자동 실행은 하지 마', 'ready', False), ('매일 말고 그냥 한 번만 돌려줘', 'ready', False),
    ('예약은 그대로 두고 빈 값만 제외해', 'ready', True)])
def test_schedule_removal_drops_only_the_schedule(follow, decision, schedule):
    s = table_state(SCHEDULED); s['resources'] = TABLE
    if follow:
        s['messages'].append({'role': 'user', 'content': follow})
    catalog, _ = source_catalog(s)
    source = next(e['id'] for e in catalog if e['source_id'] == 'user' and e.get('turn') == 0)
    values = {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000',
              'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'}
    proposal = {'operations': ['filter', 'schedule'], 'unsupported_actions': [],
                'conditions': [{'key': k, 'value': v, 'status': 'specified', 'evidence_ids': [source]} for k, v in values.items()]}
    ledger = judge_conditions(s, ConditionFake([proposal]))['ledger']
    assert ledger['decision'] == decision
    assert ('schedule' in ledger['operations']) is schedule
    assert any(k.startswith('schedule.') for k in ledger['slots']) is schedule
    assert 'filter' in ledger['operations']


DOC = {'id': 'd1', 'name': '회의자료.pdf', 'kind': 'document', 'format': 'pdf', 'text': 'x', 'pages': ['p'], 'columns': []}


def doc_state(text):
    return {**initial(), 'messages': [{'role': 'user', 'content': text}], 'resources': [DOC]}


@pytest.mark.parametrize('text', [
    '문서에서 품목, 금액을 뽑아 금액이 10만원 이상인 것만 남기고 금액 큰 순서로 정렬해서 CSV로 정리해줘',
    '문서에서 품목, 금액을 뽑아 금액 내림차순으로 표로 정리해줘', '문서에서 품목, 금액을 뽑아 금액 상위 5개만 표로 정리해줘',
    '문서에서 날짜, 금액을 뽑아 날짜별로 묶어서 표로 정리해줘', '문서에서 품목, 금액을 뽑아 중복 없이 표로 정리해줘',
    '문서에서 품목, 금액을 뽑아 그룹으로 표로 정리해줘'])
def test_fixed_document_flow_delegates_any_extra_processing_to_the_planner(text):
    s = doc_state(text)
    assert not document_rows_applies(s) and document_rows_reply(s) is None


@pytest.mark.parametrize('text,fields', [
    ('문서에서 품목, 금액을 뽑아서 CSV로 정리해줘', ['품목', '금액']),
    ('영수증에서 날짜와 금액을 한 줄씩 엑셀로 정리해줘', ['날짜', '금액'])])
def test_fixed_document_flow_does_not_ask_for_fields_already_listed(text, fields):
    reply = document_rows_reply(doc_state(text))
    assert reply['draft']['questions'] == []
    extract = next(n for n in reply['draft']['workflow']['nodes'] if n['tool'] == 'extract_information')
    assert extract['settings']['fields'] == fields


def test_fixed_document_flow_still_asks_when_fields_are_not_listed():
    assert document_rows_reply(doc_state('문서 내용을 표로 정리해줘'))['draft']['questions']


@pytest.mark.parametrize('text,expected', [
    ('첨부파일 없이 계약서 해지 조항을 요약해줘', False), ('첨부파일 없이 진행해줘', False),
    ('첨부한 파일은 없어요. 요약 방식만 알려줘', False), ('첨부한 PDF를 요약해줘', True), ('첨부파일을 검토해줘', True)])
def test_absent_attachment_wording_is_not_an_upload_reference(text, expected):
    assert refers_to_attached_file(text) is expected


AMOUNT_TABLE = [{'id': 't', 'name': '매출.csv', 'kind': 'table', 'format': 'csv', 'columns': ['금액', '지역'],
                 'profile': {'금액': {'types': ['number']}}, 'rows': 3}]


@pytest.mark.parametrize('answer,value,operator', [
    ('금액이 50만원 초과인 행이야', 500000.0, 'gt'), ('50만원 이상으로 해줘', 500000.0, 'gte'),
    ('30만원 초과이고 100만원 미만', None, None), ('잘 모르겠어요', None, None)])
def test_threshold_written_next_to_its_comparator_is_not_asked_again(answer, value, operator):
    s = table_state('매출 표에서 금액이 큰 행만 남겨서 저장해줘'); s['resources'] = AMOUNT_TABLE
    s['messages'] += [{'role': 'assistant', 'content': '선별할 기준 값은 얼마인가요?'}, {'role': 'user', 'content': answer}]
    s['pending_questions'] = [{'id': 'q', 'key': 'filter.value', 'source': 'condition', 'text': '선별할 기준 값은 얼마인가요?'}]
    catalog, _ = source_catalog(s)
    first = next(e['id'] for e in catalog if e['source_id'] == 'user' and e.get('turn') == 0)
    proposal = {'operations': ['filter'], 'unsupported_actions': [], 'conditions': [
        {'key': 'filter.field', 'value': '금액', 'status': 'specified', 'evidence_ids': [first]},
        {'key': 'filter.value', 'value': None, 'status': 'missing', 'evidence_ids': []}]}
    slots = judge_conditions(s, ConditionFake([proposal]))['ledger']['slots']
    assert slots.get('filter.value', {}).get('value') == value
    assert slots.get('filter.operator', {}).get('value') == operator


def missing(actions, quote=REQUEST):
    return dict(decision='missing_capability', tool_ids=['read_document'], missing_conditions=[], missing_actions=list(actions),
                reason='t', user_quotes=[quote])


def test_request_to_the_user_is_not_a_missing_capability(service):
    box, act, saved = service
    # initial verdict + its built-in review both return the boilerplate; one grounded re-assessment
    box['cap'] = Fake([missing(['자료 처리 단계와 조건을 확인해 주세요'])] * 2 + [assessment(REQUEST)])
    box['requirement'].replies = [extraction()]
    calls = []
    result = act('message', analysis_planner(calls, REQUEST), content=REQUEST)
    assert box['cap'].calls == 3 and result['status'] == 'ready' and len(calls) == 1


def test_repeated_ungrounded_missing_actions_keep_the_model_verdict(service):
    # One re-assessment with the evidence; a repeated verdict is kept, as main does. Turning
    # it into review made truly unsupported requests (Teams/Notion/PostgreSQL, RunPod
    # 2026-11-11 U02/U04/X01) end as system errors instead of unsupported.
    box, act, saved = service
    box['cap'] = Fake([missing(['자료 처리 단계와 조건을 확인해 주세요'])] * 4)
    result = act('message', analysis_planner([], REQUEST), content=REQUEST)
    assert box['cap'].calls == 4
    assert result['status'] == 'unsupported' and result['capability_assessment']['decision'] == 'missing_capability'
    assert result['capability_assessment']['invalid_missing_actions'] == ['자료 처리 단계와 조건을 확인해 주세요']


def test_a_real_missing_action_stays_unsupported(service):
    box, act, saved = service
    request = '요약 결과를 슬랙 채널로 보내줘'
    box['cap'] = Fake([missing(['슬랙 전송'], request), missing(['슬랙 전송'], request)])
    result = act('message', analysis_planner([], request), content=request)
    assert box['cap'].calls == 2  # no extra re-assessment for a real missing action
    assert result['status'] == 'unsupported' and result['capability_assessment']['decision'] == 'missing_capability'


@pytest.mark.parametrize('text,expected', [
    ('처리할 계약서 파일이나 자료 ID에 대해 알려주세요.', True), ('계약서 파일 ID 또는 경로에 대해 알려주세요.', True),
    ('분석할 계약서 파일이나 자료에 대해 알려주세요.', True), ('비교할 두 문서에 대해 알려주세요.', True),
    ('저장할 파일 형식에 대해 알려주세요.', False), ('결과를 저장할 파일에 대해 알려주세요.', False),
    ('메일 수신자 주소에 대해 알려주세요.', False), ('합계할 금액 열 이름에 대해 알려주세요.', False)])
def test_file_locator_questions_observed_on_runpod_are_design_time_file_requests(text, expected):
    # Questions the capability stage returned on RunPod (2026-11-11 eval D01/K01/K02/D10) and look-alikes.
    assert design_time_file_request(text) is expected
