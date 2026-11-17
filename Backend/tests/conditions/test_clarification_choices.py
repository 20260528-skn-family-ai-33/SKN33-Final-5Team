"""Clickable choices for closed-vocabulary clarification questions."""
from copy import deepcopy
import pytest
from agent_builder.conditions.condition_readiness import CHOICES, ENUMS, judge_conditions, source_catalog, validate_conditions
from agent_builder.conditions.input_resolution import option_answer, value_answer
from agent_builder.capabilities.capability_check import make_capability_check
from agent_builder.capabilities.capability_gate import input_fingerprint
from tests.conditions.test_condition_readiness import state, Fake

TABLE = {'id': 'f1', 'kind': 'table', 'name': '거래.csv', 'columns': ['거래ID', '비용', '상태'],
         'profile': {'비용': {'types': ['string'], 'missing': 1, 'duplicateValues': 0, 'numericStrings': 5}}}


def judge(s, values):
    """Ground each specified value in the user turn that actually said it."""
    catalog, _ = source_catalog(s)
    def evidence(needle):
        return [e['id'] for e in catalog if e['source_id'] == 'user' and needle in e['quote']][-1:]
    conditions = [{'key': k, 'value': v, 'status': 'missing' if v is None else 'specified',
                   'evidence_ids': evidence(needle) if v is not None else []} for k, (v, needle) in values.items()]
    return validate_conditions({'operations': ['filter'], 'conditions': conditions, 'unsupported_actions': []}, s, catalog)


def vague(*, legacy=False):
    s = state('비용 큰 것들만 골라서 파일로 만들어줘.'); s['resources'] = [TABLE]
    ledger = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': (None, ''), 'filter.value': (None, ''),
                       'output.format': (None, '')})
    if legacy:
        ledger['questions'].insert(0, {'id': 'legacy-operator', 'key': 'filter.operator',
                                       'text': '비교 방식', 'options': list(CHOICES['filter.operator'])})
    s.update(condition_ledger=ledger, pending_questions=ledger['questions'], condition_enforced=True)
    return s, ledger


def click(s, question, label):
    s = deepcopy(s)
    s['messages'] += [{'role': 'assistant', 'content': '확인 질문'}, {'role': 'user', 'content': label}]
    s['condition_answers'] = [{'question_ids': [question['id']], 'candidate_keys': [question['key']],
                               'answer': label, 'turn': len(s['messages']) - 1}]
    return s


def test_closed_vocabulary_questions_offer_exact_choices_and_no_invented_threshold():
    assert all(set(CHOICES[k].values()) <= ENUMS[k] for k in CHOICES)
    _, ledger = vague()
    options = {q['key']: q['options'] for q in ledger['questions']}
    assert 'filter.operator' not in options
    question = next(q for q in ledger['questions'] if q['key'] == 'filter.value')
    assert question['response'] == {'type': 'text'}
    assert '초과인지 이상인지 함께' in question['text']
    assert options['filter.value'] == []  # a suggested threshold would invent the criterion
    assert all('권장' not in o for opts in options.values() for o in opts)


def test_clicked_choice_is_confirmed_without_a_model_call_and_survives_revalidation():
    s, ledger = vague(legacy=True)
    question = next(q for q in ledger['questions'] if q['key'] == 'filter.operator')
    s = click(s, question, '초과')
    transport = Fake([])
    result = judge_conditions(s, transport)
    assert transport.calls == 0 and result['model_calls'] == 0
    slot = result['ledger']['slots']['filter.operator']
    assert slot['status'] == 'specified' and slot['value'] == 'gt' and slot['evidence'][0]['quote'] == '초과'
    assert result['ledger']['slots']['filter.field']['value'] == '비용'  # earlier facts are kept
    # Next turn: the stored choice is re-grounded from its own evidence.
    s2 = {**s, 'condition_ledger': result['ledger'], 'pending_questions': result['ledger']['questions']}
    question = next(q for q in result['ledger']['questions'] if q['key'] == 'output.format')
    after = judge_conditions(click(s2, question, 'CSV'), Fake([]))['ledger']['slots']
    assert after['filter.operator']['value'] == 'gt' and after['output.format']['value'] == 'csv'


@pytest.mark.parametrize('key,label,value', [('missing.policy', '빈 값 제외', 'exclude'),
                                             ('output.format', 'JSON', 'json')])
def test_each_choice_maps_to_one_value(key, label, value):
    s, ledger = vague()
    if key not in {q['key'] for q in ledger['questions']}:
        s['pending_questions'] = [{'id': 'q-' + key, 'key': key, 'text': '?', 'options': list(CHOICES[key])}]
        ledger = {**ledger, 'questions': s['pending_questions']}
    question = next(q for q in ledger['questions'] if q['key'] == key)
    slots = judge_conditions(click(s, question, label), Fake([]))['ledger']['slots']
    assert slots[key]['status'] == 'specified' and slots[key]['value'] == value


