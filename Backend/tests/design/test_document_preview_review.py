import pytest
from langgraph.types import Command

from agent_builder.conditions.input_requirements import preflight_execution
from agent_builder.design.document_rows_template import document_rows_applies, document_rows_reply
from agent_builder.design.graph import build_graph
from documents.resources.file_inspection import inspect_file
from infrastructure.persistence.storage import Storage
from tests.design.test_document_row_integrity import (
    EXPECTED, SOURCE, NoModel, block, document, no_planner, output_rows, state,
)
from tests.design.test_document_rows_template import asked_state
from workflow_engine.runner import run_workflow


def preview_message(asset, aliases):
    return f"{asset['name']}에서 {', '.join(aliases)} 항목을 추출해 주세요."


def run_preview(tmp_path, asset, aliases, *, followup=None):
    initial = state(asset, '이 문서 CSV로 저장해줘')
    store = Storage(tmp_path)
    store.add_asset(asset, [])
    graph, connection = build_graph(store, conversation=no_planner, condition_mode='off', capability_mode='off')
    config = {'configurable': {'thread_id': initial['session_id']}}
    try:
        asked = graph.invoke(initial, config)
        assert asked['status'] == 'needs_clarification'
        ready = graph.invoke(Command(resume=dict(type='message', content=preview_message(asset, aliases))), config)
        assert ready['status'] == 'ready', ready.get('issues')
        if followup:
            ready = graph.invoke(Command(resume=dict(type='message', content=followup)), config)
            assert ready['status'] == 'ready', ready.get('issues')
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[asset['id']], llm=NoModel())
        assert result['status'] == 'succeeded', result.get('error')
        return ready['draft']['workflow']['nodes'], output_rows(result['artifact'])
    finally:
        connection.close()
        store.close()


@pytest.mark.parametrize('second_table', ['visible', 'blank-header', 'text-only'])
def test_csv_from_preview_reads_only_the_selected_parser_block(tmp_path, second_table):
    extra_header = ['', *SOURCE[0][1:]] if second_table == 'blank-header' else SOURCE[0]
    extra = block([extra_header, ['H999', '다른 표 품목', '100', '99', '1']], 'other-table', 2)
    if second_table == 'text-only':
        extra['data']['cells'] = []
    asset = document([block(SOURCE, 'selected-table'), extra], name='a.pdf')
    visible = inspect_file(asset)['tables']
    assert len(visible) == (2 if second_table == 'visible' else 1)
    assert visible[0]['block_id'] == 'selected-table'

    nodes, rows = run_preview(tmp_path, asset, ['「표 1」의 품명', '「표 1」의 실입고'])
    assert nodes[0]['settings']['file_ids'] == [asset['id']]
    assert nodes[0]['settings']['document_table_id'] == 'selected-table'
    assert rows == EXPECTED


@pytest.mark.parametrize('scope', ['table', 'all-pages'])
def test_shared_column_names_require_explicit_scope_before_export(tmp_path, scope):
    extra = block([SOURCE[0], ['H999', '다른 표 품목', '100', '99', '1']], 'other-table', 2)
    asset = document([block(SOURCE, 'selected-table'), extra], name='a.pdf')
    initial = state(asset, '이 문서 CSV로 저장해줘')
    store = Storage(tmp_path)
    store.add_asset(asset, [])
    graph, connection = build_graph(store, conversation=no_planner, condition_mode='off', capability_mode='off')
    config = {'configurable': {'thread_id': initial['session_id']}}
    try:
        asked = graph.invoke(initial, config)
        question = asked['pending_questions'][0]
        fields = '품명, 실입고'
        waiting = graph.invoke(Command(resume=dict(
            type='message', content=question['text'] + ' → ' + fields,
            question_answers=[dict(question_id=question['id'], value=fields)])), config)
        assert waiting['status'] == 'needs_clarification'
        assert waiting['draft']['workflow'] is None
        assert '같은 열이 여러 표에 있어요' in waiting['messages'][-1]['content']
        scope_question = waiting['pending_questions'][0]
        assert scope_question['id'] != question['id']
        waiting = graph.invoke(Command(resume=dict(type='message', content=fields, question_id=scope_question['id'])), config)
        assert waiting['status'] == 'needs_clarification'
        assert waiting['pending_questions'][0]['id'] == scope_question['id']
        if scope == 'table':
            answer = preview_message(asset, ['「표 1」의 품명', '「표 1」의 실입고'])
            table_id, expected = 'selected-table', EXPECTED
        else:
            answer = '모든 페이지에서 ' + fields
            table_id, expected = None, EXPECTED + [{'품명': '다른 표 품목', '실입고': '99'}]
        ready = graph.invoke(Command(resume=dict(type='message', content=answer, question_id=scope_question['id'])), config)
        assert ready['status'] == 'ready', ready.get('issues')
        assert ready['draft']['workflow']['nodes'][0]['settings']['document_table_id'] == table_id
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[asset['id']], llm=NoModel())
        assert result['status'] == 'succeeded', result.get('error')
        assert output_rows(result['artifact']) == expected
    finally:
        connection.close()
        store.close()


