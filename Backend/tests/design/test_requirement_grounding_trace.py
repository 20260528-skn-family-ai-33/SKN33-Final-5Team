"""Exercise observed failure packets against the normal design nodes."""
import pytest

from agent_builder.capabilities.capability_check import make_capability_check
from agent_builder.design.request_meaning import gmail_importance, validate_meaning
from agent_builder.design.requirement_ledger import make_requirement_check
from tests.design.test_qwen_public_meaning_failures import request
from tests.design.test_request_meaning import meaning
from tests.design.test_runpod_compute_clarification import packet, state, task, Transport


def test_ambiguous_importance_routes_to_one_business_question_even_if_candidate_omits_it():
    current = request('Gmail에서 중요한 메일만 요약해 주세요.')
    result = make_capability_check('fixture', Transport({}), mode='enforce')(current)
    assert result['status'] == 'needs_clarification'
    assert [item['key'] for item in result['pending_questions']] == ['gmail.importance_criteria']
    assert result['capability_assessment']['model_calls'] == 0


@pytest.mark.parametrize('flag', ['important', 'is:important'])
def test_generic_importance_never_seals_an_important_flag(flag):
    current = request('Gmail에서 중요한 메일만 요약해 주세요.')
    with pytest.raises(ValueError):
        validate_meaning(meaning(count=None, evidence=None, filters=[flag]), current)


@pytest.mark.parametrize(('answer','expected'), [
    ('별표를 붙인 메일만 골라줘.', 'starred'),
    ('Gmail 중요 표시된 메일 기준으로 해줘.', 'important'),
    ('팀장님이 보낸 메일로 할게.', 'custom'),
    ('최대 4건만 보여줘.', 'ambiguous'),
    ('중요도와 상관없이 전체 메일을 가져와.', None),
])
def test_importance_answer_is_distinct_from_a_count_or_another_selection(answer, expected):
    current = request('Gmail에서 중요한 메일만 요약해 주세요.')
    current['messages'].append({'role':'user', 'content':answer})
    assert gmail_importance(current)[0] == expected
    if expected != 'important':
        with pytest.raises(ValueError):
            validate_meaning(meaning(count=None, evidence=None, filters=['important']), current)


def test_send_only_has_no_missing_selection_criterion():
    assert gmail_importance(request('Gmail로 고객에게 중요한 메일을 보내줘.'))[0] is None


def test_importance_question_resume_reaches_correct_executable_query(tmp_path):
    from langgraph.types import Command
    from infrastructure.persistence.storage import Storage
    from tests.design.test_meaning_graph import graph_for
    from tests.design.test_request_meaning import Extractor, mail_arguments

    current = request('Gmail에서 중요한 메일만 요약해 주세요.')
    transport = Extractor(meaning(count=None, evidence=None, filters=['important']),
                          meaning(count=None, evidence=None, filters=['starred']))
    store = Storage(tmp_path)
    graph, connection = graph_for(store, transport)
    config = {'configurable': {'thread_id':current['session_id']}}
    try:
        first = graph.invoke(current, config)
        assert first['status'] == 'needs_clarification'
        question = first['pending_questions'][0]
        result = graph.invoke(Command(resume={'type':'message', 'question_id':question['id'],
                                               'content':'Gmail에서 별표 표시한 메일을 뜻해.'}), config)
        assert result['status'] == 'ready'
        assert mail_arguments(result)['query'] == 'is:starred -in:sent -in:drafts'
    finally:
        connection.close()
        store.close()


def test_invalid_formula_placeholders_do_not_hide_two_user_decisions():
    current = state('연결한 표에 새 계산 열을 만들고 CSV로 저장해 주세요.')
    raw = packet(task('compute', formula='{qty}*{unit_price}', output_column='total_amount'))
    result = make_requirement_check('fixture', Transport(raw), mode='enforce')(current)
    assert result['status'] == 'needs_clarification'
    assert {item['key'] for item in result['pending_questions']} == {'compute.formula', 'compute.output_column'}
    assert [item['spec']['kind'] for item in result['requirement_ledger']['tasks']] == ['read', 'export']


