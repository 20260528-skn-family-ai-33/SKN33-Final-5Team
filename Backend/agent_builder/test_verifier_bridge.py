"""Exercise verifier integration through real storage, graph and table execution."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from langgraph.types import Command
from a4b_verifier.service import verify
from agent_builder.graph import build_graph
from agent_builder.models import AgentDraft
from agent_builder.nodes.verification import make_verification_node, resource_document, current_tools
from agent_builder.nodes.save import make_save_node
from agent_builder.storage import Storage, uid
from agent_builder.test_builder import initial, draft
from agent_builder.test_workflow import node, bind, edge
from agent_builder.workflow_runtime import run_workflow

PROMPT='보증금 100만원 이상만 JSON으로 저장해줘.'
EXAMPLES=Path(__file__).parents[1]/'integrations/qwen_verifier/examples'


def seed(store):
    rows=[{'번호':'A','보증금':999999},{'번호':'B','보증금':1000000},{'번호':'C','보증금':2000000}]
    asset=dict(id=uid(),name='transactions.csv',kind='table',format='csv',version=1,
               columns=['번호','보증금'],preview=rows,profile={},row_count=3,hash='test-source')
    store.add_asset(asset,rows)
    plan=dict(filters=[dict(field='보증금',operator='gte',value=1000000,compare_field=None,value_type='number')],
              sort=[],match='all',missing_policy='keep',duplicate_policy='keep',duplicate_field=None,output_format='json')
    wf=dict(entry='read',nodes=[node('read','read_table',file_ids=[asset['id']]),
            node('filter','filter_sort',[bind('table','read','table')],execution_plan=plan),
            node('save','write_result',[bind('table','filter','table')],format='json')],
            edges=[edge('read','filter'),edge('filter','save')])
    raw={**draft(), 'workflow':wf,'execution_plan':plan,'document_ids':[asset['id']],
         'requirements':[dict(description='사용자 조건',source_id='user',quote=PROMPT)]}
    state={**initial(),'messages':[dict(role='user',content=PROMPT)],'resources':[asset], 'repair_count':0}
    return state, AgentDraft.model_validate(raw).model_dump()


def fixture_verifier(data):
    output=json.loads((EXAMPLES/'model_outputs/01_ready.json').read_text())
    return verify(data,mode='fixture',fixture=output)


def test_gate_then_explicit_approval_then_real_execution(tmp_path):
    store=Storage(tmp_path); state,raw=seed(store)
    graph,connection=build_graph(store,conversation=lambda s:dict(draft=deepcopy(raw),repair_count=1),
                                 verifier=fixture_verifier,verifier_mode='enforce')
    cfg={'configurable':{'thread_id':state['session_id']}}
    checked=graph.invoke(state,cfg)
    assert checked['verification_route']=='continue_review'
    assert checked['verification']['execution_allowed'] is False
    assert checked['__interrupt__'][0].value['kind']=='review'
    assert not store.agents()
    saved=graph.invoke(Command(resume={'type':'confirm'}),cfg)
    run=run_workflow(store,saved['saved_agent_id'],'저장한 조건으로 실행',api_key='')
    assert run['status']=='succeeded'
    assert [r['번호'] for r in run['rows']]==['B','C']
    assert store.db.execute('SELECT count(*) FROM builder_verifications').fetchone()[0]==1
    connection.close();store.close()


def test_forged_evidence_blocks_approval_and_is_audited(tmp_path):
    store=Storage(tmp_path); state,raw=seed(store)
    def forged(data):
        output=json.loads((EXAMPLES/'model_outputs/01_ready.json').read_text())
        output['evidence'][0]['quote']='사용자가 말하지 않은 인용'
        return verify(data,mode='fixture',fixture=output)
    graph,c=build_graph(store,conversation=lambda s:dict(draft=raw,repair_count=1),verifier=forged,verifier_mode='enforce')
    result=graph.invoke(state,{'configurable':{'thread_id':state['session_id']}})
    assert result['verification_route']=='manual_review'
    assert result['status']=='validation_failed'
    assert not result.get('__interrupt__') and not store.agents()
    with pytest.raises(ValueError):make_save_node(store)({**result,'confirmed':True})
    c.close();store.close()


def test_shadow_mode_records_failure_without_overriding_native_gate(tmp_path):
    store=Storage(tmp_path);state,raw=seed(store)
    state['draft']=raw
    result=make_verification_node(store,verifier=fixture_verifier,mode='shadow')(state)
    assert not result['verification_enforced'] and 'issues' not in result
    assert result['verification_route']=='continue_review'
    store.close()


def test_partial_parser_output_and_over_budget_document_are_not_ready(tmp_path):
    assert resource_document(dict(id='doc',kind='document',text='본문',warnings=['일부 변환 오류가 있습니다.']))['parse_status']=='partial'
    store=Storage(tmp_path);state,raw=seed(store);state['draft']=raw
    state['resources']=[dict(id='large',kind='document',text='x'*100000,version=1)]
    result=make_verification_node(store,verifier=lambda d:pytest.fail('oversized input reached model'),mode='enforce')(state)
    assert result['verification_route']=='manual_review'
    assert result['verification']['error_code']=='integration_contract_error'
    store.close()


def test_runtime_registry_is_used():
    tools=current_tools()
    assert len(tools)==16
    assert next(t for t in tools if t['id']=='compose_answer')['required_input_ports']==['documents']


def test_original_user_draft_survives_internal_repair(tmp_path):
    store=Storage(tmp_path);state,raw=seed(store);state['draft']=raw
    bridge=make_verification_node(store,verifier=fixture_verifier,mode='shadow')
    first=bridge(state)
    follow={**state,**first,'messages':state['messages']+[dict(role='assistant',content='설정했습니다.'),dict(role='user',content='200만원 이상으로 변경해줘')]}
    second=bridge(follow)
    assert second['verification_previous_draft']==raw
    changed=deepcopy(raw);changed['name']='내부 수정'
    third=bridge({**follow,**second,'draft':changed})
    assert third['verification_previous_draft']==raw
    store.close()


def test_explicit_parser_status_overrides_upload_ready_and_metadata_survives_storage(tmp_path):
    asset=dict(id=uid(),kind='document',name='sample.pdf',text='실제 본문',pages=['실제 본문'],
               processing_status='ready',parser_status='partial',parser_metadata={'parser_code_sha256':{'parser.py':'a'*64},'lighton_report':{'enabled':False}})
    store=Storage(tmp_path)
    store.add_asset(asset,[])
    restored=store.asset(asset['id'])
    assert restored['parser_metadata']==asset['parser_metadata']
    assert resource_document(restored)['parse_status']=='partial'
    restored['parser_status']='failed'
    failed=resource_document(restored)
    assert failed['parse_status']=='failed' and not failed['chunks'] and not failed['tables']
    store.close()


def test_parser_failure_cannot_be_downgraded_by_warnings_or_success_metadata():
    asset=dict(id='failed-doc',kind='document',processing_status='failed',parser_status='succeeded',
               text='검증하면 안 되는 부분 본문',pages=['부분 본문'],warnings=['일부 변환 오류'])
    result=resource_document(asset)
    assert result['parse_status']=='failed' and result['chunks']==[]


def test_unfinished_embedded_text_recovery_is_partial():
    asset=dict(id='partial-doc',kind='document',parser_status='succeeded',text='추출된 부분',
               parser_metadata={'supplement_report':{'errors':[],'deferred_pages':[2]}})
    assert resource_document(asset)['parse_status']=='partial'


@pytest.mark.parametrize('metadata',[[],{'supplement_report':'invalid'}])
def test_malformed_parser_metadata_is_recorded_for_review_not_a_shadow_crash(tmp_path,metadata):
    store=Storage(tmp_path);state,raw=seed(store);state['draft']=raw
    state['resources']=[dict(id='bad',kind='document',text='본문',parser_metadata=metadata)]
    result=make_verification_node(store,verifier=lambda d:pytest.fail('malformed metadata reached model'),mode='shadow')(state)
    assert result['verification_route']=='manual_review'
    assert result['verification']['error_code']=='integration_contract_error'
    assert result['verification_enforced'] is False
    assert store.db.execute('SELECT count(*) FROM builder_verifications').fetchone()[0]==1
    store.close()
