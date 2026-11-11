"""Regressions for a verified numeric answer followed by a generic model action."""
import pytest

from agent_builder.design import requirement_ledger
from tests.conditions.test_condition_readiness import checked, proposal, state as new_state


REQUEST = '연결한 표에서 amount가 큰 행만 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'
DESCRIPTION = 'amount 가 20 이상인 행을 필터링하여 JSON 형식으로 저장'


def answered_request(request=REQUEST, answer='20 이상으로 해줘.', source=0, description=DESCRIPTION,
             linked=True, other_input=False):
    s = new_state(request)
    s['resources'] = [dict(id='sales', name='sales.json', kind='table', columns=['amount','cost'])]
    if other_input:s['resources'].append(dict(id='cost',name='cost.json',kind='table',columns=['amount','cost']))
    facts = {'filter.field':'amount','filter.operator':None,'filter.value':None,
             'missing.policy':'error','duplicate.policy':'keep','output.format':'json'}
    first = checked(s,proposal(s,facts,['filter']))
    s['messages'] += [dict(role='assistant', content='기준 값을 포함할까요? 선별할 기준 값은 얼마인가요?'),
                      dict(role='user', content=answer)]
    s.update(condition_ledger=first,pending_questions=first['questions'])
    if linked:
        question = next(q for q in first['questions'] if q['key'] == 'filter.operator')
        s['condition_answers'] = [dict(turn=2, answer=answer, question_ids=[question['id']], candidate_keys=[question['key']])]
    update = proposal(s, {'filter.operator':'gte','filter.value':'20'}, ['filter'])
    s.update(condition_ledger=checked(s,update),condition_enforced=True,pending_questions=[])
    packet=dict(tasks=[dict(description=description,spec={'kind':'other','action':'execute'},source_message=source)],unresolved=[])
    return s,packet


@pytest.mark.parametrize(('edits','drops'), [
    ({},True),
    ({'description':'amount >= 20인 행만 JSON으로 저장'},True),
    ({'description':'필터 JSON으로 저장'},False),
    ({'description':'amount >= 20인 행만 저장'},False),
    ({'description':'amount > 20인 행만 JSON으로 저장'},False),
    ({'description':'cost >= 20인 행만 JSON으로 저장'},False),
    ({'description':'amount >= 21인 행만 JSON으로 저장'},False),
    ({'description':DESCRIPTION+'하고 팀장에게 전송'},False),
    ({'description':'원본을 JSON으로 저장한 다음 '+DESCRIPTION},False),
    ({'description':'amount >= 20인 행만 CSV로 저장'},False),
    ({'linked':False},False), ({'source':1},False), ({'other_input':True},False),
    ({'request':REQUEST+' 원본도 따로 저장해줘.'},False),
    ({'request':REQUEST+' 그리고 담당자에게 메일을 보내줘.'},False),
    ({'request':REQUEST+' cost 합계도 계산해줘.'},False),
    ({'request':REQUEST.replace('유지해.', '유지하고 보고서를 검수해줘.')},False),
    ({'answer':'20 이상으로 해줘. 원본도 하나 보관해.'},False),
])
def test_generic_action_survives_unless_every_assertion_is_proven(edits,drops):
    state, packet = answered_request(**edits)
    plan=requirement_ledger.extract_plan(packet,state)
    assert ('other' not in [t['spec']['kind'] for t in plan['tasks']]) == drops
    if drops:
        assert [t['spec']['kind'] for t in plan['tasks']]==['filter','policy','export']
        assert plan['tasks'][-1]['depends_on']==[plan['tasks'][0]['id']]


def test_confirmed_answer_keeps_its_settings_through_save(tmp_path):
    from infrastructure.persistence.storage import Storage
    from tests.conditions.test_condition_readiness import workflow_draft
    from agent_builder.design.nodes.validation import validate_node

    state, packet = answered_request()
    plan = requirement_ledger.extract_plan(packet, state)
    draft = workflow_draft(state, threshold=20, operator='gte')
    draft['execution_plan']['filters'][0]['field'] = 'amount'
    draft['workflow']['nodes'][1]['settings']['execution_plan'] = draft['execution_plan']
    state.update(draft=draft, requirement_required=True, requirement_ledger=plan,
                 requirement_digest=requirement_ledger.plan_digest(plan),
                 requirement_fingerprint=requirement_ledger.ledger_fingerprint(state))
    result = validate_node(state)
    assert result['status'] == 'ready', result['issues']
    store = Storage(tmp_path)
    try:
        saved = store.agent(store.save_agent({**state, **result, 'confirmed':True}))['config']
        assert saved['execution_plan']['filters'] == plan['tasks'][0]['spec']['filters']
        assert saved['execution_plan']['missing_policy'] == 'error'
        assert saved['workflow']['nodes'][-1]['settings']['format'] == 'json'
    finally:
        store.close()
