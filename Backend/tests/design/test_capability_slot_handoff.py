from copy import deepcopy

import pytest
from langgraph import types

from agent_builder.capabilities.capability_check import await_capability_node
from agent_builder.conditions.condition_readiness import source_catalog, validate_conditions
from tests.conditions.test_condition_readiness import state


def answered(monkeypatch, request, question, answer, *, selected='question', second=None):
    current = state(request)
    questions = [dict(id='question', key='capability.condition.0', source='capability',
                      text=question, options=[])]
    if second:
        questions.append(dict(id='second', key='capability.condition.1', source='capability',
                              text=second, options=[]))
    current.update(capability_questions=questions, pending_questions=questions)
    current['messages'].append(dict(role='assistant', content='\n'.join(q['text'] for q in questions)))
    monkeypatch.setattr(types, 'interrupt', lambda payload: dict(type='message', question_id=selected,
                                                               content=answer))
    current.update(await_capability_node(current))
    current['pending_questions'] = []
    current['capability_questions'] = []
    return current


DEDUP = '창고정산에서 batch가 같은 행의 중복 처리 방식은 아직 못 정했어. CSV로 저장하고 빈 셀은 오류로 해줘.'
POLICY_QUESTION = 'batch 가 동일한 경우 중복을 제거할지 유지할지 결정 필요에 대해 알려주세요.'


def test_typed_schedule_and_policy_answers_bind_by_id_when_submitted_in_reverse(monkeypatch):
    from agent_builder.conditions.condition_readiness import choice_options

    current = state('중복 처리 방식을 정하고 표를 예약 실행할게.')
    questions = [
        dict(id='policy', key='duplicate.policy', source='capability', text='중복 처리 정책은 무엇인가요?',
             options=choice_options('duplicate.policy', current)),
        dict(id='clock', key='schedule.time', source='capability', text='몇 시 몇 분에 실행할까요?',
             options=[], response={'type': 'time'}),
    ]
    current.update(capability_questions=questions, pending_questions=questions)
    monkeypatch.setattr(types, 'interrupt', lambda _: {
        'type': 'message', 'content': '17:20, 마지막 행만 남김', 'question_answers': [
            {'question_id': 'clock', 'value': '17:20'},
            {'question_id': 'policy', 'value': '마지막 행만 남김'},
        ],
    })

    event = await_capability_node(current)['capability_answers'][-1]
    slot_by_id = dict(zip(event['question_ids'], event['candidate_keys'], strict=True))
    assert {(slot_by_id[a['question_id']], a['value']) for a in event['answers']} == {
        ('schedule.time', '17:20'), ('duplicate.policy', '마지막 행만 남김'),
    }
    assert {q['id']: q['key'] for q in event['questions']} == slot_by_id


