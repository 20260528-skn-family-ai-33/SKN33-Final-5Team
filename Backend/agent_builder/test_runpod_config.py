from dataclasses import asdict
import json
from pathlib import Path
import subprocess
from unittest.mock import patch
import pytest
from a4b_verifier.client import OllamaConfig,call_qwen,health,http_json
from a4b_verifier.contracts import VerificationInput

ROOT=Path(__file__).parents[2]


def test_runpod_environment_and_no_secret_in_reports(monkeypatch):
    monkeypatch.setenv('A4B_VERIFIER_PROVIDER','vllm')
    monkeypatch.setenv('A4B_VERIFIER_BASE_URL','https://example.invalid/v1')
    monkeypatch.setenv('A4B_VERIFIER_MODEL','Qwen/Qwen3.5-4B')
    monkeypatch.setenv('A4B_VERIFIER_API_KEY','secret-for-test')
    config=OllamaConfig.from_env()
    assert config.api_key=='secret-for-test'
    assert 'secret-for-test' not in repr(config)+json.dumps(asdict(config))
    with patch('a4b_verifier.client.http_json',return_value={'data':[{'id':config.model}]}) as request:
        assert health(config)['model_present']
        assert request.call_args.args[1]=='/models'


def test_runpod_verifier_payload_preserves_schema_and_rejects_truncation():
    root=ROOT/'Backend/integrations/qwen_verifier/examples'
    data=VerificationInput.model_validate_json((root/'inputs/01_ready.json').read_text())
    output=json.loads((root/'model_outputs/01_ready.json').read_text())
    config=OllamaConfig(provider='vllm',base_url='https://example.invalid/v1',model='Qwen/Qwen3.5-4B')
    response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output)}}]}
    with patch('a4b_verifier.client.http_json',return_value=response) as request:
        result,_=call_qwen(data,config)
        assert result.request_status=='ready'
        _,path,payload=request.call_args.args
        assert path=='/chat/completions'
        assert payload['structured_outputs']['json']['type']=='object'
        assert payload['chat_template_kwargs']['enable_thinking'] is False
        assert not payload['stream']
    response['choices'][0]['finish_reason']='length'
    with patch('a4b_verifier.client.http_json',return_value=response):
        with pytest.raises(RuntimeError,match='incomplete_generation'):call_qwen(data,config)


def test_bearer_key_is_only_sent_as_header(monkeypatch):
    monkeypatch.setenv('A4B_VERIFIER_API_KEY','test-secret')
    config=OllamaConfig(provider='vllm',base_url='https://example.invalid/v1')
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):return b'{"data":[]}'
    with patch('a4b_verifier.client.urlopen',return_value=Response()) as opened:
        http_json(config,'/models')
        request=opened.call_args.args[0]
        assert request.get_header('Authorization')=='Bearer test-secret'
        assert request.get_header('User-agent')=='A4B/0.1'
        assert 'test-secret' not in request.full_url


def test_env_file_is_loaded_before_defaults_without_shell_execution(tmp_path,monkeypatch):
    node=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
    if not node.exists():pytest.skip('bundled Node unavailable')
    import os
    env={k:v for k,v in os.environ.items() if not k.startswith('A4B_')}
    config=tmp_path/'runpod.env'
    config.write_text('A4B_MAIN_PROVIDER=vllm\nA4B_MAIN_MODEL="Qwen/Qwen3-8B"\nA4B_MAIN_API_KEY="do-not-print"\nA4B_VERIFIER_MODEL="Qwen/Qwen3.5-4B"\nPORT=3099\n')
    env.update(A4B_ENV_FILE=str(config),A4B_CONFIG_CHECK='1')
    result=subprocess.run([str(node),str(ROOT/'scripts/run-configured.mjs')],env=env,text=True,capture_output=True,check=True)
    actual=json.loads(result.stdout)
    assert actual['mainProvider']=='vllm' and actual['port']=='3099'
    assert actual['mainModel']=='Qwen/Qwen3-8B' and 'do-not-print' not in result.stdout
    env['PORT']='3100'
    result=subprocess.run([str(node),str(ROOT/'scripts/run-configured.mjs')],env=env,text=True,capture_output=True,check=True)
    assert json.loads(result.stdout)['port']=='3100'


def test_lens_style_shared_ollama_url(tmp_path,monkeypatch):
    import os
    node=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
    if not node.exists():pytest.skip('bundled Node unavailable')
    config=tmp_path/'simple.env'
    config.write_text('A4B_LLM_BASE_URL=https://example-pod-11434.proxy.runpod.net/v1\nA4B_LLM_MODEL=qwen3:8b-q4_K_M\nA4B_VERIFIER_MODEL=qwen3.5:4b\n')
    env={k:v for k,v in os.environ.items() if not k.startswith('A4B_')}
    env.update(A4B_ENV_FILE=str(config),A4B_CONFIG_CHECK='1')
    result=subprocess.run([str(node),str(ROOT/'scripts/run-configured.mjs')],env=env,text=True,capture_output=True,check=True)
    actual=json.loads(result.stdout)
    assert actual['mainProvider']==actual['verifierProvider']=='ollama'
    assert actual['sharedOllamaEndpoint'] is True
    for k in ['A4B_VERIFIER_BASE_URL','A4B_OLLAMA_BASE_URL','A4B_VERIFIER_PROVIDER']:
        monkeypatch.delenv(k,raising=False)
    monkeypatch.setenv('A4B_LLM_BASE_URL','https://example-pod-11434.proxy.runpod.net/v1')
    assert OllamaConfig.from_env().base_url=='https://example-pod-11434.proxy.runpod.net'
