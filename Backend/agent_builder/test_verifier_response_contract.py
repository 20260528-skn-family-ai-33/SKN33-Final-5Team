"""The provider schema rules out impossible verdicts without granting approval."""
import json
import pytest
from a4b_verifier import client
from a4b_verifier.contracts import VerificationInput
from a4b_verifier.validation import output_checks


def sample(with_draft=True, answer=False, previous=False):
    tool={'id':'work','description':'work','enabled':True,'input_ports':{},'output_ports':{},
          'required_input_ports':[],'required_settings':[],'check_scope':'basic'}
    draft={'id':'a','version':'1','workflow':{'entry':'n','nodes':[{'id':'n','tool':'work'}]}}
    return VerificationInput.model_validate({
        'contract_version':'a4b.verifier.v1','request_id':'fixture',
        'user_request':'요청한 작업을 해 주세요.','tools':[tool],
        'candidate_draft':draft if with_draft else None,
        'previous_draft':draft if previous else None,
        'latest_user_answer_id':'answer' if answer else None,
        'messages':[{'id':'answer','role':'user','content':'조건입니다.'}] if answer else []})


@pytest.mark.parametrize('with_draft,answer,previous,draft_choices,reflection_choices',[
    (False,False,False,['not_applicable'],['not_applicable']),
    (True,False,False,['pass','fail','undetermined'],['not_applicable']),
    (True,True,False,['pass','fail','undetermined'],['undetermined']),
    (True,True,True,['pass','fail','undetermined'],['pass','fail','undetermined']),
])
def test_provider_contract_varies_by_actual_input(with_draft,answer,previous,draft_choices,reflection_choices):
    props=client.response_schema(sample(with_draft,answer,previous))['properties']
    assert props['draft_verdict']['enum']==draft_choices
    assert props['reflection_verdict']['enum']==reflection_choices


def test_invalid_quote_remains_a_review_finding_after_contract_change():
    from a4b_verifier.contracts import VerificationOutput
    out=VerificationOutput.model_validate({'request_status':'ready','evidence_status':'sufficient',
        'missing_conditions':[],'unsupported_actions':[],'draft_verdict':'pass',
        'reflection_verdict':'not_applicable',
        'evidence':[{'evidence_id':'user_request','quote':'없는 문구'}],
        'issues':[],'summary':'판정'})
    assert 'invalid_quote' in {f.code for f in output_checks(sample(),out)}


def test_exact_quote_options_use_entire_actual_sources_without_mutating_base_schema():
    data=sample();base=client.response_schema(data)
    constrained=client.exact_quote_schema(data,base)
    assert constrained['$defs']['EvidenceRef']['properties']['quote']['enum']==[data.user_request]
    assert 'enum' not in base['$defs']['EvidenceRef']['properties']['quote']
    data.user_request='가'*1001
    assert client.exact_quote_schema(data,client.response_schema(data)) is None


def test_vllm_receives_contextual_schema(monkeypatch):
    captured={}
    def fake_http(config,path,body):
        captured.update(body)
        output={'request_status':'ready','evidence_status':'sufficient','missing_conditions':[],
            'unsupported_actions':[],'draft_verdict':'undetermined','reflection_verdict':'not_applicable',
            'evidence':[{'evidence_id':'user_request','quote':'요청한 작업'}], 'issues':[],'summary':'보류'}
        return {'model':'fixture','choices':[{'finish_reason':'stop','message':{'content':json.dumps(output)}}]}
    monkeypatch.setattr(client,'http_json',fake_http)
    out,_=client.call_qwen(sample(),client.OllamaConfig(provider='vllm',base_url='http://127.0.0.1/v1'))
    assert out.draft_verdict=='undetermined'
    assert 'not_applicable' not in captured['structured_outputs']['json']['properties']['draft_verdict']['enum']


@pytest.mark.parametrize('formula,expected',[
    ('qty * price',None),('price * qty',None),('qty + price','requested_product_mismatch'),
    ('qty - price','requested_product_mismatch'),('qty * price * 1','requested_product_unresolved'),
])
def test_explicit_product_is_checked_without_guessing_equivalent_expressions(formula,expected):
    from a4b_verifier.validation import code_checks
    data=sample();data.user_request='각 행의 qty와 price를 곱한 total 열을 만듭니다.'
    node=data.candidate_draft.workflow.nodes[0];node.tool='calculate_columns'
    node.settings={'table_operation':'compute','output_column':'total','calculation_formula':formula}
    relevant={f.code for f in code_checks(data) if f.code.startswith('requested_product_')}
    assert relevant==({expected} if expected else set())


def test_product_guard_does_not_apply_to_negative_or_changed_request():
    from a4b_verifier.validation import explicit_product_request
    data=sample();data.user_request='qty와 price를 곱한 total 열을 만들지 마세요.'
    assert explicit_product_request(data) is None
    data.user_request='qty와 price를 곱한 total 열을 만듭니다.'
    from a4b_verifier.contracts import Message
    data.messages=[Message(id='change',role='user',content='덧셈으로 변경')]
    assert explicit_product_request(data) is None
