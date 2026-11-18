"""Independent rows and real graph transitions for the shared text-filter contract."""
from copy import deepcopy
from itertools import permutations
import json
from pathlib import Path

import pytest
from langgraph.types import Command

from agent_builder.design.condition_requirements import multiple_outputs
from agent_builder.design.condition_templates import compile_conditions
from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from agent_builder.design.text_filter_contract import text_filter_state
from infrastructure.persistence.storage import Storage
from tests.conditions.test_clarification_choices import compound_state, judge, PARTIAL
from tests.conditions.test_condition_readiness import Fake, proposal
from tests.design.test_compound_filter_pipeline import generate
from tests.design.test_condition_requirement_ownership import packet
from workflow_engine.runner import run_workflow

BASE = '비용이 10원 이상이고 수량이 3개 이상인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'
VALUES = {**PARTIAL, 'output.format': ('json', 'JSON'), 'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')}
ROWS = [dict(비용=15, 수량=4, 지역='서울', 등급='A', 상태='완료'),
        dict(비용=15, 수량=4, 지역='부산', 등급='A', 상태='완료'),
        dict(비용=15, 수량=4, 지역='서울', 등급='B', 상태='대기'),
        dict(비용=5, 수량=4, 지역='서울', 등급='A', 상태='완료')]


def current(instruction):
    s = compound_state(BASE + ' ' + instruction)
    asset = s['resources'][0]
    asset.update(columns=list(ROWS[0]), format='csv',
                 profile={field: {'types': ['number' if field in ('비용', '수량') else 'string']} for field in ROWS[0]})
    return s


def prepare(s, values=None):
    ledger = judge(s, values or VALUES)
    assert ledger['decision'] == 'ready', ledger['questions']
    s.update(condition_ledger=ledger, condition_enforced=True, condition_required=True, pending_questions=[])
    # Model omits every filter: production ownership must reconstruct the whole instruction.
    requirements = extract_plan(packet(dict(kind='export', format='json')), s)
    s.update(requirement_required=True, requirement_ledger=requirements,
             requirement_fingerprint=ledger_fingerprint(s), requirement_digest=plan_digest(requirements))
    return generate(s)


def execute(s, path):
    store = Storage(path)
    try:
        store.add_asset(s['resources'][0], ROWS)
        result = run_workflow(store, store.save_agent(s), '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded'
        artifact = result['artifact']
        assert artifact['name'].endswith('.json')
        exported = json.loads(artifact['content'].lstrip('\ufeff'))
        assert exported == result['rows']
        return result['rows']
    finally:
        store.close()


@pytest.mark.parametrize('instruction', [
    '지역이 서울이고 등급이 A인 행만 남겨줘',
    '등급이 A이고 지역이 서울인 행만 남겨줘',
    '지역이 서울이며 등급이 A인 행만 남겨줘',
    '지역이 서울이면서 등급이 A인 행만 남겨줘',
    '지역이 서울인 행만, 등급이 A인 행만',
    '지역이 서울인 것만 그리고 등급이 A인 것만 남겨줘',
    '서울 지역만, A 등급만 남겨줘',
])
def test_text_conjunction_keeps_every_operand_through_export(tmp_path, instruction):
    s = prepare(current(instruction))
    assert execute(s, tmp_path) == ROWS[:1]


@pytest.mark.parametrize('order', list(permutations(['지역이 서울', '등급이 A', '상태가 완료'])))
def test_three_text_conditions_are_order_independent(tmp_path, order):
    s = prepare(current('이고 '.join(order) + '인 행만 남겨줘'))
    assert execute(s, tmp_path) == ROWS[:1]


@pytest.mark.parametrize('instruction', ['서울 지역만', '지역이 서울인 것만', '지역은 서울만 남겨줘', '서울 지역인 행만'])
def test_alternate_text_wording_has_the_same_result(tmp_path, instruction):
    assert execute(prepare(current(instruction)), tmp_path) == [ROWS[0], ROWS[2]]


@pytest.mark.parametrize('instruction', [
    '지역이 서울이고 등급은 나중에 정할게. 행만 남겨줘',
    '지역이 서울인 행만, 등급은 아직 미정인 행만',
    '지역이 서울인 행만 또는 등급이 A인 행만 남겨줘',
    '지역이 서울이고 지역이 부산인 행만 남겨줘',
])
def test_unsettled_operands_never_become_partial_ready_filters(instruction):
    s = current(instruction)
    assert text_filter_state(s)['issues']
    assert judge(s, VALUES)['decision'] != 'ready'


@pytest.mark.parametrize('removed_field', ['지역', '등급'])
def test_missing_text_operand_cannot_be_saved(tmp_path, removed_field):
    s = prepare(current('지역이 서울이고 등급이 A인 행만 남겨줘'))
    bad = deepcopy(s)
    for node in bad['draft']['workflow']['nodes']:
        if node['tool'] == 'filter_sort':
            node['settings']['execution_plan']['filters'] = [r for r in node['settings']['execution_plan']['filters'] if r['field'] != removed_field]
    assert validate_node(bad)['status'] in ('validation_failed', 'needs_clarification')
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError):
            store.save_agent(bad)
    finally:
        store.close()


