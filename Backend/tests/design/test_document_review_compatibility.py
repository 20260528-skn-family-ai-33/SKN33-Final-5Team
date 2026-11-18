import pytest
from documents.resources import document_resources as resources
from infrastructure.persistence.storage import Storage
from tests.design.test_document_row_integrity import block, document
from workflow_engine.executors.document.row_contract import native_rows


@pytest.mark.parametrize('change',['none','text','missing','scope'])
def test_legacy_common_value_scope_key_is_optional_only_when_absent(tmp_path,change):
    asset=document([block([['품명'],['볼트']]),dict(id='date',selected=True,content_types=['text'],
        text='거래일자: 2026-11-18',native_location={'page':1},engine='mineru')])
    parts=[{k:v for k,v in part.items() if k not in (
        'document_chunk_count','source_text_length','selected_table_review_required')}
        for part in resources.chunks_for_asset(asset)]
    if change=='text':parts[-1]['text']='거래일자: 2026-11-19'
    if change=='missing':parts.pop()
    if change=='scope':parts[0]['selected_table_review_required']=True
    store=Storage(tmp_path)
    try:
        store.add_asset(asset,[])
        if change=='none':
            rows=native_rows(parts,['품명','거래일자'],[],100,storage=store)
            assert rows[0]['거래일자']=='2026-11-18'
        else:
            with pytest.raises(ValueError):native_rows(parts,['품명','거래일자'],[],100,storage=store)
    finally:store.close()


@pytest.mark.parametrize('scope',['global','local','unknown'])
def test_large_document_computes_common_scope_once(monkeypatch,scope):
    blocks=[dict(id=f't{i}',selected=True,content_types=['text'],text='text',
        native_location={'page':1},engine='mineru') for i in range(6000)]
    asset=document(blocks)
    analysis=asset['parser_metadata']['document_analysis']
    analysis.update(review_required=True,warnings=['warning'],review_reasons=['warning'],
        review_scopes={'warning':None if scope=='global' else ['t1'] if scope=='local' else ['unknown']})
    original=resources.document_review_scope;calls=[]
    def counted(value):calls.append(1);return original(value)
    monkeypatch.setattr(resources,'document_review_scope',counted)
    chunks=list(resources.chunks_for_asset(asset))
    assert len(chunks)==6000 and len(calls)==1
    assert chunks[1]['selected_table_review_required']
    assert chunks[0]['selected_table_review_required'] is (scope!='local')
