import json
import pytest
from agent_builder.design.nodes.direct_conversation import OllamaPlanner
from agent_builder.capabilities.capability_check import make_capability_check


def test_finetune_native_wire_and_separate_capability_model(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','https://example.test/v1')
    monkeypatch.setenv('A4B_CAPABILITY_MODEL','previous105')
    monkeypatch.setenv('A4B_OLLAMA_NUM_CTX','32768')
    monkeypatch.setenv('A4B_OLLAMA_NUM_PREDICT','4096')
    monkeypatch.setenv('A4B_OLLAMA_TEMPERATURE','0')
    seen=[]
    s={'messages':[{'role':'user','content':'입력 표를 JSON으로 저장해줘.'}], 'resources':[], 'trace':[]}
    assessment={'decision':'existing_tools','tool_ids':['read_table','write_result'],
                'missing_conditions':[],'missing_actions':[],'reason':'등록된 표 저장 도구 사용',
                'user_quotes':[s['messages'][0]['content']]}
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'message':{'content':json.dumps(assessment)}}).encode()
    def open_request(request,timeout):
        seen.append(json.loads(request.data));return Response()
    monkeypatch.setattr('urllib.request.urlopen',open_request)
    result=make_capability_check('final664',mode='enforce')(s)
    assert result['capability_assessment']['decision']=='existing_tools'
    assert seen and all(p['model']=='previous105' for p in seen)
    OllamaPlanner('final664').invoke([{'role':'user','content':'test'}])
    assert seen[-1]['model']=='final664'
    for p in seen:
        assert p['think'] is False
        assert p['options']['num_ctx']==32768
        assert p['options']['num_predict']==4096
        assert p['options']['temperature']==0


@pytest.mark.parametrize('key,value',[
    ('A4B_OLLAMA_NUM_CTX','0'),('A4B_OLLAMA_TEMPERATURE','nan'),('A4B_OLLAMA_NUM_PREDICT','-1')])
def test_invalid_deployment_limits_fail_before_network(monkeypatch,key,value):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','https://example.test/v1')
    monkeypatch.setenv(key,value)
    with pytest.raises(ValueError,match=key):OllamaPlanner('final664')


@pytest.mark.parametrize('empty',['','   '])
def test_empty_compose_overrides_preserve_native_and_chat_defaults(monkeypatch,empty):
    from infrastructure.llm.model_factory import create_chat_model
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','https://example.test/v1')
    for key in ['A4B_OLLAMA_NUM_CTX','A4B_OLLAMA_NUM_PREDICT','A4B_OLLAMA_TEMPERATURE']:
        monkeypatch.setenv(key,empty)
    native=OllamaPlanner('final664',temperature=0.2)
    assert (native.num_ctx,native.num_predict,native.temperature)==(16384,4096,0.2)
    model=create_chat_model('','final664',max_tokens=8192,temperature=0.2)
    wire=model._get_request_payload('test')
    assert wire['max_tokens']==8192 and wire['temperature']==0.2