def test_explicit_all_pages_request_needs_only_the_column_names():
    asset = document([block(SOURCE[:3]), block([SOURCE[0], *SOURCE[3:]], 'continued', 2)])
    current = asked_state('품명, 실입고', request='모든 페이지에서 문서를 CSV로 저장해줘', doc=asset)
    reply = document_rows_reply(current)
    nodes = reply['draft']['workflow']['nodes']
    assert nodes[0]['settings']['document_table_id'] is None
    assert nodes[1]['settings']['fields'] == ['품명', '실입고']


@pytest.mark.parametrize('with_comma', [False, True])
def test_preview_keeps_source_labels_with_commas_and_slashes(tmp_path, with_comma):
    headers = ['품명', '수량/박스']
    table_rows = [['테이프', '4'], ['복사용지', '12']]
    fields = headers.copy()
    if with_comma:
        headers.extend(['금액', '금액, 원'])
        table_rows[0].extend(['30', '32'])
        table_rows[1].extend(['40', '42'])
        fields.append('금액, 원')
    asset = document([block([headers, *table_rows])], name='a.pdf')
    nodes, rows = run_preview(tmp_path, asset, [f'「표 1」의 {field}' for field in fields])
    assert nodes[1]['settings']['fields'] == fields
    assert list(rows[0]) == fields
    assert rows == [{field: row[headers.index(field)] for field in fields} for row in table_rows]


def test_field_addition_preserves_preview_scope(tmp_path):
    extra = block([SOURCE[0], ['other', '다른 표 품목', '999', '999', '0']], 'other-table', 2)
    asset = document([block(SOURCE, 'selected-table'), extra], name='a.pdf')
    nodes, rows = run_preview(tmp_path, asset, ['「표 1」의 품명'], followup='실입고도 추가해줘')
    assert nodes[0]['settings']['document_table_id'] == 'selected-table'
    assert rows == EXPECTED


@pytest.mark.parametrize('replacement', [None, 'other-table'])
def test_save_rejects_a_preview_workflow_with_changed_table_scope(tmp_path, replacement):
    asset = document([block(SOURCE, 'selected-table'), block(SOURCE, 'other-table', 2)], name='a.pdf')
    content = preview_message(asset, ['「표 1」의 품명', '「표 1」의 실입고'])
    current = asked_state(content, request='이 문서 CSV로 저장해줘', doc=asset)
    current.update(document_rows_reply(current))
    current.update(status='ready', confirmed=True)
    current['draft']['workflow']['nodes'][0]['settings']['document_table_id'] = replacement
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError, match='미리보기에서 선택한 원본 문서와 표'):
            store.save_agent(current)
    finally:
        store.close()