def test_cancelled_computation_does_not_return_from_a_previous_candidate():
    from agent_builder.design.requirement_ledger import extract_plan
    current = state('새 계산 열 net = units * rate를 만들고 CSV로 저장해줘.',
                    '계산 열은 취소하고 표를 CSV로 저장해줘.')
    raw = packet(task('compute', formula='units * rate', output_column='net'))
    raw['unresolved'] = [{'key':'compute.formula', 'text':'수식을 알려주세요.'}]
    plan = extract_plan(raw, current)
    assert plan['unresolved'] == []
    assert [item['spec']['kind'] for item in plan['tasks']] == ['read', 'export']


def test_distinct_computations_from_two_turns_keep_both_pairs():
    from agent_builder.design.requirement_ledger import extract_plan
    current = state('새 계산 열 gross = units * rate를 만들고 CSV로 저장해줘.',
                    '새 계산 열 doubled = units * 2도 추가해줘.')
    raw = packet(task('compute', formula='units * rate', output_column='gross'),
                 task('compute', formula='units * 2', output_column='doubled'))
    plan = extract_plan(raw, current)
    assert [t['spec']['output_column'] for t in plan['tasks'] if t['spec']['kind'] == 'compute'] == ['gross', 'doubled']


@pytest.mark.parametrize('candidate', [
    {'tasks': [task('compute', formula='{{ qty * unit_price }}', output_column='total_amount')],
     'unresolved': ['synthetic-sales.json 파일의 qty와 unit_price를 기반으로 총액을 계산하고 JSON으로 저장하시겠습니까?']},
    {'tasks': [task('compute', formula='{qty * unit_price}', output_column='total_amount'),
               task('export', format='json')], 'unresolved': ['연결된 표의 ID 가 무엇인가요?']},
    '{truncated',
])
def test_missing_compute_preserves_the_known_input_and_export_from_source(candidate):
    current = state('연결한 표에 새 계산 열을 만들고 JSON으로 저장해줘.')
    result = make_requirement_check('fixture', Transport(candidate), mode='enforce')(current)
    assert result['status'] == 'needs_clarification'
    ledger = result['requirement_ledger']
    assert [t['spec'] for t in ledger['tasks']] == [
        {'kind': 'read', 'file_ids': ['fixture-table']}, {'kind': 'export', 'format': 'json'}]
    assert {q['key'] for q in ledger['unresolved']} == {'compute.formula', 'compute.output_column'}


def test_ambiguous_mail_selection_does_not_call_the_meaning_model_before_asking():
    from agent_builder.design.request_meaning import make_meaning_node

    transport = Transport(OSError('model offline'))
    current = request('Gmail에서 중요한 메일만 요약해줘.')
    update = make_meaning_node('fixture', transport=transport, capability_mode='enforce')(current)
    result = make_capability_check('fixture', transport, mode='enforce')({**current, **update})
    assert [item['key'] for item in result['pending_questions']] == ['gmail.importance_criteria']
    assert transport.calls == 0


def test_replacing_a_mail_flag_uses_the_new_criterion():
    current = request('Gmail에서 중요 표시된 메일만 요약해줘.')
    current['messages'].append({'role': 'user', 'content': '중요 표시는 말고 별표 표시한 메일로 바꿔줘.'})
    assert gmail_importance(current)[0] == 'starred'


def test_meaning_transport_failure_is_not_recorded_as_missing_user_input():
    from agent_builder.design.request_meaning import make_meaning_node

    current = request('Gmail에서 팀 공지 메일을 찾아 요약해줘.')
    result = make_meaning_node('fixture', transport=Transport(OSError('model offline')))(current)
    trace = result['trace'][-1]
    assert trace['status'] == 'failed'
    assert trace['failure_kind'] == 'system_error'
    assert not result.get('pending_questions')