def test_displayed_policy_menu_has_one_owner_and_preserves_other_real_questions():
    from agent_builder.capabilities.capability_check import make_capability_check
    from agent_builder.conditions.condition_readiness import choice_options
    from tests.conditions.test_condition_readiness import Fake
    from tests.capabilities.test_capability_assessment import packet
    current = state('상품 표에서 sku 중복 처리 방식을 정하지 않았어. JSON에 저장해.')
    current['resources'] = [dict(id='catalog', name='상품.csv', kind='table', columns=['sku'])]
    options = list(reversed(choice_options('duplicate.policy', current)))
    menu = "중복된 행을 'sku' 열 기준으로 어떻게 처리할지 (" + ', '.join(options) + ') 를 정해주세요.'
    leaked = 'user_quotes":["상품 표에서 sku 중복 처리 방식을 정하지 않았어."]}**이유:**사용자가 정책을 선택해야 합니다.'
    for extra in ([], ['보낼 이메일 수신자는 누구인가요?']):
        raw = packet(current, 'clarify', tools=['filter_sort'], conditions=[menu, leaked, *extra])
        result = make_capability_check('fixture', Fake([raw]), 'enforce')(current)
        questions = result['pending_questions']
        assert len(questions) == 1 + len(extra)
        assert questions[0]['key'] == 'duplicate.policy'
        assert questions[0]['options'] == choice_options('duplicate.policy', current)
        assert leaked not in result['messages'][-1]['content']
        assert result['capability_assessment']['rejected_metadata_condition_indices'] == [1]
        if extra:
            assert questions[1]['text'] == extra[0]
    metadata_only = packet(current, 'clarify', tools=['filter_sort'], conditions=[leaked])
    invalid = make_capability_check('fixture', Fake([metadata_only]), 'enforce')(current)
    assert invalid['status'] == 'validation_failed' and invalid['pending_questions'] == []
    quoted_literal = '열 이름이 user_quotes인 경우 사용할 파일은 무엇인가요?'
    literal = packet(current, 'clarify', tools=['filter_sort'], conditions=[quoted_literal])
    preserved = make_capability_check('fixture', Fake([literal]), 'enforce')(current)
    assert [q['text'] for q in preserved['pending_questions']] == [quoted_literal]
    # A second user decision after the serialized header cannot be discarded.
    for suffix in ('파일 형식을 추가로 알려주세요.', '어느 폴더를 원하나요?', '아직 해석하지 않은 내용.'):
        ambiguous = leaked[:leaked.index('}') + 1] + suffix
        mixed = packet(current, 'clarify', tools=['filter_sort'], conditions=[menu, ambiguous])
        preserved = make_capability_check('fixture', Fake([mixed]), 'enforce')(current)
        assert len(preserved['pending_questions']) == 2
    # The same menu quoted as a file literal or paired with a second decision
    # does not establish ownership of the full question.
    from agent_builder.capabilities.capability_check import _question_key
    for text in (menu.replace('어떻게 처리할지', '어떻게 처리할지와 최대 행 수를 얼마로 할지'),
                 menu.replace('어떻게 처리할지', '처리할지 취소할지'),
                 '파일에 다음 문장을 넣을까요? ' + menu):
        assert _question_key(text, 'generic') == 'generic'


@pytest.mark.parametrize(('question', 'key'), [
    ('duplicate.policy', 'duplicate.policy'),
    ('중복은 첫 행만 남길지 마지막 행만 유지할지 정책을 골라 주세요.', 'duplicate.policy'),
    ('delivery.recipient', 'delivery.recipient'),
    ('수신자 이메일 주소', 'delivery.recipient'),
    ('수신자 주소와 제목을 알려주세요.', 'unknown'),
    ('중복을 제거할지 유지할지와 파일 이름을 알려주세요.', 'unknown'),
])
def test_question_owner_respects_explicit_keys_and_leaves_composite_intact(question,key):
    from agent_builder.capabilities.capability_check import _question_key
    assert _question_key(question, 'unknown') == key


def test_focused_live_optional_placeholders_do_not_create_unrequested_operations(monkeypatch):
    question = 'batch 가 동일한 경우 중복을 제거할지, 첫 번째 행만 유지할지, 아니면 모두 유지할지에 대한 정책 결정 필요에 대해 알려주세요.'
    current = answered(monkeypatch, DEDUP, question, '원본에서 마지막으로 나온 행만 남겨줘.')
    placeholders = ['filter.field', 'filter.operator', 'filter.value', 'schedule.frequency', 'schedule.hour',
                    'schedule.minute', 'schedule.timezone', 'schedule.weekday', 'schedule.monthday', 'schedule.month_end']
    raw = dict(operation='deduplicate', conditions=[
        dict(key='duplicate.field', status='specified', value='batch', evidence_ids=['user:0:0']),
        dict(key='duplicate.policy', status='specified', value='last', evidence_ids=['user:2:0']),
        dict(key='missing.policy', status='specified', value='error', evidence_ids=['user:0:0']),
        dict(key='output.format', status='specified', value='csv', evidence_ids=['user:0:0']),
        *(dict(key=k, status='missing', value=None, evidence_ids=[]) for k in placeholders)], unsupported_actions=[])
    ledger = validate_conditions(raw, current, source_catalog(current)[0])
    assert ledger['unresolved'] == []
    assert ledger['operations'] == ['deduplicate']
    raw['operation'] = 'filter'
    assert validate_conditions(raw, current, source_catalog(current)[0])['decision'] == 'clarify'
    raw['operation'] = 'deduplicate'
    for extra, expected in [(' 행을 필터링해줘.', 'filter.field'), (' 실행도 예약해줘.', 'schedule.hour')]:
        with_more = deepcopy(current)
        with_more['messages'][0]['content'] += extra
        assert expected in validate_conditions(raw, with_more, source_catalog(with_more)[0])['unresolved']