def test_run_rebinds_a_same_named_document_by_unique_header_not_table_id(tmp_path):
    asset = document([block(SOURCE, 'selected-table')], name='a.pdf')
    replacement = dict(asset, id='replacement')
    content = preview_message(asset, ['「표 1」의 품명', '「표 1」의 실입고'])
    current = asked_state(content, request='이 문서 CSV로 저장해줘', doc=asset)
    current.update(document_rows_reply(current), status='ready', confirmed=True)
    store = Storage(tmp_path)
    try:
        store.add_asset(asset, [])
        store.add_asset(replacement, [])
        result = run_workflow(store, store.save_agent(current), '실행', '', file_ids=[replacement['id']], llm=NoModel())
        assert result['status'] == 'succeeded', result.get('error')
        assert output_rows(result['artifact']) == EXPECTED
        assert all(record['evidence']['품명']['file_id'] == 'replacement'
                   for record in result['outputs']['extract']['records'])
    finally:
        store.close()


@pytest.mark.parametrize('change', ['data', 'lower-label', 'merge', 'unknown-header', 'ambiguous-header'])
def test_replacement_checks_all_confirmed_header_rows_and_merges(tmp_path, change):
    def asset(identifier, item, label, *, merged=True, marked=True):
        table = block([
            ['품명', '품목 그룹', '원본 그룹', '기타 그룹'],
            ['품명', '수량', label, '보관'],
            [item, '8', '분류', '서울'],
        ], identifier + '-table')
        table['engine'] = 'docling'
        cells = table['data']['cells']
        for cell in cells:
            cell['column_header'] = marked and cell['row'] in (0, 1)
            cell['start_row_offset_idx'] = cell.pop('row')
            cell['start_col_offset_idx'] = cell.pop('column')
        if merged:
            cells[6]['col_span'] = 2
            del cells[7]
        return dict(document([table], name=identifier + '.pdf'), id=identifier)

    original = asset('original', '원본 품목', '발주')
    replacement = asset('replacement', '새 품목', '반품' if change == 'lower-label' else '발주',
                        merged=change != 'merge', marked=change != 'unknown-header')
    if change == 'ambiguous-header':
        unknown = asset('unknown', '다른 품목', '발주', marked=False)
        replacement['parser_metadata']['document_analysis']['blocks'].extend(
            unknown['parser_metadata']['document_analysis']['blocks'])
    current = asked_state(preview_message(original, ['「표 1」의 품명']),
                          request='이 문서 CSV로 저장해줘', doc=original)
    current.update(document_rows_reply(current))
    current['messages'].append({'role': 'user', 'content': '수량도 추가해줘'})
    current.update(document_rows_reply(current), status='ready', confirmed=True)
    store = Storage(tmp_path)
    try:
        for item in (original, replacement):
            store.add_asset(item, [])
        identifier = store.save_agent(current)
        config = store.agent(identifier)['config']
        if change == 'data':
            assert all(item['ready'] for item in preflight_execution(config, store, ['replacement']))
        else:
            with pytest.raises(ValueError, match='하나로 확정'):
                preflight_execution(config, store, ['replacement'])
        run = run_workflow(store, identifier, '실행', '', file_ids=['replacement'], llm=NoModel())
        if change == 'data':
            assert run['status'] == 'succeeded', run.get('error')
            assert output_rows(run['artifact']) == [{'품명': '새 품목', '수량': '8'}]
        else:
            assert run['status'] == 'failed' and '하나로 확정' in run['error']
            assert run.get('artifact') is None
    finally:
        store.close()


