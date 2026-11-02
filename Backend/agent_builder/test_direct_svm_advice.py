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


def test_optional_additional_candidates_remain_advisory_and_registered_only():
    from integrations.tool_recommender_svm56.service_advice_adapter import build_svm_advice
    class Model:
        labels=['read_table','calculate_columns','private_tool','untrained']
        def predict(self, query, top_k):
            assert top_k==4
            return {'threshold':0.0,'selected_tool_ids':['read_table'],
                    'untrained_tool_ids':['untrained'],
                    'ranked_tools':[{'tool_id':'read_table','decision_score':0.3,'selected_by_threshold':True},
                                    {'tool_id':'calculate_columns','decision_score':-0.2,'selected_by_threshold':False},
                                    {'tool_id':'private_tool','decision_score':-0.1,'selected_by_threshold':False}]}
    state={'messages':[{'role':'user','content':'표를 계산해 줘'}]}
    registry=[{'id':'read_table'},{'id':'calculate_columns'},{'id':'untrained'}]
    baseline=build_svm_advice(state,registry,Model())
    expanded=build_svm_advice(state,registry,Model(),extra_margin=-0.25)
    assert baseline['ml_additional_review_candidates']==[]
    assert [r['tool_id'] for r in expanded['ml_candidates']]==['read_table']
    assert [r['tool_id'] for r in expanded['ml_additional_review_candidates']]==['calculate_columns']
    assert expanded['advisory_only'] is True
    assert 'private_tool' not in str(expanded['ml_additional_review_candidates'])
    assert expanded['untrained_registered_tool_ids']==['untrained']
    assert build_svm_advice(state,registry,Model(),extra_margin=float('nan'))['ml_status']=='failed'


def test_untrusted_model_is_rejected_before_deserialization(tmp_path,monkeypatch):
    import joblib
    import pytest
    from integrations.tool_recommender_svm56 import svm56_service_hook as hook
    model=tmp_path/'model.joblib';model.write_bytes(b'untrusted')
    catalog=tmp_path/'catalog.json';catalog.write_text('[]',encoding='utf8')
    monkeypatch.setattr(joblib,'load',lambda *_:pytest.fail('untrusted data was deserialized'))
    hook._load_pinned.cache_clear()
    with pytest.raises(ValueError,match='hash'):
        hook._load_pinned(str(model),str(catalog))