def test_free_text_wrong_question_and_shared_labels_are_not_choices():
    s, ledger = vague(legacy=True)
    operator = next(q for q in ledger['questions'] if q['key'] == 'filter.operator')
    value = next(q for q in ledger['questions'] if q['key'] == 'filter.value')
    assert option_answer(click(s, operator, '100만원 초과로 해줘')) is None  # free text keeps the model path
    assert option_answer(click(s, value, '초과')) is None  # label of another question
    shared = deepcopy(s)
    shared['pending_questions'] = [{'id': 'a', 'key': 'filter.field', 'options': ['비용', '상태']},
                                   {'id': 'b', 'key': 'duplicate.field', 'options': ['거래ID', '비용']}]
    shared['messages'] += [{'role': 'user', 'content': '비용'}]
    assert option_answer(shared) is None  # ambiguous without a question ID


def test_unknown_answer_still_uses_the_existing_non_answer_path():
    s, ledger = vague(legacy=True)
    operator = next(q for q in ledger['questions'] if q['key'] == 'filter.operator')
    s = click(s, operator, '모르겠어')
    assert option_answer(s) is None
    result = judge_conditions(s, Fake([]))
    assert result['model_calls'] == 0 and result['ledger']['slots']['filter.operator']['status'] == 'missing'


def test_capability_judgement_is_reused_for_a_clicked_choice_only_with_unchanged_input():
    s, ledger = vague(legacy=True)
    previous = {'decision': 'existing_tools', 'tool_ids': ['read_table', 'filter_sort', 'write_result'],
                'missing_conditions': [], 'missing_actions': [], 'reason': '지원',
                'user_quotes': [s['messages'][0]['content']]}
    s.update(capability_assessment=previous, capability_fingerprint=input_fingerprint(s), capability_enforced=True)
    operator = next(q for q in ledger['questions'] if q['key'] == 'filter.operator')
    transport = Fake([])
    result = make_capability_check('qwen3.5:4b', transport, 'enforce')(click(s, operator, '초과'))
    assert transport.calls == 0 and result['capability_assessment']['answer_resolution'] == 'condition_choice'
    changed = click(s, operator, '초과'); changed['resources'] = [{**TABLE, 'columns': ['거래ID', '금액']}]
    blocked = make_capability_check('qwen3.5:4b', Fake([]), 'enforce')(changed)
    # Changed resources are never reused: the version check stops for re-validation first.
    assert 'answer_resolution' not in blocked['capability_assessment']
    assert blocked['capability_assessment']['decision'] == 'system_error'


def answer_value(s, ledger, text):
    question = next(q for q in ledger['questions'] if q['key'] == 'filter.value')
    return click(s, question, text)


@pytest.mark.parametrize('text,value', [
    ('100만원', 1000000), ('1,000,000', 1000000), ('1000000원', 1000000), ('85점', 85), ('85', 85),
    ('100만', 1000000), ('1.5만원', 15000), ('0', 0), ('100 만원', 1000000),
    ('100만원으로', 1000000), ('1백만원', 1000000)])
def test_plain_amount_answer_is_read_exactly(text, value):
    s, ledger = vague()
    found = value_answer(answer_value(s, ledger, text))
    assert found and float(found['value']) == value


@pytest.mark.parametrize('text', [
    '백만원', '삼십만원', '1억', '1억 2천만원', '-5', '50%', '100k', '$100', '100만원에서 200만원',
    '2026-11-02', '100만원 초과', '100만원 이상으로 바꿔줘', '1,00', '약 100만원', '100만원 정도'])
def test_ambiguous_amounts_keep_the_model_path(text):
    """The raw parser reads 1억 as 1 and -5 as 5; such answers must never be parsed here."""
    s, ledger = vague()
    assert value_answer(answer_value(s, ledger, text)) is None


def test_amount_for_a_text_column_keeps_the_model_path():
    s, ledger = vague()
    ledger['slots']['filter.field'] = {**ledger['slots']['filter.field'], 'value': '상태'}
    s['condition_ledger'] = ledger
    assert value_answer(answer_value(s, ledger, '100')) is None


def test_typed_amount_is_confirmed_without_a_model_call_and_reused_by_capability():
    s, ledger = vague()
    transport = Fake([])
    result = judge_conditions(answer_value(s, ledger, '100만원'), transport)
    slot = result['ledger']['slots']['filter.value']
    assert transport.calls == 0 and result['model_calls'] == 0
    assert slot['status'] == 'specified' and slot['value'] == 1000000.0 and slot['evidence'][0]['quote'] == '100만원'
    previous = {'decision': 'existing_tools', 'tool_ids': ['read_table', 'filter_sort'], 'missing_conditions': [],
                'missing_actions': [], 'reason': '지원', 'user_quotes': [s['messages'][0]['content']]}
    s.update(capability_assessment=previous, capability_fingerprint=input_fingerprint(s), capability_enforced=True)
    capability = Fake([])
    reused = make_capability_check('qwen3.5:4b', capability, 'enforce')(answer_value(s, ledger, '100만원'))
    assert capability.calls == 0 and reused['capability_assessment']['answer_resolution'] == 'condition_choice'


