import pytest
from langgraph.types import Command
from agent_builder.design.graph import build_graph
from documents.resources.document_resources import chunks_for_asset, select_table_parts
from infrastructure.persistence.storage import Storage
from tests.design.test_document_row_integrity import SOURCE, EXPECTED, block, document, state, no_planner
from tests.design.test_document_preview_review import run_preview, preview_message
from workflow_engine.executors.document.table_rows import native_records, TableStructureError


def two_tables():
    first=block(SOURCE,'selected-table')
    first.update(status='succeeded', data={**first['data'], 'table_quality':{'issues':[]}})
    second=block(SOURCE,'other-table')
    second.update(status='review_required',data={**second['data'],'table_quality':{'issues':['low_score']}})
    asset=document([first,second],name='a.pdf')
    analysis=asset['parser_metadata']['document_analysis']
    analysis.update(review_required=True,status='partial',warnings=['table_quality_unresolved:other'],
                    review_reasons=['table_quality_unresolved:other'],
                    review_scopes={'table_quality_unresolved:other':['other-table']})
    return asset


def test_explicit_good_table_can_be_saved_and_executed_without_clearing_document_warning(tmp_path):
    asset=two_tables()
    nodes,rows=run_preview(tmp_path,asset,['「표 1」의 품명','「표 1」의 실입고'])
    assert nodes[0]['settings']['document_table_id']=='selected-table'
    assert rows==EXPECTED
    analysis=asset['parser_metadata']['document_analysis']
    assert analysis['review_required'] and analysis['warnings']
    parts=list(chunks_for_asset(asset))
    # Whole-document requests and explicit selection of the bad table still block.
    for selected in [parts,select_table_parts(parts,'other-table')]:
        with pytest.raises(TableStructureError):native_records(selected,['품명','실입고'],[],100)


@pytest.mark.parametrize('failure',['global','unknown_scope','legacy','local','parent'])
def test_explicit_selection_never_bypasses_global_or_unknown_review(tmp_path,failure):
    asset=two_tables();analysis=asset['parser_metadata']['document_analysis']
    if failure=='legacy':analysis.pop('review_scopes')
    elif failure=='local':analysis['blocks'][0]['status']='review_required'
    elif failure=='parent':
        analysis['blocks'][0]['parent_object_id']='other-table'
    else:
        analysis['warnings'].append('document_error')
        if failure=='global':analysis['review_scopes']['document_error']=None
    initial=state(asset,'이 문서 CSV로 저장해줘')
    store=Storage(tmp_path);store.add_asset(asset,[])
    graph,connection=build_graph(store,conversation=no_planner,condition_mode='off',capability_mode='off')
    config={'configurable':{'thread_id':initial['session_id']}}
    try:
        graph.invoke(initial,config)
        result=graph.invoke(Command(resume=dict(type='message',content=preview_message(
            asset,['「표 1」의 품명','「표 1」의 실입고']))),config)
        assert result['status']=='validation_failed'
        assert result['system_failure']['kind']=='document_table_structure'
        assert not store.agents()
    finally:
        connection.close();store.close()


@pytest.mark.parametrize('route',['remote','local','direct'])
def test_docling_crop_failure_keeps_real_pipeline_table_executable(tmp_path,monkeypatch,route):
    from tests.documents.analysis.test_docling_response_contract import harness,changed
    from tests.documents.analysis.test_docling_review_scope import asset_for
    calls=[]
    def mutate(value):
        calls.append(1)
        return value if len(calls)==1 else changed(value,'second_bad_block')
    payload,_,_=harness(tmp_path,monkeypatch,route,mutate,two_tables=True)
    asset=asset_for(payload)
    good=next(b for b in payload['document_analysis']['blocks'] if b['engine']=='docling')
    nodes,rows=run_preview(tmp_path/'storage',asset,['「표 1」의 Item','「표 1」의 Count'])
    assert nodes[0]['settings']['document_table_id']==good['id']
    assert rows==[{'Item':'Milk','Count':'1'}]
    assert asset['parser_metadata']['document_analysis']['review_required']
