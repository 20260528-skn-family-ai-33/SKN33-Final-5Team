"""Prove that grouped changes retain the current task and its exact source turns."""
import json
from copy import deepcopy

import pytest

from agent_builder.design.grouped_totals_contract import contract_specs, grouped_contract
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest, validate_plan
from tests.design.test_grouped_totals_request import controlled_draft, state


def seal_with_stale_model_sources(current):
    contract = grouped_contract(current)
    raw = dict(tasks=[dict(source_message=0, description=role, spec=spec)
                      for role, spec in contract_specs(contract)], unresolved=[])
    plan = extract_plan(raw, current)
    current.update(requirement_required=True, requirement_ledger=plan,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(plan))
    return {task['description']: task for task in plan['tasks']}


def test_revised_task_sources_come_from_the_contract_instead_of_the_model():
    current = state()
    original = current['messages'][0]['content']
    changes = ['완료 대신 취소 주문만 대상으로 해줘.', '계산식은 수량 + 단가로 변경해줘.',
               '내림차순으로 정렬해줘.', 'JSON으로 바꿔줘.']
    turns = ['이전 CSV를 저장해줘.', '새로운 작업이야.', original, *changes, '네, 확인했습니다.']
    current['messages'] = [dict(role='user', content=turn) for turn in turns]
    tasks = seal_with_stale_model_sources(current)

    revision_indexes = dict(filter=3, compute=4, sort=5, export_json=6)
    for role, task in tasks.items():
        source = revision_indexes.get(role, 2)
        assert task['source_message'] == source
        assert task['quote'] == turns[source]
    assert tasks['filter']['spec']['filters'][0]['value'] == '취소'
    assert tasks['compute']['spec']['formula'] == '수량 + 단가'
    assert tasks['sort']['spec']['sort'][0]['direction'] == 'descending'
    assert tasks['export_json']['spec']['format'] == 'json'
    assert tasks['sum']['depends_on'] == tasks['count']['depends_on'] == [tasks['compute']['id']]
    assert tasks['export_json']['depends_on'] == [tasks['select']['id']]

    forged = deepcopy(current['requirement_ledger'])
    export = next(task for task in forged['tasks'] if task['spec']['kind'] == 'export')
    export.update(source_message=2, quote=original)
    with pytest.raises(ValueError, match='근거가 현재 요청 또는 후속 변경'):
        validate_plan(forged, current)


def test_sort_cancellation_and_format_change_keep_the_group_calculation():
    current = state()
    current['messages'].append(dict(role='user', content='정렬은 필요 없어. JSON으로 바꿔줘.'))
    tasks = seal_with_stale_model_sources(current)
    assert 'sort' not in tasks
    assert tasks['select']['depends_on'] == [tasks['total_name']['id'], tasks['count_name']['id']]
    assert tasks['select']['spec']['fields'] == ['지역', '총매출', '주문건수']
    assert tasks['export_json']['source_message'] == 1


def test_format_clause_must_not_hide_an_unrecognized_row_change():
    current = state()
    current['messages'].append(dict(role='user', content='비회원 주문은 제외하고 JSON으로 바꿔줘.'))
    with pytest.raises(ValueError, match='후속 변경'):
        seal_with_stale_model_sources(current)


def test_deepagent_schema_and_draft_use_new_grouped_request_and_revisions(monkeypatch):
    from agent_builder.design.nodes import conversation

    current = state()
    original = current['messages'][0]['content']
    change = 'JSON으로 바꿔줘.'
    current['messages'] = [dict(role='user', content=turn) for turn in (
        '이전 자료를 정리해줘. ' + '가' * 2001, '새로운 작업이야.', original, change, '네.')]
    current.update(issues=[], repair_count=0, node_library=[])
    seal_with_stale_model_sources(current)
    answer = controlled_draft(current)
    answer['requirements'] = [dict(description='지역별 집계', evidence_id='e1')]
    seen = []

    def factory(**options):
        schema = options['response_format'].schema
        assert schema['$defs']['Requirement']['properties']['evidence_id']['enum'] == ['e1', 'e2']

        class Agent:
            def invoke(self, payload, config):
                context = json.loads(payload['messages'][0]['content'].split('\n', 1)[1])
                seen.extend(item['quote'] for item in context['available_evidence'])
                return {'structured_response': deepcopy(answer)}

        return Agent()

    monkeypatch.setenv('A4B_PLANNER_MODE', 'deepagent')
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setattr(conversation, 'create_deep_agent', factory)
    result = conversation.make_conversation(None, 'fixture', model_factory=object)(current)
    assert seen == [original, change]
    assert [item['quote'] for item in result['draft']['requirements']] == [original, change]