def test_grouped_filter_answer_confirms_both_operator_and_value():
    from agent_builder.design.question_answers import question_answers

    s, ledger = vague()
    question = next(q for q in ledger['questions'] if q['key'] == 'filter.value')
    answer = '100만원 미만'
    assert question_answers([{'question_id': question['id'], 'value': answer}], s)[0]['value'] == answer

    message = f"{question['text']} → {answer}"
    answered = click(s, question, message)
    event = answered['condition_answers'][0]
    event['answers'] = [{'question_id': question['id'], 'key': question['key'], 'value': answer, 'answer': answer}]
    outcome = judge_conditions(answered, Fake([]))
    assert outcome['model_calls'] == 0
    result = outcome['ledger']
    assert result['slots']['filter.operator']['value'] == 'lt'
    assert result['slots']['filter.value']['value'] == 1000000
    assert not any(q['key'].startswith('filter.') for q in result['questions'])
    next_question = next(q for q in result['questions'] if q['key'] == 'output.format')
    next_turn = {**answered, 'condition_ledger': result, 'pending_questions': result['questions']}
    final = judge_conditions(click(next_turn, next_question, 'CSV'), Fake([]))['ledger']
    assert final['slots']['filter.operator']['value'] == 'lt'
    assert final['slots']['filter.value']['value'] == 1000000
    assert final['slots']['output.format']['value'] == 'csv'
    assert {q['key'] for q in final['questions']} == {'missing.policy'}


@pytest.mark.parametrize(('comparison', 'expected'), [
    ('10원보다 적은', 10), ('10만원보다 적은', 100000), ('100000원 미만인', 100000),
])
def test_explicit_less_than_in_initial_request_does_not_ask_for_filter(comparison, expected):
    s = state(f'비용이 {comparison} 거래를 보여줘.')
    s['resources'] = [TABLE]
    result = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': (None, ''), 'filter.value': (None, '')})
    assert result['slots']['filter.operator']['value'] == 'lt'
    assert result['slots']['filter.value']['value'] == expected
    assert not any(q['key'].startswith('filter.') for q in result['questions'])


def test_threshold_of_another_column_does_not_fill_missing_filter_value():
    s = state('비용이 낮고 수량이 10개보다 적은 거래를 보여줘.')
    s['resources'] = [{**TABLE, 'columns': [*TABLE['columns'], '수량']}]
    result = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': (None, ''), 'filter.value': (None, '')})
    assert result['slots']['filter.value']['status'] == 'missing'
    assert result['decision'] != 'ready'


@pytest.mark.parametrize(('request_text', 'mode', 'expected'), [
    ('비용이 10원 이상이고 수량이 3개 이상인 행', 'all', [(15, 4)]),
    ('비용이 10원보다 적거나 수량이 3개보다 적은 행', 'any', [(15, 1), (5, 4)]),
])
def test_partial_model_output_does_not_complete_compound_filter(request_text, mode, expected):
    from agent_builder.conditions.condition_readiness import literal_compound_filter
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    from tests.conditions.test_condition_readiness import workflow_draft
    from workflow_engine.executors.legacy_tools.filter_sort import filter_sort_node

    s = state(request_text + '을 JSON으로 저장해줘.')
    s['resources'] = [{**TABLE, 'columns': [*TABLE['columns'], '수량']}]
    result = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': (None, ''),
                       'filter.value': (None, ''), 'output.format': ('json', 'JSON')})
    assert result['decision'] != 'ready'

    match, rules = literal_compound_filter(s)
    assert match == mode and len(rules) == 2
    draft = workflow_draft(s)
    plan = draft['workflow']['nodes'][1]['settings']['execution_plan']
    plan.update(filters=rules[:1], match=match)
    assert numeric_filter_issues(s, draft)
    plan.update(filters=rules, match='all' if match == 'any' else 'any')
    assert numeric_filter_issues(s, draft)
    plan.update(match=match)
    assert not numeric_filter_issues(s, draft)
    ctx = {'rows': [{'비용': cost, '수량': count} for cost, count in [(15, 1), (15, 4), (5, 4)]],
           'columns': ['비용', '수량'], 'file_id': 'table', 'trace': []}
    filter_sort_node(ctx, rules, [], match=match)
    rows = ctx['rows']
    assert [(row['비용'], row['수량']) for row in rows] == expected


@pytest.mark.parametrize('text', [
    '비용이 10원보다 적은 경우가 아니라 큰 경우만 남겨줘',
    '비용이 10원보다 높은지 낮은지는 아직 정하지 않았어',
])
def test_uncertain_natural_comparison_keeps_amount_and_asks_direction(text):
    s = state(text)
    s['resources'] = [TABLE]
    result = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': (None, ''), 'filter.value': (None, '')})
    assert result['slots']['filter.value']['value'] == 10
    assert result['slots']['filter.operator']['status'] == 'missing'
    assert [q['key'] for q in result['questions'] if q['key'].startswith('filter.')] == ['filter.operator']


