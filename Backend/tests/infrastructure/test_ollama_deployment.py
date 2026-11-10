import json
import pytest
from pathlib import Path
from agent_builder.design.nodes.direct_conversation import OllamaPlanner
from agent_builder.capabilities.capability_check import make_capability_check


def test_model_only_example_does_not_change_planning_policy():
    path=Path(__file__).resolve().parents[3]/'infrastructure/env/.env.aws.qwen35-9b.example'
    keys={line.split('=',1)[0] for line in path.read_text().splitlines()
          if line.strip() and not line.lstrip().startswith('#') and '=' in line}
    assert not keys & {'A4B_PLANNER_MODE','A4B_PLANNER_PROMPT','A4B_REQUIREMENT_MODE',
                       'A4B_REQUIREMENT_GENERATION_MODE','A4B_VERIFIER_MODE'}
    assert {'A4B_MAIN_MODEL','A4B_CAPABILITY_MODEL'} <= keys


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
        assert p['truncate'] is False and p['shift'] is False
        assert p['options']['num_ctx']==32768
        assert p['options']['num_predict']==4096
        assert p['options']['temperature']==0


def test_native_length_limit_rejects_even_parseable_partial_content(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','https://example.test/v1')
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'message':{'content':'{}'},'done':True,'done_reason':'length'}).encode()
    def open_request(request,timeout):
        calls.append(request);return Response()
    monkeypatch.setattr('urllib.request.urlopen',open_request)
    with pytest.raises(ValueError,match='출력 길이 제한'):
        OllamaPlanner('final664').invoke([{'role':'user','content':'test'}])
    assert len(calls)==1


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
