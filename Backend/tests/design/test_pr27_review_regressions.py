"""Reviewer examples exercise save gates and execution, without model/network calls."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_ledger import make_requirement_check, ledger_fingerprint, plan_digest
from infrastructure.persistence.storage import Storage
from tests.design.test_requirement_coverage import seed, task
from tests.workflow_engine.test_workflow import node, bind, edge, saved
from workflow_engine.runner import run_workflow, execute_node


def seal(state):
    state.update(requirement_fingerprint=ledger_fingerprint(state),
                 requirement_digest=plan_digest(state['requirement_ledger']))
    return state


def test_final_download_cannot_bypass_processed_export(tmp_path):
    state=seed(); wf=state['draft']['workflow']
    wf['nodes'].append(node('raw_out','write_result',[bind('table','read','table')],format='json'))
    wf['edges'].append(edge('out','raw_out'))
    assert coverage_report(state,state['draft'])['issues']
    assert validate_node(state)['status']!='ready'
    state.update(confirmed=True,status='ready',verification_route='continue_review')
    store=Storage(tmp_path)
    try:
        with pytest.raises(ValueError):store.save_agent(state)
        assert not store.agents()
    finally:store.close()


def test_final_download_may_forward_the_proven_artifact_through_finish():
    state=seed();wf=state['draft']['workflow']
    wf['nodes'].append(node('done','finish',[bind('artifact','out','artifact')]))
    wf['edges'].append(edge('out','done'));state['draft']['tool_ids'].append('finish')
    assert not coverage_report(state,state['draft'])['issues']


@pytest.mark.parametrize('finish',[False,True])
def test_actual_final_file_uses_execution_order_and_processed_rows(tmp_path,finish):
    state=seed();wf=state['draft']['workflow']
    # A preliminary raw file is fine when the final downloadable file is processed.
    wf['nodes'].append(node('preview','write_result',[bind('table','read','table')],format='json'))
    wf['edges']=[edge('read','preview'),edge('preview','calc'),edge('calc','filter'),edge('filter','out')]
    if finish:
        wf['nodes'].append(node('done','finish',[bind('artifact','out','artifact')]))
        wf['edges'].append(edge('out','done'));state['draft']['tool_ids'].append('finish')
    assert not coverage_report(state,state['draft'])['issues']
    store,identifier,_=saved(tmp_path,wf)
    try:
        store.add_asset(dict(id='input',name='input.csv',kind='table',columns=['qty']),[{'qty':1},{'qty':3}])
        result=run_workflow(store,identifier,'실행','unused',file_ids=['input'])
        assert result['status']=='succeeded',result.get('error')
        assert json.loads((tmp_path/result['report_path']).read_text(encoding='utf-8'))==[{'qty':3,'amount':30}]
        from workflow_engine.execution_path import artifact_output_ids
        reversed_outputs=dict(reversed(list(result['outputs'].items())))
        assert artifact_output_ids(wf,reversed_outputs)[-1]==('done' if finish else 'out')
    finally:store.close()


def source_case(actual):
    state=seed();request='A.csv를 JSON으로 저장해줘.'
    state['messages']=[dict(role='user',content=request)]
    state['resources']=[dict(id=i,name=i+'.csv',kind='table',columns=['qty']) for i in ('A','B')]
    ledger=dict(tasks=[task('r1','read',file_ids=['A']),task('r2','export',['r1'],format='json')],unresolved=[])
    for t in ledger['tasks']:t['quote']=request
    state['requirement_ledger']=ledger
    state['draft']['document_ids']=['A','B']
    state['draft']['workflow']=dict(entry='read',nodes=[
        node('read','read_table',operation='read_csv',arguments_json=json.dumps({'path':{'asset_id':actual}})),
        node('out','export_result',[bind('records','read','records')],format='json')],edges=[edge('read','out')])
    state['draft']['tool_ids']=['read_table','export_result']
    return seal(state)


@pytest.mark.parametrize('actual,allowed',[('A',True),('B',False)])
def test_operation_read_tracks_its_actual_asset(actual,allowed):
    state=source_case(actual)
    assert (not coverage_report(state,state['draft'])['issues']) is allowed


def test_opaque_file_path_never_inherits_all_attached_asset_ids():
    state=source_case('A')
    state['draft']['workflow']['nodes'][0]['settings']['arguments_json']='{"path":"unproven.csv"}'
    assert coverage_report(state,state['draft'])['issues']


def test_unused_earlier_read_cannot_hide_wrong_operation_source():
    state=source_case('B');wf=state['draft']['workflow']
    wf['nodes'].insert(0,node('unused','read_table',file_ids=['A']))
    wf['entry']='unused';wf['edges'].insert(0,edge('unused','read'))
    assert coverage_report(state,state['draft'])['issues']


@pytest.mark.parametrize('use_processed',[True,False])
def test_xlsx_export_requires_the_requested_processed_table(use_processed,tmp_path):
    state=seed();state['requirement_ledger']['tasks'][3]['spec']['format']='xlsx'
    state['draft']['workflow']['nodes'][-1]=node('out','create_spreadsheet',
        [bind('sheets','filter' if use_processed else 'read','table')],operation='create',
        arguments_json=json.dumps({'sheets':[{'name':'Result','table':{'$binding':'sheets'}}]}))
    state['draft']['tool_ids'][-1]='create_spreadsheet';seal(state)
    assert (not coverage_report(state,state['draft'])['issues']) is use_processed
    if use_processed:
        store,identifier,_=saved(tmp_path,state['draft']['workflow'])
        try:
            store.add_asset(dict(id='data',name='data.csv',kind='table',columns=['qty']),[{'qty':1},{'qty':3}])
            result=run_workflow(store,identifier,'실행','unused',file_ids=['data'])
            assert result['status']=='succeeded',result.get('error')
            from openpyxl import load_workbook
            book=load_workbook(tmp_path/result['report_path'],read_only=True)
            try:assert list(book.active.values)==[('qty','amount'),(3,30)]
            finally:book.close()
        finally:store.close()


def test_compose_forwards_requirement_modes():
    import yaml
    config=yaml.safe_load((Path(__file__).parents[3]/'docker-compose.yml').read_text(encoding='utf-8'))
    env=config['services']['app']['environment']
    assert env['A4B_REQUIREMENT_MODE']=='${A4B_REQUIREMENT_MODE:-off}'
    assert env['A4B_REQUIREMENT_GENERATION_MODE']=='${A4B_REQUIREMENT_GENERATION_MODE:-baseline}'


@pytest.mark.parametrize('decision',[True,False])
def test_only_chosen_branch_requires_replacement_files(tmp_path,decision):
    wf=dict(entry='choose',nodes=[node('choose','condition',condition_operator='semantic',instructions='경로 선택'),
        node('sales','read_table',file_ids=['old-sales']),node('stock','read_table',file_ids=['old-stock']),
        node('sales_out','write_result',[bind('table','sales','table')],format='json'),
        node('stock_out','write_result',[bind('table','stock','table')],format='json')],
        edges=[edge('choose','sales','yes'),edge('choose','stock','no'),
               edge('sales','sales_out'),edge('stock','stock_out')])
    store,identifier,_=saved(tmp_path,wf)
    try:
        chosen='sales' if decision else 'stock'
        for i,name,n in [('old-sales','sales.csv',1),('old-stock','stock.csv',2),('new',chosen+'.csv',99)]:
            store.add_asset(dict(id=i,name=name,kind='table',columns=['amount']),[{'amount':n}])
        agent=store.agent(identifier);agent['config']['document_ids']=['old-sales','old-stock']
        def executor(n,s,storage,config,llm):
            return {'decision':decision} if n['id']=='choose' else execute_node(n,s,storage,config,llm)
        result=run_workflow(store,identifier,'실행','unused',file_ids=['new'],agent_snapshot=agent,
                            executor=executor,llm=object())
        assert result['status']=='succeeded',result.get('error')
        assert json.loads(result['artifact']['content'])==[{'amount':99}]
        assert ('stock' if decision else 'sales') not in result['outputs']
    finally:store.close()


def extraction_state(value=1000):
    state=seed();request='amount가 1,000 이상이면 JSON 저장해줘.'
    state['messages']=[dict(role='user',content=request)]
    wire=dict(tasks=[dict(source_message=0,description='금액 선별',spec=dict(kind='filter',match='all',
        filters=[dict(field='amount',operator='gte',value=value,compare_field=None,value_type='number')]))],unresolved=[])
    for k in ('requirement_ledger','requirement_fingerprint','requirement_digest'):state.pop(k,None)
    return state,wire


@pytest.mark.parametrize('provider,key',[('vllm',''),('openai','fixture-key'),('ollama','fixture-key')])
def test_requirement_extraction_uses_configured_structured_provider(monkeypatch,provider,key):
    state,wire=extraction_state();seen={}
    monkeypatch.setenv('A4B_MAIN_PROVIDER',provider)
    class Structured:
        def with_structured_output(self,schema,**kwargs):
            seen['schema']=schema;return self
        def invoke(self,messages):return wire
    def factory(api_key,model,**kwargs):
        seen['model']=model;seen['key']=api_key;return Structured()
    monkeypatch.setattr('infrastructure.llm.model_factory.create_chat_model',factory)
    result=make_requirement_check('team-qwen',mode='enforce',api_key=key)(state)
    assert result['requirement_route']=='plan',result.get('issues')
    assert seen['model']=='team-qwen'
    assert seen['key']==key
    assert 'id' not in seen['schema']['$defs']['RequiredTask']['properties']


@pytest.mark.parametrize('value,allowed',[('1,000',True),('1,000.00',True),('1,2',False),('1.2.3',False),('NaN',False),('Infinity',False),('bad',False)])
def test_numeric_extraction_returns_validation_failure_instead_of_crashing(value,allowed):
    state,wire=extraction_state(value)
    class Transport:
        calls=0
        def invoke(self,messages):
            self.calls+=1;return dict(content=json.dumps(wire))
    transport=Transport();result=make_requirement_check('fixture',transport,mode='enforce')(state)
    assert (result['requirement_route']=='plan') is allowed
    if not allowed:
        assert result['status']=='validation_failed' and transport.calls==2
    else:
        normalized=result['requirement_ledger']['tasks'][0]['spec']['filters'][0]['value']
        assert ',' not in str(normalized)


def test_numeric_contract_error_can_be_repaired_once():
    state,wire=extraction_state('bad');correct=deepcopy(wire);correct['tasks'][0]['spec']['filters'][0]['value']='1,000'
    class Transport:
        calls=0
        def invoke(self,messages):
            self.calls+=1;return dict(content=json.dumps(wire if self.calls==1 else correct))
    transport=Transport();result=make_requirement_check('fixture',transport,mode='enforce')(state)
    assert result['requirement_route']=='plan' and transport.calls==2