def test_prefix_column_does_not_narrow_operator_choices():
    s = state('총비용이 큰 건 중 비용 기준은 5원이야')
    s['resources'] = [{**TABLE, 'columns': [*TABLE['columns'], '총비용']}]
    result = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': (None, ''), 'filter.value': ('5', '5원')})
    question = next(q for q in result['questions'] if q['key'] == 'filter.operator')
    assert question['options'] == ['초과', '이상', '미만', '이하']


def test_followup_amount_does_not_inherit_another_columns_operator():
    s = state('비용이 낮고 수량이 10개보다 많은 행을 남겨줘')
    s['resources'] = [{**TABLE, 'columns': [*TABLE['columns'], '수량']}]
    ledger = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': (None, ''), 'filter.value': (None, '')})
    s.update(condition_ledger=ledger, pending_questions=ledger['questions'], condition_enforced=True)
    question = next(q for q in ledger['questions'] if q['key'] == 'filter.value')
    result = judge_conditions(click(s, question, '5'), Fake([]))['ledger']
    assert result['slots']['filter.value']['value'] == 5
    assert result['slots']['filter.operator']['status'] == 'missing'
    assert result['decision'] != 'ready'


def test_compound_filter_survives_output_reply_and_explicit_edits():
    from agent_builder.conditions.condition_readiness import literal_compound_filter
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    from tests.conditions.test_condition_readiness import workflow_draft
    from tests.workflow_engine.test_workflow import node, bind, edge

    s = state('비용이 10원 이상이고 수량이 3개 이상인 행을 보여줘')
    s['resources'] = [{**TABLE, 'columns': ['비용', '수량']}]

    def reply(text):
        s['messages'].append({'role': 'user', 'content': text})

    reply('JSON으로 보여줘')
    mode, rules = literal_compound_filter(s)
    draft = workflow_draft(s)
    flow = draft['workflow']
    flow['nodes'][-1] = node('out', 'finish', [bind('table', 'filter', 'table')])
    flow['edges'][-1] = edge('filter', 'out')
    plan = flow['nodes'][1]['settings']['execution_plan']
    plan.update(filters=rules[:1], match=mode)
    assert numeric_filter_issues(s, draft)
    plan.update(filters=rules)
    assert not numeric_filter_issues(s, draft)

    reply('비용이 20원 이상으로 바꿔줘')
    assert [(r['field'], r['value']) for r in literal_compound_filter(s)[1]] == [('비용', 20), ('수량', 3)]
    assert numeric_filter_issues(s, draft)
    reply('수량 조건 취소해줘')
    assert [(r['field'], r['value']) for r in literal_compound_filter(s)[1]] == [('비용', 20)]
    reply('전체 필터 제거해줘')
    assert literal_compound_filter(s) is None


# --- Compound filters: every stated condition is applied, or the user is asked. ---

COMPOUND_TABLE = {**TABLE, 'columns': ['비용', '수량', '할인'], 'profile': {}}
ROWS = [(15, 1), (15, 4), (5, 4)]
PARTIAL = {'filter.field': ('비용', '비용'), 'filter.operator': (None, ''), 'filter.value': (None, '')}


def compound_state(*turns):
    s = state(turns[0]); s['resources'] = [COMPOUND_TABLE]
    for turn in turns[1:]:
        s['messages'] += [{'role': 'assistant', 'content': '확인 질문'}, {'role': 'user', 'content': turn}]
    return s


def filter_draft(s, rules, match):
    from tests.conditions.test_condition_readiness import workflow_draft
    draft = workflow_draft(s)
    draft['workflow']['nodes'][1]['settings']['execution_plan'].update(filters=rules, match=match)
    return draft


def kept_rows(rules, match):
    from workflow_engine.executors.legacy_tools.filter_sort import filter_sort_node
    ctx = {'rows': [{'비용': cost, '수량': count} for cost, count in ROWS],
           'columns': ['비용', '수량'], 'file_id': 'table', 'trace': []}
    filter_sort_node(ctx, rules, [], match=match)
    return [(row['비용'], row['수량']) for row in ctx['rows']]


@pytest.mark.parametrize(('request_text', 'mode', 'expected'), [
    ('비용이 10원 이상이면서 수량이 3개 이상인 행', 'all', [(15, 4)]),
    ('비용이 10원 이상인 행 그리고 수량이 3개 이상인 행', 'all', [(15, 4)]),
    ('비용이 10원 이상이고 수량도 3개 이상인 행', 'all', [(15, 4)]),
    ('비용이 10원보다 적은 행 또는 수량이 3개보다 적은 행', 'any', [(15, 1), (5, 4)]),
    ('비용이 10원 미만인 거래 혹은 수량이 3개 미만인 거래', 'any', [(15, 1), (5, 4)]),
])
def test_compound_filter_is_read_through_the_noun_between_its_conditions(request_text, mode, expected):
    from agent_builder.conditions.condition_readiness import literal_compound_filter
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues

    s = compound_state(request_text + '을 JSON으로 저장해줘.')
    found, rules = literal_compound_filter(s)
    assert found == mode and [rule['field'] for rule in rules] == ['비용', '수량']
    assert numeric_filter_issues(s, filter_draft(s, rules[:1], 'all'))
    assert numeric_filter_issues(s, filter_draft(s, rules, 'all' if mode == 'any' else 'any'))
    assert not numeric_filter_issues(s, filter_draft(s, rules, mode))
    assert kept_rows(rules, mode) == expected


