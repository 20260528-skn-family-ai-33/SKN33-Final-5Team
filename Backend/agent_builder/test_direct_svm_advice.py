import json
from agent_builder.nodes.conversation import make_conversation
from agent_builder.test_direct_conversation import Fake, draft, state

def test_direct_planner_receives_optional_advice_and_full_catalog(monkeypatch):
    monkeypatch.setenv('A4B_PLANNER_MODE','direct')
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v1')
    monkeypatch.setattr('agent_builder.tool_recommendation.recommend_for_state',
        lambda state:{'ml_status':'candidates_available','ml_candidates':[{'tool_id':'read_table','decision_score':0.5}], 'advisory_only':True})
    fake=Fake(draft())
    result=make_conversation('', 'fixture', model_factory=lambda:fake)(state())
    context=json.loads(fake.calls[0][1]['content'].split('\n',1)[1])
    assert context['tool_selection_advice']['advisory_only'] is True
    assert context['tool_selection_advice']['ml_candidates'][0]['tool_id']=='read_table'
    assert 'calculate_columns' in {t['id'] for t in context['supported_tools']}
    assert result['trace'][-1]['tool_advice_status']=='candidates_available'
