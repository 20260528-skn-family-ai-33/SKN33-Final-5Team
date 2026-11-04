"""Prevent silent native/operation contract mixing at public boundaries."""
import json

import pytest

from contracts.models import NodeSettings
from tests.workflow_engine.test_workflow import node, bind, edge
from workflow_engine.executors.canonical_operation import validate_settings
from workflow_engine.workflow import workflow_errors


@pytest.mark.parametrize('extra', [
    {'arguments_json':'{"file_id":"table-a"}'},
    {'output_port':'records'},
])
def test_native_reader_rejects_ignored_operation_settings(extra):
    read=node('read','read_table',file_ids=['table-a'],**extra)
    issues=workflow_errors({'entry':'read','nodes':[read],'edges':[]},['read_table'],
                           [{'id':'table-a','kind':'table','columns':['amount']}])
    assert any('실행 계약' in issue for issue in issues),issues


@pytest.mark.parametrize('extra', [
    {'text_find':'another word'}, {'format':'csv'}, {'file_ids':['table-a']},
])
def test_operation_rejects_native_settings_instead_of_ignoring_them(extra):
    operation=node('replace','text_transform',operation='replace',
                   arguments_json=json.dumps({'text':'hello','find':'hello','replacement':'changed'}),**extra)
    with pytest.raises(ValueError,match='실행 계약'):
        validate_settings(operation)


def test_native_library_cannot_persist_operation_arguments_without_operation():
    from workflow_engine.library.node_library import NodeSpec
    settings=NodeSettings.model_validate(node('replace','text_transform',arguments_json='{"text":"hello"}')['settings'])
    with pytest.raises(ValueError,match='실행 계약'):
        NodeSpec.model_validate({'kind':'processing','name':'문자 바꾸기','purpose':'입력 문자열을 변환합니다.',
                                 'tool':'text_transform','settings':settings})


def test_catalog_does_not_advertise_ports_native_executors_ignore():
    from workflow_engine.catalog.registry import PORTS
    assert PORTS['read_table'][0]=={}
    assert set(PORTS['export_result'][0])=={'records','text','answer'}


def test_native_export_does_not_silently_discard_bound_text(tmp_path):
    read=node('read','read_document')
    records=node('extract','extract_information',[bind('documents','read','documents')],fields=['제목'])
    answer=node('answer','compose_answer',[bind('documents','read','documents')])
    out=node('out','export_result',[bind('records','extract','records'),bind('text','answer','text')],format='json')
    workflow={'entry':'read','nodes':[read,records,answer,out],
              'edges':[edge('read','extract'),edge('extract','answer'),edge('answer','out')]}
    issues=workflow_errors(workflow,['read_document','extract_information','compose_answer','export_result'],[])
    assert any('저장할 본문' in issue for issue in issues),issues


def test_native_writer_rejects_unsupported_format_before_save(tmp_path):
    read=node('read','read_table')
    out=node('out','write_result',[bind('table','read','table')],format='markdown')
    workflow={'entry':'read','nodes':[read,out],'edges':[edge('read','out')]}
    issues=workflow_errors(workflow,['read_table','write_result'],[])
    assert any('CSV/JSON' in issue for issue in issues),issues


@pytest.mark.parametrize('predicate', [
    {'column':'amount','op':'gte','value':20},
    {'column':'amount','op':'ge','value':20,'other_column':'limit'},
    {'field':'amount','operator':'gte','value':20},
    {'column':'amount','op':'ge'},
])
def test_record_filter_rejects_mixed_native_condition_contracts(predicate):
    operation=node('filter','filter_sort',operation='filter',
                   arguments_json=json.dumps({'rows':[{'amount':30}],'predicates':[predicate]}))
    with pytest.raises(ValueError,match='predicates'):
        validate_settings(operation)


def test_saved_operation_catalog_does_not_invent_native_count_column(tmp_path):
    from workflow_engine.library.reusable_workflows import catalog
    from tests.workflow_engine.test_workflow import saved
    operation=node('sum','aggregate_table',operation='sum',
                   arguments_json=json.dumps({'table':{'rows':[{'amount':30}]},'group_by':[],
                                               'value_column':'amount','null_policy':'error'}))
    store,identifier,_=saved(tmp_path,{'entry':'sum','nodes':[operation],'edges':[]})
    try:
        agent=store.agent(identifier)
        reusable=next(x for x in catalog(store,agent['model'],agent['credential_id']) if x['id']==identifier)
        output=next(x for x in reusable['outputs'] if x['node_id']=='sum' and x['port']=='table')
        assert output['columns'] is None  # Unknown operation metadata, never a native-mode guess.
    finally:store.close()


def test_finish_cannot_be_saved_with_work_after_termination(tmp_path):
    from tests.design.test_builder import initial,draft
    from infrastructure.persistence.storage import Storage
    from agent_builder.design.nodes.save import make_save_node
    finish=node('finish','finish',instructions='여기서 중단합니다.')
    after=node('after','scalar_calculate',operation='evaluate',
               arguments_json='{"formula":"1+2","variables":{}}')
    workflow={'entry':'finish','nodes':[finish,after],'edges':[edge('finish','after')]}
    tools=['finish','scalar_calculate']
    assert any('종료' in issue for issue in workflow_errors(workflow,tools,[]))
    store=Storage(tmp_path)
    state={**initial(),'draft':{**draft(),'tool_ids':tools,'workflow':workflow},'confirmed':True}
    try:
        with pytest.raises(ValueError):make_save_node(store)(state)
        assert store.agents()==[]
    finally:store.close()


def test_direct_export_execution_does_not_drop_one_of_two_bodies():
    from workflow_engine.executors.output.export_result import execute
    out=node('out','export_result',[bind('records','rows','records'),bind('text','text','text')],format='json')
    state={'outputs':{'rows':{'records':[{'amount':30}]},'text':{'text':'함께 저장할 설명'}}}
    with pytest.raises(ValueError,match='저장할 본문'):execute(out,state,None,{},None)


def test_direct_native_execution_cannot_ignore_operation_arguments():
    from workflow_engine.executors.dispatch import execute_node
    read=node('read','read_table',arguments_json='{"file_id":"wrong"}')
    with pytest.raises(ValueError,match='실행 계약'):
        execute_node(read,{},None,{},None)