@pytest.mark.parametrize('request_text', [
    '비용은 10원 이상, 수량은 3개 이상인 행',
    '비용이 10원 이상인 거래들 중 수량이 3개 이상인 것',
])
def test_unstated_relation_between_conditions_is_asked_and_never_ready(request_text):
    from agent_builder.conditions.clarification_contract import Question
    from agent_builder.conditions.condition_readiness import MATCH_CHOICES, compound_filter_state, literal_compound_filter
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues

    s = compound_state(request_text + '을 JSON으로 저장해줘.')
    ledger = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': ('gte', '이상'),
                       'filter.value': ('10', '10원'), 'output.format': ('json', 'JSON')})
    assert ledger['decision'] == 'clarify' and 'filter.match' in ledger['unresolved']
    found = compound_filter_state(s)
    assert found['pending'] == 'match' and literal_compound_filter(s) is None
    # A draft that drops a condition is stopped even before the answer arrives.
    assert numeric_filter_issues(s, filter_draft(s, found['rules'][:1], 'all'))
    assert not numeric_filter_issues(s, filter_draft(s, found['rules'], 'any'))

    s.update(condition_ledger=ledger, pending_questions=ledger['questions'], condition_enforced=True)
    others = [q for q in ledger['questions'] if q['key'] != 'filter.match']
    for question in others:  # at most two questions are shown at a time
        s = click(s, question, {'missing.policy': '빈 값이면 오류'}.get(question['key'], 'JSON'))
        ledger = judge_conditions(s, Fake([]))['ledger']
        s.update(condition_ledger=ledger, pending_questions=ledger['questions'])
    question = next(q for q in ledger['questions'] if q['key'] == 'filter.match')
    Question.model_validate(question)
    assert question['options'] == list(MATCH_CHOICES) and '비용 10 이상' in question['text'] and '수량 3 이상' in question['text']
    for label, mode in MATCH_CHOICES.items():
        answered = click(s, question, label)
        assert literal_compound_filter(answered)[0] == mode
        assert 'filter.match' not in judge_conditions(answered, Fake([]))['ledger']['unresolved']
        rules = literal_compound_filter(answered)[1]
        assert numeric_filter_issues(answered, filter_draft(answered, rules[:1], 'all'))
        assert not numeric_filter_issues(answered, filter_draft(answered, rules, mode))


@pytest.mark.parametrize('request_text', [
    '비용이 10원 이상이고 수량이 3개 이상이 아닌 행',
    '비용이 10원보다 큰 게 아니고 수량이 3개 이상인 행',
    '비용이 10원 이상이고 수량이 3개 이상이거나 할인이 5원 이하인 행',
])
def test_denied_or_mixed_compound_filter_asks_for_the_conditions_again(request_text):
    from agent_builder.conditions.condition_readiness import compound_filter_state, literal_compound_filter
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues

    s = compound_state(request_text + '을 JSON으로 저장해줘.')
    assert compound_filter_state(s)['pending'] == 'rewrite'
    ledger = judge(s, {**PARTIAL, 'output.format': ('json', 'JSON')})
    assert ledger['decision'] == 'clarify' and 'filter.match' in ledger['unresolved']
    from agent_builder.conditions.clarification_contract import Question
    from agent_builder.design.question_controls import project_question_controls
    s.update(condition_ledger=ledger, pending_questions=ledger['questions'], condition_enforced=True)
    from agent_builder.conditions.clarification_contract import _contract_questions
    for exported in _contract_questions(ledger):
        Question.model_validate(exported)
    for shown in project_question_controls(s)['pending_questions']:
        if shown['key'] == 'filter.match':  # written out again, never picked from guessed labels
            assert shown['options'] == [] and shown['response'] == {'type': 'text'}
    # Nothing unread is enforced: the opposite of what the user wrote is not required of a draft.
    wrong = [dict(field='수량', operator='lt', value=3.0, compare_field=None, value_type='number')]
    assert not numeric_filter_issues(s, filter_draft(s, wrong, 'all'))
    rewritten = compound_state(request_text, '비용이 10원 이상이고 수량이 3개 미만인 행')
    mode, rules = literal_compound_filter(rewritten)
    assert mode == 'all' and [(r['field'], r['operator'], r['value']) for r in rules] == [('비용', 'gte', 10), ('수량', 'lt', 3)]


