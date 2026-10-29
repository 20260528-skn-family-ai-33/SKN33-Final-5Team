import sys
import types
from unittest.mock import Mock
import pytest
from agent_builder.tool_recommendation import recommend_for_state
from agent_builder.registry import TOOLS

@pytest.mark.parametrize('error',[FileNotFoundError('model missing'),RuntimeError('model inference failed')])
def test_optional_recommender_failure_retains_full_catalog_and_is_not_unsupported(monkeypatch,error):
    monkeypatch.setenv('A4B_TOOL_ADVICE_MODE','hint')
    package=types.ModuleType('tool_rec');package.__path__=[]
    module=types.ModuleType('tool_rec.recommend');module.recommend_tools=Mock(side_effect=error)
    monkeypatch.setitem(sys.modules,'tool_rec',package);monkeypatch.setitem(sys.modules,'tool_rec.recommend',module)
    before=[x['id'] for x in TOOLS]
    result=recommend_for_state({'messages':[{'role':'user','content':'표를 정리해줘'}],'resources':[]})
    assert result['ml_status']=='failed' and result['ml_candidates']==[]
    assert result['ml_error_type']==type(error).__name__ and '지원하지' not in result['ml_usage']
    assert [x['id'] for x in TOOLS]==before and len(before)==16

def test_invalid_advice_mode_is_not_silently_ignored(monkeypatch):
    monkeypatch.setenv('A4B_TOOL_ADVICE_MODE','wrong')
    with pytest.raises(ValueError,match='off/hint'):recommend_for_state({'messages':[]})


def test_recommender_failure_does_not_narrow_tools_or_create_unsupported(monkeypatch):
    from agent_builder.nodes.conversation import make_conversation
    from agent_builder.test_deep_agent_conversation import state,draft
    monkeypatch.setenv('A4B_TOOL_ADVICE_MODE','hint')
    package=types.ModuleType('tool_rec');package.__path__=[]
    module=types.ModuleType('tool_rec.recommend');module.recommend_tools=Mock(side_effect=FileNotFoundError())
    monkeypatch.setitem(sys.modules,'tool_rec',package);monkeypatch.setitem(sys.modules,'tool_rec.recommend',module)
    def factory(**kwargs):
        tools={t.name:t for t in kwargs['tools']}
        class Agent:
            def invoke(self,payload,config):
                assert len(tools['list_tools'].invoke({}))==16
                return {'structured_response':draft(questions=['기준을 알려주세요'])}
        return Agent()
    monkeypatch.setattr('agent_builder.nodes.conversation.create_deep_agent',factory)
    result=make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'표를 정리해줘'}]))
    assert result['draft']['questions']==['기준을 알려주세요'] and not result['draft']['unsupported']
    assert result['trace'][-1]['tool_advice_status']=='failed'