@pytest.mark.parametrize(('answer', 'selected', 'policy'), [
    ('원본에서 마지막으로 나온 행만 남겨줘.', 'question', 'last'),
    ('처음 나온 행만 보존해줘.', 'question', 'first'),
    ('원본에서 마지막으로 나온 행만 남겨줘.', 'second', None),
    ('마지막 행만 남기지 마.', 'question', None),
    ('마지막 두 행만 남겨줘.', 'question', None),
])
def test_capability_answer_validates_only_the_selected_policy(monkeypatch, answer, selected, policy):
    current = answered(monkeypatch, DEDUP, POLICY_QUESTION, answer, selected=selected, second='파일 이름은 무엇인가요?')
    values = [('duplicate.field', 'batch', ['user:0:0', 'user:2:0']),
              ('duplicate.policy', policy or 'last', ['user:2:0']),
              ('output.format', 'csv', ['user:0:0']), ('missing.policy', 'error', ['user:0:0'])]
    raw = dict(operation='deduplicate', conditions=[
        dict(key=k, status='specified', value=v, evidence_ids=refs) for k, v, refs in values], unsupported_actions=[])
    ledger = validate_conditions(raw, current, source_catalog(current)[0])
    assert ledger['slots']['duplicate.policy']['value'] == policy
    if policy:
        assert ledger['decision'] == 'ready'
        assert ledger['slots']['duplicate.policy']['evidence'][0]['turn'] == 2
        omitted = deepcopy(raw)
        omitted['conditions'] = [c for c in omitted['conditions'] if c['key'] != 'duplicate.policy']
        assert validate_conditions(omitted, current, source_catalog(current)[0])['slots']['duplicate.policy']['value'] == policy
        # The answer, still attached to the original question, outranks a later extractor omission.
        later = deepcopy(current)
        later.update(condition_ledger=ledger)
        later['messages'].append(dict(role='user', content='파일 이름을 다시 확인할게.'))
        raw['conditions'][1].update(status='missing', value=None, evidence_ids=[])
        assert validate_conditions(raw, later, source_catalog(later)[0])['slots']['duplicate.policy']['value'] == policy
        later['messages'][-1]['content'] = '중복 처리는 첫 행만 남겨줘.'
        assert validate_conditions(raw, later, source_catalog(later)[0])['slots']['duplicate.policy']['value'] == 'first'
        later['messages'][-1]['content'] = '중복 처리는 나중에 정할게.'
        assert validate_conditions(raw, later, source_catalog(later)[0])['slots']['duplicate.policy']['value'] is None


