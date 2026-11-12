"""Inventory shown before design: actual source fields on upload, relink and restore."""
import pytest

from documents.analysis.readers import Inspector
from documents.resources.file_inspection import inspect_file


def document(name, blocks, **analysis):
    return {'id': 'source', 'name': name, 'kind': 'document',
            'parser_metadata': {'document_analysis': {'contract_version': 'a4b.document_analysis.v1',
                                                       'blocks': blocks, **analysis}}}


def test_excel_keeps_each_sheet_its_columns_and_native_amount(tmp_path):
    from openpyxl import Workbook
    source = tmp_path / '지점.xlsx'
    book = Workbook()
    first = book.active
    first.title = '서울'
    for sheet, name, amount in [(first,'서울',1300),(book.create_sheet('부산'),'부산',2600)]:
        sheet['A1'],sheet['Z1'],sheet['AA1'] = '지점','주문번호','금액'
        sheet['A2'],sheet['Z2'],sheet['AA2'] = name,'0017',amount
    book.save(source)
    book.close()
    blocks = Inspector(source,tmp_path).run('xlsx')['blocks']
    item = inspect_file(document(source.name,blocks))
    tables = {table['name']: table for table in item['tables']}
    assert set(tables) == {'서울','부산'}
    assert tables['서울']['columns'] == ['지점','주문번호','금액']
    row, = tables['서울']['preview']
    assert (row['지점'], row['주문번호'], str(row['금액'])) == ('서울','0017','1300')
    assert str(tables['부산']['preview'][0]['금액']) == '2600'


def test_word_table_can_be_selected_without_interpreting_prose_as_a_header(tmp_path):
    from docx import Document
    source = tmp_path / '접수.docx'
    doc = Document()
    doc.add_paragraph('담당자: 김담당')
    table = doc.add_table(rows=2, cols=2)
    for row, values in zip(table.rows, [('접수번호','단계'),('0009','검토')]):
        for cell, value in zip(row.cells, values):
            cell.text = value
    doc.save(source)
    blocks = Inspector(source,tmp_path).run('docx')['blocks']
    result = inspect_file(document(source.name,blocks))
    assert result['fields'] == [{'label':'담당자','value':'김담당'}]
    assert result['tables'][0]['columns'] == ['접수번호','단계']
    assert result['tables'][0]['preview'] == [{'접수번호':'0009','단계':'검토'}]


def test_docling_pdf_recovers_cells_by_offsets_not_cell_iteration_order():
    def cell(row, col, value):
        return {'start_row_offset_idx':row,'end_row_offset_idx':row+1,
                'start_col_offset_idx':col,'end_col_offset_idx':col+1,'row_span':1,'col_span':1,'text':value}
    cells = [cell(1,1,'2,600'),cell(0,0,'상품코드'),cell(1,0,'0012'),cell(0,1,'금액')]
    blocks = [{'content_types':['table'],'text':'상품코드 금액\n0012 2,600','native_location':{'page':2},
               'engine':'docling','data':{'cells':cells}}]
    table, = inspect_file(document('스캔.pdf',blocks))['tables']
    assert table['columns'] == ['상품코드','금액']
    assert table['preview'] == [{'상품코드':'0012','금액':'2,600'}]


def test_only_selected_ocr_supplies_readable_suggestions_even_when_flattened_asset_has_more():
    asset = document('명세.png', [
        {'text':'담당자: 박담당\n청구액: 120','selected':True},
        {'text':'담당자: 과거판독\n승인번호: 채택되지않은값','selected':False},
    ])
    asset.update(text='담당자: 과거판독', pages=['승인번호: 채택되지않은값'])
    result = inspect_file(asset)
    assert result['has_readable_text'] and result['fields'] == [
        {'label':'담당자','value':'박담당'},{'label':'청구액','value':'120'}]
    assert all('과거판독' not in text and '채택되지않은값' not in text for text in result['snippets'])


def test_legacy_pages_offer_all_labels_and_do_not_choose_a_conflicting_value():
    result = inspect_file({'id':'old','name':'요청서.pdf','pages':[
        '접수번호： A-012\n담당자: 김담당\nhttps://example.org',
        '접수번호: B-013\n담당자: 김담당\n납기: 4월 1일']})
    by_name = {field['label']:field.get('value') for field in result['fields']}
    assert by_name == {'접수번호':None,'담당자':'김담당','납기':'4월 1일'}


def test_failed_empty_file_and_manually_completed_review_remain_distinct():
    failed = inspect_file({'id':'unreadable','name':'빈사진.jpg','parser_status':'failed','pages':[' ']})
    assert failed['partial'] and not failed['has_readable_text']
    assert not failed['fields'] and not failed['snippets'] and not failed['tables']
    reviewed = document('검토본.png',[{'text':'검토결과: 확인'}],review_required=True)
    reviewed['parser_status'] = 'partial'
    reviewed['parser_metadata']['document_review'] = {'status':'completed'}
    result = inspect_file(reviewed)
    assert result['has_readable_text'] and not result['partial']


@pytest.mark.parametrize('asset,rows',[
    ({'name':'비용.csv','kind':'table','format':'csv','columns':['접수번호','금액'],
      'preview':[{'접수번호':'0012','금액':2600}]},[{'접수번호':'0012','금액':2600}]),
    ({'name':'메모.txt','kind':'document','format':'txt','columns':[],'text':'접수번호: A-501','pages':['접수번호: A-501']},[]),
])
def test_upload_library_attach_and_reopen_return_the_same_grounded_inventory(tmp_path, asset, rows):
    from infrastructure.persistence.ids import uid
    from service.worker import handle

    def call(action, **args):
        return handle({'action':action, 'directory':str(tmp_path), **args},conversation=lambda _:pytest.fail('inspection called the planner'))

    first = call('new_session',model='test',credential_id=uid())
    second = call('new_session',model='test',credential_id=uid())
    uploaded = call('upload',id=first['id'],revision=first['revision'],asset=asset,rows=rows)
    inventory, = uploaded['file_inspection']
    assert inventory['id'] == uploaded['file_ids'][0] and inventory['name'] == asset['name']
    assert inventory['has_readable_text'] and not uploaded['messages']
    if rows:
        assert inventory['columns'] == ['접수번호','금액'] and inventory['preview'] == rows
    else:
        assert inventory['fields'] == [{'label':'접수번호','value':'A-501'}]
    assert not call('session',id=second['id'])['file_inspection']
    relinked = call('attach_asset',id=second['id'],revision=second['revision'],asset_id=inventory['id'])
    assert relinked['file_inspection'] == [inventory]
    assert call('session',id=second['id'])['file_inspection'] == [inventory]


def test_batch_attachment_is_atomic_and_reopens_all_selected_files(tmp_path):
    from infrastructure.persistence.ids import uid
    from service.worker import handle

    def call(action, **data):
        return handle({'action':action,'directory':str(tmp_path),**data})

    session = call('new_session', model='test', credential_id=uid())
    folder = call('folder_create', name='영수증')['folder']
    ids = [call('library_upload', folder_id=folder['id'], rows=[],
                asset={'name':name,'kind':'document','format':'txt','text':'내용'})['asset']['id']
           for name in ('카드.txt','현금.txt')]
    target = {'id':session['id'],'revision':session['revision']}
    with pytest.raises(ValueError):
        call('attach_asset', **target, asset_ids=[ids[0],uid()])
    assert call('session', id=session['id'])['file_ids'] == []
    linked = call('attach_asset', **target, asset_ids=ids)
    assert linked['revision'] == session['revision'] + 1
    assert [file['id'] for file in call('session', id=session['id'])['resources']] == ids
