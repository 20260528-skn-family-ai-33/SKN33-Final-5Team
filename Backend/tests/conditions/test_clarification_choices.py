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