@pytest.mark.parametrize('field', ['비용', '지역'])
@pytest.mark.parametrize('structured', [False, True])
def test_keep_answer_finishes_the_question_and_exports_original_conditions(tmp_path, field, structured):
    s = current(f'지역이 서울인 행만 남겨줘. {field} 조건 삭제해도 될까?')
    raw = proposal(s, {'filter.field': '비용', 'filter.operator': None, 'filter.value': None,
                      'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'})
    fake = Fake([raw])
    store = Storage(tmp_path); store.add_asset(s['resources'][0], ROWS)
    graph, connection = build_graph(store, conversation=compile_conditions, condition_transport=fake, condition_mode='enforce')
    config = {'configurable': {'thread_id': s['session_id']}}
    try:
        asked = graph.invoke(s, config)
        assert [q['key'] for q in asked['pending_questions']] == ['filter.match']
        question = asked['pending_questions'][0]
        reply = dict(type='message', content='기존 조건 모두 유지', question_id=question['id'])
        if structured:
            reply.update(content=question['text'] + ' → 기존 조건 모두 유지',
                         question_answers=[dict(question_id=question['id'], value='기존 조건 모두 유지')])
        ready = graph.invoke(Command(resume=reply), config)
        assert ready['status'] == 'ready', ready.get('issues')
        assert not ready['pending_questions'] and not multiple_outputs(ready)
        assert fake.calls == 1
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded' and result['rows'] == [ROWS[0], ROWS[2]]
    finally:
        connection.close(); store.close()


@pytest.mark.parametrize('reply', ['기존 조건 모두 유지', '모두 만족', '둘 다', 'AND', 'OR'])
def test_filter_control_replies_do_not_create_extra_artifacts(reply):
    s = current('지역이 서울인 행만 남겨줘.')
    s['messages'].append(dict(role='user', content='질문 → ' + reply))
    s['condition_answers'] = [dict(turn=1, candidate_keys=['filter.match'], answer=reply)]
    assert not multiple_outputs(s)
    s['messages'].append(dict(role='user', content='별도 CSV 파일도 만들어줘.'))
    assert multiple_outputs(s)


def test_deployment_gate_runs_the_filter_contract_regressions():
    workflow = Path(__file__).resolve().parents[3] / '.github/workflows/deploy.yml'
    text = workflow.read_text()
    for suite in ['tests/conditions/test_clarification_choices.py', 'tests/design/test_compound_filter_pipeline.py',
                  'tests/design/test_filter_edit_sequences.py', 'tests/design/test_text_filter_dialogue.py']:
        assert suite in text


@pytest.mark.parametrize('instruction', [
    '지역이 서울이고 등급이 A인 행만 JSON으로 저장해줘.',
    '등급이 A이고 지역이 서울인 행만 JSON으로 저장해줘.',
    '서울 지역만, A 등급만 JSON으로 저장해줘.',
])
def test_text_only_request_uses_the_same_contract_in_slots_and_export(tmp_path, instruction):
    s = current('')
    s['messages'][0]['content'] = instruction + ' 빈 값은 오류, 중복은 유지해.'
    # The extractor knows the field but leaves the operator/value empty.
    values = {'filter.field': ('지역', '지역'), 'filter.operator': (None, ''),
              'filter.value': (None, ''), 'output.format': ('json', 'JSON'),
              'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')}
    assert execute(prepare(s, values), tmp_path) == [ROWS[0], ROWS[3]]


@pytest.mark.parametrize('instruction', [
    '두 조건 모두 만족하는 행을 JSON으로 저장해줘.',
    '두 조건을 둘 다 만족하는 결과를 JSON으로 저장해줘.',
    '기존 조건 모두 유지. 결과를 JSON으로 저장해줘.',
])
def test_condition_quantifiers_are_not_artifact_counts(instruction):
    assert not multiple_outputs(current(instruction))


def test_cancelling_one_text_atom_preserves_every_other_atom(tmp_path):
    s = current('지역이 서울이고 등급이 A인 행만 남겨줘.')
    s['messages'].append(dict(role='user', content='지역 조건 취소해줘'))
    assert execute(prepare(s), tmp_path) == ROWS[:2]


def test_keep_resolves_multiple_cancellation_questions_without_losing_conditions(tmp_path):
    s = current('지역이 서울이고 등급이 A인 행만 남겨줘.')
    s['messages'].append(dict(role='user', content='지역 조건 삭제해도 될까? 등급 조건 취소할지 모르겠어'))
    ledger = judge(s, VALUES)
    assert ledger['decision'] == 'clarify'
    q = next(q for q in ledger['questions'] if q['key'] == 'filter.match')
    s['messages'].append(dict(role='user', content='기존 조건 모두 유지'))
    s['condition_answers'] = [dict(turn=2, question_ids=[q['id']], candidate_keys=['filter.match'], answer='기존 조건 모두 유지')]
    assert execute(prepare(s), tmp_path) == ROWS[:1]


def test_shared_exclusion_does_not_invert_each_operand_independently():
    s = current('지역이 서울이고 등급이 A인 행만 제외해줘')
    assert judge(s, VALUES)['decision'] != 'ready'


@pytest.mark.parametrize('instruction', [
    '중복 학생ID는 마지막 행만 남겨', 'amount가 큰 행만 JSON으로 저장해줘',
    'customer_id 가 같은 행 중 첫 번째만 유지한 뒤 JSON 로 저장한다.',
    '비용이 비어 있지 않은 행만 남겨줘',
])
def test_unparsed_selection_does_not_take_over_other_condition_owners(instruction):
    s = current('')
    s['messages'][0]['content'] = instruction
    s['resources'][0]['columns'] += ['학생ID', 'amount', 'customer_id']
    assert not text_filter_state(s)['issues']


@pytest.mark.parametrize('instruction', [
    "'서울' 지역만, 'A' 등급만 남겨줘", "지역이 '서울 또는 부산'인 행만 남겨줘",
])
def test_quoted_literals_do_not_become_connectives(instruction):
    contract = text_filter_state(current(instruction))
    assert not contract['issues']
    if '또는' in instruction:
        assert contract['predicates'][0][1]['value'] == '서울 또는 부산'
    else:
        assert {r['field'] for _, r in contract['predicates']} == {'지역', '등급'}