@pytest.mark.parametrize(('headers', 'extra_value'), [
    ('<thead><tr><td>품명</td><td>수량</td></tr></thead>', ''),
    ('<tr><th>품명</th><th>수량</th></tr>', ''),
    ('<thead><tr><th>품명</th><th>수량</th><th>품목 그룹</th></tr>'
     '<tr><th>품명</th><th>수량</th><th>분류</th></tr></thead>', '<td>철물</td>'),
])
def test_replacement_can_read_legacy_mineru_header_markers_from_saved_html(tmp_path, headers, extra_value):
    from documents.analysis.tables import from_html

    def asset(identifier, item):
        data = from_html(f'<table>{headers}<tr><th>{item}</th><td>8</td>{extra_value}</tr></table>')
        body_row = data['cells'][-1]['row']
        assert all(cell.get('column_header', False) == (cell['row'] < body_row) for cell in data['cells'])
        text = '\n'.join(cell['text'] for cell in data['cells'])
        for cell in data['cells']:
            cell.pop('column_header', None)
            cell['native_id'] = 'legacy'
        data['cells'].reverse()
        table = block([['품명', '수량'], [item, '8']], identifier + '-table')
        table['data'] = data
        table['text'] = text
        return dict(document([table], name=identifier + '.pdf'), id=identifier)

    original = asset('original', '원본 품목')
    replacement = asset('replacement', '새 품목')
    current = asked_state(preview_message(original, ['「표 1」의 품명', '「표 1」의 수량']),
                          request='이 문서 CSV로 저장해줘', doc=original)
    current.update(document_rows_reply(current), status='ready', confirmed=True)
    store = Storage(tmp_path)
    try:
        for item in (original, replacement):
            store.add_asset(item, [])
        identifier = store.save_agent(current)
        for asset, item in [(original, '원본 품목'), (replacement, '새 품목')]:
            run = run_workflow(store, identifier, '실행', '', file_ids=[asset['id']], llm=NoModel())
            assert run['status'] == 'succeeded', run.get('error')
            assert output_rows(run['artifact']) == [{'품명': item, '수량': '8'}]
    finally:
        store.close()


@pytest.mark.parametrize(('engine', 'pause_at', 'multi_header'), [
    ('mineru', 'documents', True), ('mineru', 'records', True), ('mineru', 'records', False),
    ('docling', 'documents', True), ('docling', 'records', True),
])
def test_resume_legacy_checkpoint_without_exporting_a_header(tmp_path, engine, pause_at, multi_header):
    from agent_builder.design.document_rows_template import _draft
    from documents.analysis.tables import from_html
    from documents.resources.document_resources import TABLE_HEADERS_CHECKED
    from tests.workflow_engine.test_workflow import bind, edge, node
    from workflow_engine.executors.document.extract_information import EVIDENCE_KEYS, MAX_ROWS
    from workflow_engine.executors.document.table_rows import native_records
    from workflow_engine.runner import execute_node

    headings = [['품명', '수량', '품목 그룹']]
    if multi_header:
        headings.append(['품명', '수량', '분류'])
    headers = ''.join('<tr>' + ''.join(f'<th>{value}</th>' for value in row) + '</tr>' for row in headings)
    data = from_html(f'<table><thead>{headers}</thead><tr><td>볼트</td><td>8</td><td>철물</td></tr></table>')
    for cell in data['cells']:
        if engine == 'mineru':
            cell.pop('column_header', None)
        else:
            cell['start_row_offset_idx'] = cell.pop('row')
            cell['start_col_offset_idx'] = cell.pop('column')
    table = block([*headings, ['볼트', '8', '철물']])
    table['data'] = data
    table['engine'] = engine
    asset = document([table], name='legacy.pdf')
    request = '품명과 수량을 추출해서 검토 확인 후 CSV로 저장해 줘.'
    draft = _draft(request, '', ['품명', '수량'], [asset['id']])
    workflow = draft['workflow']
    read, extract, out = workflow['nodes']
    if pause_at == 'documents':
        ask = node('ask', 'ask_user', [bind('documents', 'read', 'documents')], question='이 자료를 사용할까요?')
        extract['bindings'] = [bind('documents', 'ask', 'documents')]
        workflow.update(nodes=[read, ask, extract, out],
                        edges=[edge('read', 'ask'), edge('ask', 'extract'), edge('extract', out['id'])])
    else:
        ask = node('ask', 'ask_user', [bind('records', 'extract', 'records')], question='이 자료를 사용할까요?')
        out['bindings'] = [bind('records', 'ask', 'records')]
        workflow.update(nodes=[read, extract, ask, out],
                        edges=[edge('read', 'extract'), edge('extract', 'ask'), edge('ask', out['id'])])
    draft['tool_ids'].append('ask_user')
    current = state(asset, request)
    current.update(draft=draft, status='ready', confirmed=True)

    def legacy_execute(node, state, store, config, llm):
        if node['tool'] == 'extract_information':
            documents = [{**part, 'table_cells': [{k: v for k, v in cell.items() if k != 'column_header'}
                                                  for cell in part['table_cells']]}
                         for part in state['outputs']['read']['documents']]
            return {'records': native_records(documents, ['품명', '수량'], EVIDENCE_KEYS, MAX_ROWS)}
        output = execute_node(node, state, store, config, llm)
        if node['tool'] == 'read_document':
            for part in output['documents']:
                for key in (TABLE_HEADERS_CHECKED, 'document_chunk_count', 'source_text_length'):
                    part.pop(key, None)
                if engine == 'mineru':
                    for cell in part.get('table_cells', []):
                        cell.pop('column_header', None)
        return output

    store = Storage(tmp_path)
    try:
        store.add_asset(asset, [])
        identifier = store.save_agent(current)
        run = run_workflow(store, identifier, '실행', '', file_ids=[asset['id']], llm=NoModel(), executor=legacy_execute)
        assert run['status'] == 'needs_input', run.get('error')
        if pause_at == 'records':
            assert [row['품명'] for row in run['outputs']['extract']['records']] == (
                ['품명', '볼트'] if multi_header else ['볼트'])
    finally:
        store.close()

    store = Storage(tmp_path)
    try:
        result = run_workflow(store, identifier, '실행', '', run_id=run['id'],
                              answer='네, 이 자료를 사용해 주세요.', llm=NoModel())
        if pause_at == 'records' and multi_header:
            assert result['status'] == 'failed' and '표 머리글이 데이터 행으로 포함' in result['error']
            assert result.get('artifact') is None
        else:
            assert result['status'] == 'succeeded', result.get('error')
            assert output_rows(result['artifact']) == [{'품명': '볼트', '수량': '8'}]
    finally:
        store.close()