def test_selected_recipient_reaches_send_arguments_and_save_validation(monkeypatch):
    import json
    from agent_builder.design.requirement_ledger import make_requirement_check, validate_plan
    from agent_builder.design.requirement_coverage import coverage_report
    from agent_builder.design.request_meaning import extract_meaning, make_meaning_node
    from tests.design.test_request_meaning import Extractor, meaning
    from tests.workflow_engine.test_workflow import node
    request = 'Gmail 제목은 “저장 작업”, 본문은 정확히 “삭제라는 단어는 예시다.”로 이메일을 보내줘. 수신자는 내가 다음에 정할게.'
    answer = 'night-shift@example.invalid 로 보내줘.'
    original = state(request)
    raw = meaning(count=None, evidence=None, actions=['send'], other_requirements=[
        '제목: 저장 작업', '본문: 삭제라는 단어는 예시다.', '수신자: 사용자가 나중에 결정할 것임'])
    raw['tasks'][0]['selection']['order'] = 'unspecified'
    sealed = extract_meaning(original, Extractor(raw))
    current = answered(monkeypatch, request, '수신자 주소에 대해 알려주세요.', answer)
    current.update(sealed)
    current.update(make_meaning_node('local', transport=Extractor(RuntimeError('unexpected extraction')))(current))
    from agent_builder.capabilities.capability_judgement import assess_capability
    current['capability_assessment'] = assess_capability(current, None)
    assert current['capability_assessment']['tool_ids'] == ['email.send']
    check = make_requirement_check('local', transport=Extractor(RuntimeError('unexpected extraction')), mode='enforce')
    update = check(current)
    assert update['requirement_route'] == 'plan'
    current.update(update)
    ledger = current['requirement_ledger']
    address = 'night-shift@example.invalid'
    assert ledger['unresolved'] == []
    assert [t['spec'] for t in ledger['tasks']] == [
        dict(kind='email_send', to=address, subject='저장 작업', body='삭제라는 단어는 예시다.')]
    args = dict(to=[address], subject='저장 작업', body='삭제라는 단어는 예시다.')
    from contracts.models import AgentDraft
    from workflow_engine.catalog.canonical import operation_variants
    from agent_builder.design.requirement_generation import constrain_request_settings
    from agent_builder.design.operation_steps import schema_for
    schema = AgentDraft.model_json_schema()
    with monkeypatch.context() as local:
        local.setenv('A4B_MAIN_PROVIDER', 'ollama')
        variants = operation_variants(schema['$defs']['WorkflowNode'], schema['$defs']['Binding'],
                                      {'email.prepare','email.send','finish'}, [])
    schema['$defs']['WorkflowNode'] = {'anyOf':variants}
    wire = schema_for(constrain_request_settings(schema, current))
    first, = wire['$defs']['FirstBusinessStep']['anyOf']
    assert first['properties']['tool']['enum'] == ['email.send']
    properties = first['properties']['settings']['properties']['arguments_json']['properties']
    assert properties['subject']['enum'] == ['저장 작업'] and properties['body']['enum'] == ['삭제라는 단어는 예시다.']
    assert wire['$defs']['WorkflowDefinition']['properties']['next_steps']['maxItems'] == 0
    mail = node('send', 'email.send', operation='execute', arguments_json=json.dumps(args))
    draft = dict(workflow=dict(entry='send', nodes=[mail], edges=[]))
    assert coverage_report(current, draft)['issues'] == []
    extra = deepcopy(mail)
    extra['id'] = 'extra'
    extra['settings']['arguments_json'] = json.dumps({**args, 'to':['second@example.invalid']})
    two_sends = dict(workflow=dict(entry='send', nodes=[mail,extra],
                                   edges=[dict(source='send', target='extra', branch='next')]))
    assert coverage_report(current, two_sends)['issues']
    from agent_builder.design.nodes.conversation import make_conversation
    from tests.design.test_deep_agent_conversation import draft as model_draft
    class Planner:
        def invoke(self, payload, config):
            context = json.loads(payload['messages'][0]['content'].split('\n', 1)[1])
            specs = context['required_execution_contracts']['specs']
            assert specs == [ledger['tasks'][0]['spec']]
            spec = specs[0]
            config = dict(to=[spec['to']], subject=spec['subject'], body=spec['body'])
            generated = node('mail','email.send', operation='execute', arguments_json=json.dumps(config))
            return {'structured_response': model_draft(workflow=dict(entry='mail', nodes=[generated], edges=[]))}
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', lambda **kwargs: Planner())
    planned = make_conversation(None, 'test', model_factory=lambda:object())(current)
    assert coverage_report(current, planned['draft'])['issues'] == []
    for override in [dict(to=['different@example.invalid']), dict(cc=[address]), dict(body='other')]:
        mail['settings']['arguments_json'] = json.dumps({**args, **override})
        assert coverage_report(current, draft)['issues']
    current['messages'].append(dict(role='user', content='이번에는 보내지 마.'))
    with pytest.raises(ValueError):
        validate_plan(ledger, current)