@pytest.mark.parametrize(('text', 'operator'), [
    ('비용이 10원보다 큰 게 아닌 거래를 보여줘.', None),
    ('비용이 10원 이상이 아닌 거래를 보여줘.', None),
    ('비용이 10원 이상인 거래 중 취소되지 않은 것을 보여줘.', 'gte'),
    ('비용이 10원보다 큰 거래를 보여줘.', 'gt'),
])
def test_denial_right_after_a_comparison_is_not_confirmed_as_that_comparison(text, operator):
    s = state(text); s['resources'] = [TABLE]
    result = judge(s, PARTIAL)
    slot = result['slots']['filter.operator']
    assert slot.get('value') == operator and (slot['status'] == 'specified') == bool(operator)
    assert result['slots']['filter.value']['value'] == 10
    assert bool(operator) != any(q['key'] == 'filter.operator' for q in result['questions'])


BASE = '비용이 10원 이상이고 수량이 3개 이상인 행을 JSON으로 저장해줘'


def test_later_single_condition_asks_what_happens_to_the_rest():
    from agent_builder.conditions.condition_readiness import KEEP_OTHERS, ONLY_NEW, compound_filter_state, literal_compound_filter
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues

    s = compound_state(BASE, '아니야, 그냥 비용이 50원 이상인 행만 저장해줘')
    found = compound_filter_state(s)
    assert found['pending'] == 'revise' and found['candidate']['value'] == 50
    ledger = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': ('gte', '이상'),
                       'filter.value': ('50', '50원'), 'output.format': ('json', 'JSON')})
    question = next(q for q in ledger['questions'] if q['key'] == 'filter.match')
    assert question['options'] == [KEEP_OTHERS, ONLY_NEW] and '수량 3 이상' in question['text']
    only_cost = [dict(field='비용', operator='gte', value=50.0, compare_field=None, value_type='number')]
    assert not numeric_filter_issues(s, filter_draft(s, only_cost, 'all'))  # not blocked on the old filter

    dropped = compound_state(BASE, '아니야, 그냥 비용이 50원 이상인 행만 저장해줘', ONLY_NEW)
    assert compound_filter_state(dropped)['rules'] == [] and not numeric_filter_issues(dropped, filter_draft(dropped, only_cost, 'all'))
    kept = compound_state(BASE, '아니야, 그냥 비용이 50원 이상인 행만 저장해줘', KEEP_OTHERS)
    assert [(r['field'], r['value']) for r in literal_compound_filter(kept)[1]] == [('비용', 50), ('수량', 3)]
    assert numeric_filter_issues(kept, filter_draft(kept, only_cost, 'all'))


def test_unclear_change_to_a_condition_offers_the_current_conditions():
    from agent_builder.conditions.condition_readiness import KEEP_ALL, compound_filter_state, literal_compound_filter

    s = compound_state(BASE, '수량 조건은 빼줘')
    found = compound_filter_state(s)
    assert found['pending'] == 'revise' and found['candidate'] is None
    ledger = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': ('gte', '이상'),
                       'filter.value': ('10', '10원'), 'output.format': ('json', 'JSON')})
    question = next(q for q in ledger['questions'] if q['key'] == 'filter.match')
    assert question['options'] == [KEEP_ALL, '비용 조건 제거', '수량 조건 제거']
    removed = compound_state(BASE, '수량 조건은 빼줘', '수량 조건 제거')
    assert [r['field'] for r in literal_compound_filter(removed)[1]] == ['비용']
    assert len(literal_compound_filter(compound_state(BASE, '수량 조건은 빼줘', KEEP_ALL))[1]) == 2
    typed = compound_state(BASE, '비용 기준을 올려줘', '비용이 20원 이상')
    assert [(r['field'], r['value']) for r in literal_compound_filter(typed)[1]] == [('비용', 20), ('수량', 3)]
    # A reply to another question leaves the filter as it was.
    assert literal_compound_filter(compound_state(BASE, 'CSV로 바꿔줘'))[0] == 'all'


def test_branch_that_returns_no_rows_does_not_block_a_complete_compound_filter():
    from agent_builder.conditions.condition_readiness import literal_compound_filter
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    from tests.workflow_engine.test_workflow import node, edge

    s = compound_state(BASE)
    _, rules = literal_compound_filter(s)
    for filters, blocked in ((rules, False), (rules[:1], True)):
        draft = filter_draft(s, filters, 'all')
        draft['workflow']['nodes'].append(node('notice', 'finish', []))
        draft['workflow']['edges'].append(edge('read', 'notice'))
        assert bool(numeric_filter_issues(s, draft)) == blocked


def test_grouped_threshold_question_is_exported_in_the_published_question_shape():
    from agent_builder.conditions.clarification_contract import Question, _contract_questions

    _, ledger = vague()
    assert any('response' in question for question in ledger['questions'])
    for exported in _contract_questions(ledger):
        Question.model_validate(exported)


MESSAGE_LIMIT = 4000  # the longest design message the API accepts (Backend/api/src/agent-builder.ts)