def test_blank_column_header_markers_do_not_prove_table_identity():
    from types import SimpleNamespace
    from workflow_engine.executors.sources import source_file_ids

    original = document([block([['', ' '], ['품명', '수량']])])
    replacement = dict(original, id='replacement')
    assets = {item['id']: item for item in (original, replacement)}
    store = SimpleNamespace(asset=assets.__getitem__)
    node = {'id': 'read', 'tool': 'read_document',
            'settings': {'file_ids': [original['id']], 'document_table_id': 'table-1'}}
    with pytest.raises(ValueError, match='원본 문서에서 확인할 수 없습니다'):
        source_file_ids(node, [replacement['id']], store, {'workflow': {'nodes': [node]}})


@pytest.mark.parametrize('empty', [True, False])
def test_run_checks_all_attached_documents_before_export(tmp_path, empty):
    from agent_builder.design.document_rows_template import _draft

    original = document([block([['품명', '수량'], ['볼트', '8']])])
    extra = dict(document([] if empty else [block([['품명', '수량']])], name='두 번째.pdf'), id='extra')
    request = '각 품목에서 품명과 수량을 추출해 CSV로 저장해줘'
    current = state(original, request)
    current.update(draft=_draft(request, '', ['품명', '수량'], [original['id']]), status='ready', confirmed=True)
    store = Storage(tmp_path)
    try:
        for item in (original, extra):
            store.add_asset(item, [])
        identifier = store.save_agent(current)
        run = run_workflow(store, identifier, '실행', '', file_ids=[original['id'], 'extra'], llm=NoModel())
        if empty:
            assert run['status'] == 'failed' and '두 번째.pdf' in run['error']
            assert run.get('artifact') is None
        else:
            assert run['status'] == 'succeeded', run.get('error')
            assert output_rows(run['artifact']) == [{'품명': '볼트', '수량': '8'}]
    finally:
        store.close()


@pytest.mark.parametrize('aliases', [
    ['「표 1」의 품명', '「표 2」의 실입고'],
    ['「표 1」의 품명', '「표 1」의 존재하지 않는 열'],
])
def test_scoped_selection_outside_fixed_recipe_does_not_become_plain_field_names(aliases):
    asset = document([block(SOURCE), block(SOURCE, 'other-table', 2)], name='a.csv.pdf')
    content = preview_message(asset, aliases)
    for current in (asked_state(content, request='이 문서 CSV로 저장해줘', doc=asset), state(asset, content)):
        assert not document_rows_applies(current)
        assert document_rows_reply(current) is None


