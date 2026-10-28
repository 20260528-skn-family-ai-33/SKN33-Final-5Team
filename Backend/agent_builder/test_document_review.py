from copy import deepcopy
import concurrent.futures
import uuid
import pytest
from document_analysis.contracts import block, project
from .storage import Storage
from .document_review import view, save, groups_for
from .document_resources import chunks_for_asset


def fixture_asset():
    image=block('image','image',{'frame':1},unit='frame-1',artifact='images/source.png')
    region=block('region','table',{'frame':1},unit='frame-1',parent='image',engine='layout')
    base=block('base','text',{'frame':1},unit='frame-1',parent='region',text='거래 000017 금액 45,000',engine='docling')
    candidate=block('candidate','table',{'frame':1},unit='frame-1',parent='image',text='000017\t45,000',engine='docling',data={'cells':[{'row':0,'column':0,'text':'000017'},{'row':0,'column':1,'text':'45,000'}]})
    candidate.update(selected=False,phase='comparison')
    data=dict(contract_version='a4b.document_analysis.v1',document_id='a'*64,source_sha256='a'*64,source_format='png',primary_processor='layout',
              blocks=[image,region,base,candidate],units=[dict(id='frame-1',native_location={'frame':1})],routes=[],warnings=['table_structure_missing:region'],
              review_required=True,review_reasons=['table_structure_missing:region'],coverage={},status='partial')
    payload=project(data)
    return dict(id=str(uuid.uuid4()),name='실제와 독립된 단위 시험.png',kind='document',format='png',version=1,hash='a'*64,
                **{key:value for key,value in payload.items() if key!='document_analysis'},parser_metadata={'document_analysis':data})


def setup_asset(tmp_path):
    storage=Storage(tmp_path);asset=fixture_asset();storage.add_asset(asset,[])
    return storage,asset


def command(current,action='decide',decision='adopt',**kwargs):
    return dict(expected_revision=current['review']['revision'],request_id=str(uuid.uuid4()),source_sha256=current['review']['source_sha256'],
                analysis_sha256=current['review']['analysis_sha256'],action=action,
                **({'group_id':current['groups'][0]['id'],'decision':decision} if action=='decide' else {}),**kwargs)


def test_adopt_hold_keep_and_completion_preserve_base_and_update_consumers(tmp_path):
    storage,original=setup_asset(tmp_path)
    try:
        initial=view(storage,original['id']);assert len(initial['groups'])==1
        held=save(storage,original['id'],command(initial,decision='hold',note='원본과 비교 중'))
        assert held['review']['status']=='deferred' and held['asset']['text']==original['text']
        with pytest.raises(ValueError,match='미결·보류'):save(storage,original['id'],command(held,'complete',acknowledge=True))
        adopted=save(storage,original['id'],command(held,note='셀과 금액 확인'))
        assert len(adopted['asset']['tables'])==1
        assert [c['block_id'] for c in chunks_for_asset(storage.asset(original['id']))]==['candidate']
        assert storage.raw_asset(original['id'])==original
        with pytest.raises(ValueError,match='주의사항'):save(storage,original['id'],command(adopted,'complete'))
        done=save(storage,original['id'],command(adopted,'complete',acknowledge=True,note='원본 확인 완료'))
        assert done['review']['status']=='completed'
        assert done['asset']['parser_status']=='partial' and done['asset']['parser_metadata']['document_analysis']['review_required']
        assert list(chunks_for_asset(done['asset']))[0]['review_required'] is False
        assert done['asset']['version']==4
        with pytest.raises(ValueError,match='다시 연'):save(storage,original['id'],command(done,decision='keep'))
        reopened=save(storage,original['id'],command(done,'reopen'))
        kept=save(storage,original['id'],command(reopened,decision='keep',note='기본 결과 선택'))
        assert kept['asset']['text']==original['text'] and kept['asset']['tables']==[]
        assert len(kept['history'])==5
        storage.close();storage=Storage(tmp_path)
        assert view(storage,original['id'])['review']==kept['review']
        assert storage.raw_asset(original['id'])==original
    finally:storage.close()


def test_replayed_request_is_idempotent_but_stale_or_forged_choices_fail(tmp_path):
    storage,asset=setup_asset(tmp_path)
    try:
        initial=view(storage,asset['id']);request=command(initial)
        first=save(storage,asset['id'],request);again=save(storage,asset['id'],request)
        assert again['review']==first['review'] and again['replayed'] and len(again['history'])==1
        with pytest.raises(ValueError,match='같은 요청'):save(storage,asset['id'],{**request,'decision':'hold'})
        with pytest.raises(ValueError,match='변경'):save(storage,asset['id'],command(initial,decision='keep'))
        for override in [{'group_id':'unknown'},{'source_sha256':'b'*64},{'analysis_sha256':'b'*64},{'expected_revision':True}]:
            with pytest.raises(ValueError):save(storage,asset['id'],{**command(first),**override})
        assert view(storage,asset['id'])['review']['revision']==1
    finally:storage.close()


def test_parallel_decisions_cannot_overwrite_each_other(tmp_path):
    storage,asset=setup_asset(tmp_path);initial=view(storage,asset['id']);storage.close()
    def choose(decision):
        connection=Storage(tmp_path)
        try:
            try:return save(connection,asset['id'],command(initial,decision=decision))['review']['revision']
            except ValueError:return 'stale'
        finally:connection.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(choose,['adopt','hold']))
    assert sorted(map(str,results))==['1','stale']


def test_pdf_page_scope_keeps_other_pages_and_office_candidate_is_document_scope(tmp_path):
    asset=fixture_asset();data=asset['parser_metadata']['document_analysis']
    data['source_format']='pdf';data['units']=[dict(id='page-1',native_location={'page':1}),dict(id='page-2',native_location={'page':2})]
    base=block('page-one','text',{'page':1},unit='page-1',text='첫 페이지 기본',engine='mineru')
    other=block('page-two','text',{'page':2},unit='page-2',text='둘째 페이지 유지',engine='mineru')
    candidate=block('candidate','text',{'page':1},unit='page-1',text='첫 페이지 후보',engine='docling');candidate.update(selected=False,phase='comparison')
    data['blocks']=[base,other,candidate]
    assert groups_for(asset)[0]['base_block_ids']==['page-one']
    storage=Storage(tmp_path);storage.add_asset(asset,[])
    try:
        current=view(storage,asset['id']);adopted=save(storage,asset['id'],command(current))
        assert adopted['asset']['pages']==['첫 페이지 후보','둘째 페이지 유지']
    finally:storage.close()
    data['source_format']='docx';candidate['phase']='primary'
    assert groups_for(asset)[0]['scope']=={'kind':'document'}
    assert groups_for(asset)[0]['base_block_ids']==['page-one','page-two']


def test_old_design_cannot_be_confirmed_after_document_review_changes(tmp_path):
    from .graph import build_graph
    from .worker import handle
    storage,asset=setup_asset(tmp_path)
    session=storage.new_session('local-test','local-model');session['file_ids']=[asset['id']]
    session=storage.update_session(session,0)
    graph,connection=build_graph(storage,conversation=lambda state:{})
    graph.update_state({'configurable':{'thread_id':session['id']}},{'resources':[asset],'status':'ready'},as_node='resources')
    connection.close()
    save(storage,asset['id'],command(view(storage,asset['id'])))
    storage.close()
    with pytest.raises(ValueError,match='자료 검토 결과가 변경'):
        handle({'action':'confirm','id':session['id'],'revision':session['revision'],'directory':str(tmp_path)},conversation=lambda state:{})