def test_gmail_quantity_revision_survives_general_planner_and_is_checked_again_on_save():
    import json
    from agent_builder.conditions.condition_check import audit_conditions
    from agent_builder.design.gmail_summary import gmail_summary_reply
    from agent_builder.design.requirement_coverage import coverage_report
    from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
    from agent_builder.design.temporal_context import ensure_temporal_context
    current = state('로스앤젤레스 현지 기준 어제 받은 Gmail을 요약해줘.')
    current['user_timezone'] = 'America/Los_Angeles'
    current.update(ensure_temporal_context(current, now='2028-01-01T01:00:00Z'))
    current['messages'].append(dict(role='user', content='그 목록에서 최신 두 통만 남겨서 다시 요약해줘.'))
    current.update(ensure_temporal_context(current, now='2028-01-02T19:00:00Z'))
    current['capability_assessment'] = dict(decision='existing_tools', tool_ids=['email.list','compose_answer'])
    raw = dict(tasks=[dict(source_message=1, description='메일 요약', spec=dict(kind='document_analysis'))], unresolved=[])
    ledger = extract_plan(raw, current)
    assert ledger['tasks'][0]['spec']['gmail_limit'] == 2
    assert ledger['tasks'][0]['spec']['gmail_limit_source_message'] == 1
    retold = deepcopy(raw)
    retold['tasks'][0]['source_message'] = 0
    assert extract_plan(retold, current)['tasks'][0]['spec']['gmail_limit'] == 2
    current.update(requirement_required=True, requirement_ledger=ledger, requirement_digest=plan_digest(ledger),
                   requirement_fingerprint=ledger_fingerprint(current))
    draft = gmail_summary_reply(state('Gmail에서 최근 100개 메일을 요약해줘'))['draft']
    reader, summary = draft['workflow']['nodes']
    args = dict(max_results=100, query='after:1830153600 before:1830240000')
    reader['settings']['arguments_json'] = json.dumps(args)
    summary['settings']['instructions'] = current['messages'][-1]['content']
    current['draft'] = draft
    assert coverage_report(current, draft)['issues']
    corrected = audit_conditions(current)['draft']
    assert json.loads(corrected['workflow']['nodes'][0]['settings']['arguments_json']) == {**args, 'max_results':2}
    assert not coverage_report(current, corrected)['issues']
    current['messages'][-1]['content'] = '요약할 때는 받은 편지 중 최근 세 통만 뽑아 줘.'
    assert extract_plan(raw,current)['tasks'][0]['spec']['gmail_limit'] == 3
    for reply in ['제목이 “최신 두 통만”인 메일을 요약해줘.', '최신 두 통만 빼고 요약해줘.',
                  '최신 두 통만 남기지 말고 전부 요약해줘.', '요약문을 두 줄로 정리해줘.']:
        current['messages'][-1]['content'] = reply
        assert not extract_plan(raw,current)['tasks'][0]['spec'].get('gmail_limit')


def test_count_only_edit_reuses_reviewed_period_and_does_not_hide_additional_work():
    from agent_builder.design.request_meaning import make_meaning_node, sealed_meaning
    from agent_builder.design.gmail_summary import gmail_summary_reply
    from agent_builder.design.temporal_context import ensure_temporal_context
    from tests.design.test_request_meaning import Extractor, interpreted, meaning, mail_arguments
    original, _ = interpreted('Gmail에서 어제 받은 메일을 요약해줘.',
                              meaning(count=None, evidence=None, period='yesterday'))
    initial = mail_arguments(gmail_summary_reply(original))
    original['capability_assessment'] = dict(decision='existing_tools',tool_ids=['email.list','compose_answer'])
    for reply in ['그 목록에서 최신 두 통만 남겨서 다시 요약해줘.', '조회 한도를 2건으로 변경해줘']:
        current = deepcopy(original)
        current['messages'].append(dict(role='user',content=reply))
        current.update(ensure_temporal_context(current, now='2028-02-03T00:00:00Z'))
        fake = Extractor(RuntimeError('unnecessary re-extraction'))
        current.update(make_meaning_node('test', transport=fake)(current))
        assert not fake.messages
        assert sealed_meaning(current)['tasks'][0]['selection']['period_source_message'] == 0
        assert mail_arguments(gmail_summary_reply(current)) == {**initial,'max_results':2}
    for reply in ['최근 두 통만 골라서 요약하고 팀에도 발송해줘.', '그 목록에서 최신 두 통만 남겨서 중요 표시 메일만 요약해줘.']:
        current = deepcopy(original)
        current['messages'].append(dict(role='user',content=reply))
        current.update(ensure_temporal_context(current, now='2028-02-03T00:00:00Z'))
        fake = Extractor(RuntimeError('ordinary planner remains in charge'))
        current.update(make_meaning_node('test', transport=fake)(current))
        assert fake.messages
        assert sealed_meaning(current) is None