def test_legacy_document_draft_can_still_add_fields():
    asset = document()
    current = asked_state('품명', request='이 문서 CSV로 저장해줘', doc=asset)
    current.update(document_rows_reply(current))
    for node in current['draft']['workflow']['nodes']:
        node['settings'].pop('document_table_id', None)
    current['messages'].append({'role': 'user', 'content': '실입고도 추가해줘'})
    revised = document_rows_reply(current)
    assert revised['draft']['workflow']['nodes'][1]['settings']['fields'] == ['품명', '실입고']


@pytest.mark.parametrize('answer', ['표 1', '두 번째 표'])
def test_scope_answer_preserves_the_original_fields(answer):
    from agent_builder.design.document_rows_template import TABLE_SCOPE_QUESTION
    asset=document([block(SOURCE,'first'),block(SOURCE,'second',2)])
    initial=asked_state('품명, 실입고')
    initial['resources']=[asset]
    initial['messages'][-1]['content']='품명, 실입고'
    waiting=document_rows_reply(initial)
    assert waiting['pending_questions'][0]['document_fields']==['품명','실입고']
    current={**initial,**waiting}
    current['messages']=waiting['messages']+[dict(role='user',content=answer)]
    result=document_rows_reply(current)
    if answer=='표 1':
        nodes=result['draft']['workflow']['nodes']
        assert nodes[0]['settings']['document_table_id']=='first'
        assert nodes[1]['settings']['fields']==['품명','실입고']
    else:
        assert result['draft']['questions']==[TABLE_SCOPE_QUESTION]
        assert result['pending_questions'][0]['document_fields']==['품명','실입고']


def test_structured_preview_binds_same_named_document_by_identity(tmp_path):
    import json
    from agent_builder.design.document_rows_template import document_row_contract_issues
    first=document([block(SOURCE,'same-block')],name='same.pdf')
    second=document([block([SOURCE[0],['H999','다른 자료','1','99','0']],'same-block')],name='same.pdf');second['id']='second'
    initial=state(first,'이 문서 CSV로 저장해줘');initial['resources']=[first,second]
    store=Storage(tmp_path)
    for asset in initial['resources']:store.add_asset(asset,[])
    graph,connection=build_graph(store,conversation=no_planner,condition_mode='off',capability_mode='off')
    config={'configurable':{'thread_id':initial['session_id']}}
    try:
        asked=graph.invoke(initial,config);question=asked['pending_questions'][0]
        value=json.dumps(dict(document_selection=dict(file_id=first['id'],block_id='same-block',fields=['품명','실입고'])))
        ready=graph.invoke(Command(resume=dict(type='message',content=preview_message(first,['「표 1」의 품명','「표 1」의 실입고']),
                        question_answers=[dict(question_id=question['id'],value=value)])),config)
        assert ready['status']=='ready',ready.get('issues')
        from copy import deepcopy
        bad=deepcopy(ready['draft']);bad['workflow']['nodes'][0]['settings']['file_ids']=[second['id']]
        assert document_row_contract_issues(ready,bad)
        saved=graph.invoke(Command(resume=dict(type='confirm')),config)
        result=run_workflow(store,saved['saved_agent_id'],'실행','',file_ids=[first['id']],llm=NoModel())
        assert result['status']=='succeeded' and output_rows(result['artifact'])==EXPECTED
    finally:
        connection.close();store.close()


@pytest.mark.parametrize('selection', [
    {'file_id':'missing','block_id':'table-1','fields':['품명']},
    {'file_id':'delivery','block_id':'missing','fields':['품명']},
    {'file_id':'delivery','block_id':'table-1','fields':['없는 열']},
])
def test_stale_or_forged_preview_identity_is_rejected_at_answer_boundary(selection):
    import json
    from agent_builder.design.document_rows_template import _ask
    from agent_builder.design.question_answers import question_answers
    s=state(document(),'이 문서 CSV로 저장해줘');s.update(_ask(s,s['messages'][0]['content']))
    identifier=s['pending_questions'][0]['id']
    with pytest.raises(ValueError,match='원본 문서'):
        question_answers([dict(question_id=identifier,value=json.dumps(dict(document_selection=selection)))],s)
