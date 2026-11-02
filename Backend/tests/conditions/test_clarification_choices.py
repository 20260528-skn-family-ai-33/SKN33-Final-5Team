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


def vague():
    s = state('비용 큰 것들만 골라서 파일로 만들어줘.'); s['resources'] = [TABLE]
    ledger = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': (None, ''), 'filter.value': (None, ''),
                       'output.format': (None, '')})
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
    assert options['filter.operator'] == ['초과', '이상', '미만', '이하']
    assert options['filter.value'] == []  # a suggested threshold would invent the criterion
    assert all('권장' not in o for opts in options.values() for o in opts)


def test_clicked_choice_is_confirmed_without_a_model_call_and_survives_revalidation():
    s, ledger = vague()
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
    s, ledger = vague()
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
    s, ledger = vague()
    operator = next(q for q in ledger['questions'] if q['key'] == 'filter.operator')
    s = click(s, operator, '모르겠어')
    assert option_answer(s) is None
    result = judge_conditions(s, Fake([]))
    assert result['model_calls'] == 0 and result['ledger']['slots']['filter.operator']['status'] == 'missing'


def test_capability_judgement_is_reused_for_a_clicked_choice_only_with_unchanged_input():
    s, ledger = vague()
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