@pytest.mark.parametrize('head', ['비용이 10', '비용이 10원보다', '비용', '비용이 10원 이상이고'])
def test_long_runs_of_spaces_are_read_once(head):
    """A message of spaces after a number or column finishes like any other message.

    Before the whitespace runs were taken whole, each optional piece between them
    could claim any share of the run and this input took minutes to reject.
    """
    from agent_builder.conditions.condition_readiness import compound_filter_state
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    from tests.conditions.test_condition_readiness import workflow_draft

    draft = workflow_draft(state('비용이 10원 이상인 행'))
    text = (head + ' ' * MESSAGE_LIMIT)[:MESSAGE_LIMIT]
    s = state(text); s['resources'] = [COMPOUND_TABLE]
    assert compound_filter_state(s)['rules'] == []
    assert judge(s, PARTIAL)['decision'] != 'ready'
    numeric_filter_issues(s, draft)


SAVE_TABLE = {**TABLE, 'columns': ['거래ID', '비용', '수량', '날짜'],
              'profile': {column: {'types': ['number'], 'missing': 0, 'duplicateValues': 0, 'numericStrings': 3}
                          for column in ('비용', '수량')}}


def save_ready_state(request_text, first, rules, match):
    from tests.conditions.test_condition_readiness import workflow_draft

    s = state(request_text + '을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'); s['resources'] = [SAVE_TABLE]
    operator, word = first
    ledger = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': (operator, word),
                       'filter.value': ('10', '10원'), 'output.format': ('json', 'JSON')})
    assert ledger['decision'] == 'ready'
    draft = workflow_draft(s, threshold=10, operator=operator)
    plan = draft['workflow']['nodes'][1]['settings']['execution_plan']
    plan.update(filters=rules, match=match)
    draft['execution_plan'] = plan
    s.update(condition_enforced=True, condition_ledger=ledger, confirmed=True, draft=draft)
    return s


@pytest.mark.parametrize(('request_text', 'first', 'mode', 'second'), [
    ('비용이 10원보다 적은 행 또는 수량이 3개보다 적은 행', ('lt', '적은'), 'any', ('수량', 'lt', 3)),
    ('비용이 10원 이상이면서 수량이 3개 이상인 행', ('gte', '이상'), 'all', ('수량', 'gte', 3)),
    ('비용이 10원 이상이고 수량이 3개 이상인 행', ('gte', '이상'), 'all', ('수량', 'gte', 3)),
])
def test_save_accepts_the_complete_compound_filter_and_nothing_less(request_text, first, mode, second):
    from agent_builder.capabilities.capability_gate import require_save_ready

    cost = dict(field='비용', operator=first[0], value=10.0, compare_field=None, value_type='number')
    other = dict(field=second[0], operator=second[1], value=float(second[2]), compare_field=None, value_type='number')
    require_save_ready(save_ready_state(request_text, first, [cost, other], mode))
    for rules, match in (([cost], 'all'), ([cost, other], 'all' if mode == 'any' else 'any')):
        with pytest.raises(ValueError, match='확정 업무 조건'):
            require_save_ready(save_ready_state(request_text, first, rules, match))


def confirmed_single(*follow_ups):
    s = state('비용이 10원 이상인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'); s['resources'] = [SAVE_TABLE]
    ledger = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': ('gte', '이상'),
                       'filter.value': ('10', '10원'), 'output.format': ('json', 'JSON')})
    assert ledger['decision'] == 'ready'
    s.update(condition_ledger=ledger, pending_questions=[], condition_enforced=True)
    for text in follow_ups:
        s['messages'] += [{'role': 'assistant', 'content': '확인 질문'}, {'role': 'user', 'content': text}]
    return s


@pytest.mark.parametrize('follow_up', ['아까 기준을 좀 높여줘', '기준 좀 낮춰 줘', '필터 조건 바꿔줘'])
def test_change_to_the_criterion_without_a_value_is_asked(follow_up):
    from agent_builder.conditions.condition_readiness import KEEP_ALL, compound_filter_state

    s = confirmed_single(follow_up)
    ledger = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': ('gte', '이상'),
                       'filter.value': ('10', '10원'), 'output.format': ('json', 'JSON')})
    assert ledger['decision'] == 'clarify' and 'filter.match' in ledger['unresolved']
    question = next(q for q in ledger['questions'] if q['key'] == 'filter.match')
    assert '비용 10 이상' in question['text'] and question['options'] == [KEEP_ALL]
    assert compound_filter_state(confirmed_single(follow_up, KEEP_ALL))['pending'] is None
    retyped = compound_filter_state(confirmed_single(follow_up, '비용이 20원 이상'))
    assert retyped['pending'] is None and [r['value'] for r in retyped['rules']] == [20]

    found = compound_filter_state(compound_state(BASE, follow_up))
    assert found['pending'] == 'revise' and len(found['rules']) == 2


@pytest.mark.parametrize('follow_up', ['JSON 말고 CSV로 저장해줘', '중복 기준은 거래ID로 해줘', '날짜 기준으로 정렬해줘'])
def test_other_follow_ups_do_not_reopen_the_criterion(follow_up):
    from agent_builder.conditions.condition_readiness import compound_filter_state

    assert compound_filter_state(confirmed_single(follow_up))['pending'] is None
    s = compound_state(BASE, follow_up); s['resources'] = [SAVE_TABLE]  # 날짜 is a column of this table
    assert compound_filter_state(s)['pending'] is None


@pytest.mark.parametrize('reply', ['아까 기준을 좀 높여줘', '기준을 20으로 바꿔줘'])
def test_unnamed_compound_edit_requires_clarification_before_save(reply):
    from agent_builder.conditions.condition_readiness import compound_filter_state, plan_issues
    values = {'filter.field': ('비용', '비용'), 'filter.operator': ('gte', '이상'),
              'filter.value': ('10', '10원'), 'output.format': ('json', 'JSON')}
    s = compound_state(BASE, reply)
    found = compound_filter_state(s)
    assert found['pending'] == 'revise'
    question = next(q for q in judge(s, values)['questions'] if q['key'] == 'filter.match')
    assert '비용 10 이상' in question['text'] and '수량 3 이상' in question['text']
    stale = judge(compound_state(BASE), values)
    assert plan_issues(stale, filter_draft(s, found['rules'], 'all'), state=s)
    s['messages'].append({'role': 'user', 'content': '비용이 20원 이상'})
    resolved = compound_filter_state(s)
    assert resolved['pending'] is None
    assert [(r['field'], r['value']) for r in resolved['rules']] == [('비용', 20), ('수량', 3)]


@pytest.mark.parametrize(('request_text', 'mode', 'expected'), [
    ('비용이 10원보다 적은 행 또는 수량이 3개보다 적은 행', 'any', [(15, 1), (5, 4)]),
    ('비용이 10원 이상이면서 수량이 3개 이상인 행', 'all', [(15, 4)]),
])
def test_compound_validation_save_and_execution_agree(tmp_path, request_text, mode, expected):
    from agent_builder.conditions.condition_readiness import compound_filter_state, plan_issues
    from agent_builder.design.nodes.validation import validate_node
    from infrastructure.persistence.storage import Storage
    from workflow_engine.runner import run_workflow
    s = compound_state(request_text + '을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    asset = s['resources'][0]
    asset.update(id='table', name='거래.csv', format='csv', row_count=3)
    rules = compound_filter_state(s)['rules']
    op, word = ('lt', '적은') if mode == 'any' else ('gte', '이상')
    ledger = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': (op, word),
                       'filter.value': ('10', '10원'), 'output.format': ('json', 'JSON'),
                       'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')})
    assert ledger['decision'] == 'ready'
    store = Storage(tmp_path)
    try:
        store.add_asset(asset, [{'비용': a, '수량': b} for a, b in ROWS])
        extra = [*rules, dict(rules[0], value=100)]
        cases = [(rules[:1], mode, True), (rules, 'all' if mode == 'any' else 'any', True),
                 (extra, mode, True), (rules, mode, False)]
        for selected, match, blocked in cases:
            draft = filter_draft(s, selected, match)
            draft['execution_plan'] = draft['workflow']['nodes'][1]['settings']['execution_plan']
            current = {**s, 'draft': draft, 'condition_enforced': True,
                       'condition_ledger': ledger, 'confirmed': True}
            assert bool(plan_issues(ledger, draft, state=s)) == blocked
            assert (validate_node(current)['status'] != 'ready') == blocked
            if blocked:
                with pytest.raises(ValueError, match='확정 업무 조건'):
                    store.save_agent(current)
            else:
                identifier = store.save_agent(current)
                result = run_workflow(store, identifier, '실행', '', file_ids=['table'])
                assert result['status'] == 'succeeded'
                assert [(row['비용'], row['수량']) for row in result['rows']] == expected
    finally:
        store.close()


def test_cancelled_compound_is_not_restored_from_the_old_single_filter_ledger():
    from agent_builder.conditions.condition_readiness import compound_filter_state
    s = compound_state(BASE)
    s['condition_ledger'] = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': ('gte', '이상'),
                                    'filter.value': ('10', '10원'), 'output.format': ('json', 'JSON')})
    s['messages'] += [{'role': 'user', 'content': '전체 필터 제거해줘'},
                      {'role': 'user', 'content': 'CSV로 바꿔줘'}]
    found = compound_filter_state(s)
    assert found['rules'] == [] and found['pending'] is None


def test_compound_save_rejects_a_ledger_value_not_in_the_confirmed_rules():
    from copy import deepcopy
    from agent_builder.conditions.condition_readiness import compound_filter_state, plan_issues
    s = compound_state(BASE)
    ledger = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': ('gte', '이상'),
                       'filter.value': ('10', '10원'), 'output.format': ('json', 'JSON')})
    draft = filter_draft(s, compound_filter_state(s)['rules'], 'all')
    stale = deepcopy(ledger)
    stale['slots']['filter.value']['value'] = 50
    assert plan_issues(stale, draft, state=s)
    assert not plan_issues(ledger, draft, state=s)
