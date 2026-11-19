import sys,copy,json
import csv
import io
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import pytest
from contracts.models import AgentDraft
from documents.resources.document_resources import TABLE_SCOPE_INCOMPLETE
from infrastructure.persistence.storage import Storage; from infrastructure.persistence.ids import uid
from workflow_engine.workflow import workflow_errors
from workflow_engine.runner import run_workflow, execute_node; from workflow_engine.executors.schemas import Facts, Fact
from workflow_engine.executors.schemas import SearchTerms
from tests.design.test_builder import draft, initial
from workflow_engine.executors.legacy_tools.filter_sort import filter_sort_node

SETTINGS={'file_ids':[],'query':None,'fields':[],'instructions':None,'format':None,'question':None,'condition_field':None,'condition_operator':None}
def node(id,tool,bindings=None,**settings):return {'id':id,'tool':tool,'label':id,'bindings':bindings or [],'settings':{**SETTINGS,**settings}}
def bind(input,id,output):return {'input':input,'node_id':id,'output':output}
def edge(a,b,branch='next'):return {'source':a,'target':b,'branch':branch}
def flow(branch=False):
    nodes=[node('read','read_document'),node('extract','extract_information',[bind('documents','read','documents')],fields=['제목','금액'])]
    edges=[edge('read','extract')]
    if branch:
        nodes += [node('check','condition',[bind('records','extract','records')],condition_operator='has_missing'),node('ask','ask_user',[bind('records','check','records')],question='금액 누락을 확인하고 계속할까요?'),node('out_yes','export_result',[bind('records','ask','records'),bind('answer','ask','answer')],format='json'),node('out_no','export_result',[bind('records','check','records')],format='json')]
        edges += [edge('extract','check'),edge('check','ask','yes'),edge('check','out_no','no'),edge('ask','out_yes')]
    else:
        nodes += [node('out','export_result',[bind('records','extract','records')],format='json')];edges += [edge('extract','out')]
    return {'entry':'read','nodes':nodes,'edges':edges}
class FakeLLM:
    def with_structured_output(self,*args,**kwargs):return self
    def invoke(self,messages):
        data=json.loads(messages[-1]['content']);d=data['documents'][0]
        return Facts(facts=[Fact(field='제목',value='테스트',source_id=d['source_id'],quote='제목: 테스트'),Fact(field='금액',value=None,source_id=None,quote=None)])
def saved(tmp_path,w,*,document_text='제목: 테스트\n작성일: 2026-10-12',credential_id=None,**config):
    store=Storage(tmp_path);s=initial();d=draft();d['workflow']=w;d['tool_ids']=list(dict.fromkeys(n['tool'] for n in w['nodes']));d['document_ids']=[]
    s.update(draft=AgentDraft.model_validate({**d,**config}).model_dump(),confirmed=True)
    if credential_id is not None:
        s['credential_id'] = credential_id
    id=store.save_agent(s)
    asset={'id':uid(),'name':'sample.txt','kind':'document','format':'txt','text':document_text,'pages':[document_text],'columns':[]};store.add_asset(asset,[])
    return store,id,asset

def test_contract_rejects_type_missing_port_and_cycles():
    w=flow();tools=[n['tool'] for n in w['nodes']]
    assert not workflow_errors(w,tools,[])
    w['nodes'][1]['bindings'][0]['output']='wrong';assert workflow_errors(w,tools,[])
    w=flow();w['nodes'][-1]['bindings'][0]=bind('records','read','documents');assert any('자료형' in s for s in workflow_errors(w,tools,[]))
    w=flow();w['edges'].append(edge('out','read'));assert any('순환' in s for s in workflow_errors(w,tools,[]))

def test_branch_contract_rejects_path_without_input():
    w=flow(True);tools=[n['tool'] for n in w['nodes']]
    assert not workflow_errors(w,tools,[])
    w['nodes'][-1]['bindings'][0]=bind('records','ask','records')
    assert any('입력' in s for s in workflow_errors(w,tools,[]))

def test_actual_extract_export_and_exact_evidence(tmp_path):
    store,id,asset=saved(tmp_path,flow());run=run_workflow(store,id,'추출', 'unused',file_ids=[asset['id']],llm=FakeLLM())
    assert run['status']=='succeeded';assert len(run['rows'])==2;assert run['rows'][0]['evidence']['quote']=='제목: 테스트';assert run['rows'][1]['value'] is None
    assert json.loads(run['artifact']['content'])[0]['value']=='테스트';assert store.agent(id)['config']['document_ids']==[];store.close()

def test_checkpoint_resume_same_id_after_restart_and_no_forged_evidence(tmp_path):
    store,id,asset=saved(tmp_path,flow(True));run=run_workflow(store,id,'추출','unused',file_ids=[asset['id']],llm=FakeLLM())
    assert run['status']=='needs_input';assert run['question']['node_id']=='ask';run_id=run['id'];store.close()
    store=Storage(tmp_path);second=run_workflow(store,id,'추출','unused',run_id=run_id,answer='누락을 표시하고 계속하세요',llm=FakeLLM())
    assert second['id']==run_id;assert second['status']=='succeeded'
    result=json.loads(second['artifact']['content']);assert result['user_confirmation']=='누락을 표시하고 계속하세요';assert result['records'][1]['value'] is None
    assert len([s for s in second['steps'] if s['node']=='extract'])==1;assert all(s['node']!='out_no' for s in second['steps']);store.close()

@pytest.mark.parametrize('format', ['csv', 'json'])
def test_resumed_export_projects_forwarded_field_values(tmp_path, format):
    class StatusLLM(FakeLLM):
        def invoke(self, messages):
            source = json.loads(messages[-1]['content'])['documents'][0]
            return Facts(facts=[
                Fact(field='status', value='paid', source_id=source['source_id'], quote='status: paid'),
                Fact(field='금액', value=None, source_id=None, quote=None),
            ])

    workflow = flow(branch=True)
    workflow['nodes'][1]['settings']['fields'] = ['status', '금액']
    output = next(n for n in workflow['nodes'] if n['id'] == 'out_yes')
    output['settings'].update(fields=['status'], format=format)
    store, identifier, asset = saved(tmp_path, workflow, document_text='status: paid')
    try:
        initial_run = run_workflow(store, identifier, '추출', 'unused', file_ids=[asset['id']], llm=StatusLLM())
        assert initial_run['status'] == 'needs_input', initial_run.get('error')
    finally:
        store.close()

    store = Storage(tmp_path)
    try:
        confirmation = '누락을 표시하고 계속하세요'
        run = run_workflow(store, identifier, '추출', 'unused', run_id=initial_run['id'],
                           answer=confirmation, llm=StatusLLM())
        assert run['status'] == 'succeeded', run.get('error')
        assert run['outputs']['ask']['records'][0]['status'] == 'verified'
        content = run['artifact']['content']
        if format == 'csv':
            assert list(csv.DictReader(io.StringIO(content.lstrip('\ufeff')))) == [{'status': 'paid'}]
        else:
            assert json.loads(content) == {'records': [{'status': 'paid'}], 'user_confirmation': confirmation}
    finally:
        store.close()


def test_forwarded_generic_records_keep_their_original_columns(tmp_path):
    generic_row = {'file_name': 'sample.txt', 'field': 'kind', 'value': 'paid', 'status': 'provided'}
    source = node('source', 'json_transform', operation='lookup', output_port='records',
                  arguments_json=json.dumps({'value': {'rows': [generic_row]}, 'path': ['rows']}))
    check = node('check', 'condition', [bind('records', 'source', 'records')], condition_operator='empty')
    output = node('out', 'export_result', [bind('records', 'check', 'records')], format='json', fields=['status'])
    workflow = {'entry': 'source', 'nodes': [source, check, output, node('empty', 'finish', instructions='결과 없음')],
                'edges': [edge('source', 'check'), edge('check', 'empty', 'yes'), edge('check', 'out', 'no')]}
    store, identifier, _ = saved(tmp_path, workflow)
    try:
        run = run_workflow(store, identifier, '실행', 'unused', llm=FakeLLM())
        assert run['status'] == 'succeeded', run.get('error')
        assert json.loads(run['artifact']['content']) == [{'status': 'provided'}]
    finally:
        store.close()


def test_export_projects_field_values_from_pinned_workflow(tmp_path):
    child_workflow = flow()
    child_workflow['nodes'].pop()
    child_workflow['edges'].pop()
    credential_id = uid()
    store, child_id, asset = saved(tmp_path, child_workflow, credential_id=credential_id)
    store.close()

    child = node('child', 'saved_workflow', agent_id=child_id, agent_version=1, output_node='extract', output_port='records')
    output = node('out', 'export_result', [bind('records', 'child', 'records')], format='json', fields=['제목'])
    workflow = {'entry': 'child', 'nodes': [child, output], 'edges': [edge('child', 'out')]}
    store, identifier, _ = saved(tmp_path, workflow, credential_id=credential_id)
    try:
        run = run_workflow(store, identifier, '추출', 'unused', file_ids=[asset['id']], llm=FakeLLM())
        assert run['status'] == 'succeeded', run.get('error')
        assert json.loads(run['artifact']['content']) == [{'제목': '테스트'}]
    finally:
        store.close()


class SearchLLM:
    def __init__(self, keyword):
        self.keyword = keyword

    def with_structured_output(self, schema, **_kwargs):
        assert schema is SearchTerms
        return self

    def invoke(self, _messages):
        return SearchTerms(keywords=[self.keyword])


@pytest.mark.parametrize(('omitted', 'number_of_searches', 'preview_scope', 'incomplete'), [
    ('table', 1, False, True),
    ('empty_table', 1, False, True),
    ('table', 2, False, True),
    ('whole_file', 1, False, True),
    ('zero_matches', 2, False, True),
    ('prose', 1, False, False),
    ('table', 1, True, False),
])
def test_row_export_preserves_the_table_scope_of_document_search(
        tmp_path, omitted, number_of_searches, preview_scope, incomplete):
    from agent_builder.design.document_rows_template import _draft
    from tests.design.test_document_row_integrity import block, document, output_rows, state

    keyword = '소켓' if omitted == 'zero_matches' else '볼트'
    kept = block([['품명', '수량'], ['볼트', '2']], identifier='kept')
    if omitted == 'prose':
        other = dict(id='omitted', selected=True, content_types=['text'], text='참고 설명',
                     native_location={'page': 1}, engine='mineru')
    else:
        other_rows = [] if omitted == 'empty_table' else [['품명', '수량'], ['너트', '3']]
        other = block(other_rows, identifier='omitted')
    asset = document([kept] if omitted == 'whole_file' else [kept, other])
    assets = [asset]
    if omitted == 'whole_file':
        assets.append(dict(document([other], name='두 번째 자료.pdf'), id='second-file'))
    request = '문서 검색을 먼저 하고 모든 품목의 품명과 수량을 CSV로 저장해줘.'
    current_draft = _draft(request, request, ['품명', '수량'], [item['id'] for item in assets],
                           document_table_id=kept['id'] if preview_scope else None)
    read, extract, export = current_draft['workflow']['nodes']
    path = [read]
    for index in range(number_of_searches):
        path.append(node(f'search{index}', 'search_document',
                         [bind('documents', path[-1]['id'], 'documents')], query=keyword))
    if omitted == 'zero_matches':
        path.append(node('check_scope', 'condition', [bind('documents', path[-1]['id'], 'documents')],
                         condition_operator='empty'))
        path.append(node('confirm_scope', 'ask_user', [bind('documents', 'check_scope', 'documents')],
                         question='검색 결과를 확인했나요?'))
    extract['bindings'] = [bind('documents', path[-1]['id'], 'documents')]
    path.extend([extract, export])
    edges = [edge(a['id'], b['id'], 'yes' if a['id'] == 'check_scope' else 'next') for a, b in zip(path, path[1:])]
    if omitted == 'zero_matches':
        path.append(node('unexpected', 'finish', instructions='검색 결과 확인'))
        edges.append(edge('check_scope', 'unexpected', 'no'))
    current_draft['workflow'].update(nodes=path, edges=edges)
    current_draft['tool_ids'] = list(dict.fromkeys(n['tool'] for n in path))
    current = dict(state(asset, request), resources=assets, draft=current_draft, status='ready', confirmed=True)
    store = Storage(tmp_path)
    try:
        for item in assets:
            store.add_asset(item, [])
        identifier = store.save_agent(current)
        run = run_workflow(store, identifier, '실행', '', llm=SearchLLM(keyword))
        if omitted == 'zero_matches':
            assert run['status'] == 'needs_input', run.get('error')
            run = run_workflow(store, identifier, '실행', '', run_id=run['id'], answer='확인했어요',
                               llm=SearchLLM(keyword))
        assert all(TABLE_SCOPE_INCOMPLETE not in part for part in run['outputs']['read']['documents'])
        for index in range(number_of_searches):
            search = run['outputs'][f'search{index}']
            selected = search['documents']
            expected_blocks = [] if omitted == 'zero_matches' else ['kept']
            assert [part['block_id'] for part in selected] == expected_blocks
            assert search.get(TABLE_SCOPE_INCOMPLETE, False) is incomplete
            assert all(part.get(TABLE_SCOPE_INCOMPLETE, False) is incomplete for part in selected)
        if omitted == 'zero_matches':
            for identifier in ('check_scope', 'confirm_scope'):
                output = run['outputs'][identifier]
                assert output['documents'] == []
                assert output[TABLE_SCOPE_INCOMPLETE] is True
        if incomplete:
            assert run['status'] == 'failed' and not run.get('artifact')
            assert '일부 표가 제외' in run['error']
        else:
            assert run['status'] == 'succeeded', run.get('error')
            assert output_rows(run['artifact']) == [{'품명': '볼트', '수량': '2'}]
    finally:
        store.close()


def test_row_export_blocks_zero_matches_from_pinned_search(tmp_path):
    from tests.design.test_document_row_integrity import block, document

    read = node('read', 'read_document')
    search = node('search', 'search_document', [bind('documents', 'read', 'documents')], query='소켓')
    child_workflow = {'entry': 'read', 'nodes': [read, search], 'edges': [edge('read', 'search')]}
    credential_id = uid()
    store, child_id, _ = saved(tmp_path, child_workflow, credential_id=credential_id)
    store.close()

    child = node('child', 'saved_workflow', agent_id=child_id, agent_version=1,
                 output_node='search', output_port='documents')
    extract = node('extract', 'extract_information', [bind('documents', 'child', 'documents')],
                   fields=['품명'], extraction_mode='rows')
    output = node('out', 'export_result', [bind('records', 'extract', 'records')], fields=['품명'], format='json')
    parent_workflow = {'entry': 'child', 'nodes': [child, extract, output],
                       'edges': [edge('child', 'extract'), edge('extract', 'out')]}
    store, identifier, _ = saved(tmp_path, parent_workflow, credential_id=credential_id)
    asset = document([block([['품명'], ['볼트']])])
    try:
        store.add_asset(asset, [])
        run = run_workflow(store, identifier, '실행', '', file_ids=[asset['id']], llm=SearchLLM('소켓'))
        assert run['outputs']['child']['documents'] == []
        assert run['outputs']['child'][TABLE_SCOPE_INCOMPLETE] is True
        assert run['status'] == 'failed' and not run.get('artifact')
        assert '일부 표가 제외' in run['error']
    finally:
        store.close()


@pytest.mark.parametrize('extract_before_confirmation', [False, True])
@pytest.mark.parametrize('scope', ['all_tables', 'missing_file', 'preview_table'])
def test_legacy_search_checkpoint_rechecks_scope_before_export(tmp_path, extract_before_confirmation, scope):
    from tests.design.test_document_row_integrity import block, document, output_rows

    kept = block([['품명', '수량'], ['볼트', '2']], identifier='kept')
    other = block([['품명', '수량'], ['너트', '3']], identifier='other')
    blocks = [kept]
    if scope == 'preview_table':
        blocks.append(other)
    elif scope == 'all_tables':
        blocks.append(block([['품명', '수량'], ['볼트 추가', '3']], identifier='second'))
    asset = document(blocks)
    read = node('read', 'read_document')
    if scope == 'preview_table':
        read['settings'].update(file_ids=[asset['id']], document_table_id='kept')
    search = node('search', 'search_document', [bind('documents', 'read', 'documents')], query='볼트')
    extract = node('extract', 'extract_information', fields=['품명', '수량'], extraction_mode='rows')
    check = node('check', 'condition', condition_operator='empty')
    ask = node('ask', 'ask_user', question='검색 결과를 확인했나요?')
    out = node('out', 'export_result', fields=['품명', '수량'], format='csv')
    empty = node('empty', 'finish', instructions='검색 결과 없음')
    if extract_before_confirmation:
        extract['bindings'] = [bind('documents', 'search', 'documents')]
        check['bindings'] = [bind('records', 'extract', 'records')]
        ask['bindings'] = [bind('records', 'check', 'records')]
        out['bindings'] = [bind('records', 'ask', 'records')]
        path = [read, search, extract, check, ask, out]
    else:
        check['bindings'] = [bind('documents', 'search', 'documents')]
        ask['bindings'] = [bind('documents', 'check', 'documents')]
        extract['bindings'] = [bind('documents', 'ask', 'documents')]
        out['bindings'] = [bind('records', 'extract', 'records')]
        path = [read, search, check, ask, extract, out]
    edges = [edge(a['id'], b['id'], 'no' if a is check else 'next') for a, b in zip(path, path[1:])]
    workflow = {'entry': 'read', 'nodes': [*path, empty], 'edges': [*edges, edge('check', 'empty', 'yes')]}
    store, identifier, _ = saved(tmp_path, workflow)

    def legacy_executor(n, current, storage, config, llm):
        # Simulate a checkpoint from before search stored scope metadata and
        # before extraction consulted the workflow's document ancestry.
        result = execute_node(n, current, storage, {**config, 'workflow': {}} if n['id'] == 'extract' else config, llm)
        if n['id'] == 'search':
            result.pop(TABLE_SCOPE_INCOMPLETE)
            result['documents'] = [{k: v for k, v in part.items() if k != TABLE_SCOPE_INCOMPLETE}
                                   for part in result['documents']]
        return result

    try:
        store.add_asset(asset, [])
        file_ids = [asset['id']]
        if scope == 'missing_file':
            missing = dict(document([other]), id='second-file')
            store.add_asset(missing, [])
            file_ids.append(missing['id'])
        paused = run_workflow(store, identifier, '첨부한 자료의 모든 품목을 CSV로 저장해줘', '',
                              file_ids=file_ids, llm=SearchLLM('볼트'), executor=legacy_executor)
        assert paused['status'] == 'needs_input', paused.get('error')
        assert TABLE_SCOPE_INCOMPLETE not in paused['outputs']['search']
    finally:
        store.close()

    store = Storage(tmp_path)
    try:
        run = run_workflow(store, identifier, '실행', '', run_id=paused['id'], answer='확인했어요', llm=SearchLLM('볼트'))
        if scope == 'missing_file':
            assert run['status'] == 'failed' and not run.get('artifact')
            assert '일부 표가 제외' in run['error']
            assert run['steps'][-1]['node'] == ('out' if extract_before_confirmation else 'extract')
        else:
            assert run['status'] == 'succeeded', run.get('error')
            expected = [{'품명': '볼트', '수량': '2'}]
            if scope == 'all_tables':
                expected.append({'품명': '볼트 추가', '수량': '3'})
            assert output_rows(run['artifact']) == expected
    finally:
        store.close()


def test_no_missing_branch_skips_question(tmp_path):
    store,id,asset=saved(tmp_path,flow(True))
    def executor(n,s,st,c,l):
        if n['tool']=='extract_information':return {'records':[{'file_name':'sample','field':'금액','value':'10','status':'verified','evidence':None}]}
        return execute_node(n,s,st,c,l)
    run=run_workflow(store,id,'실행','unused',file_ids=[asset['id']],llm=FakeLLM(),executor=executor)
    assert run['status']=='succeeded';assert not run['question'];assert all(s['node']!='ask' for s in run['steps']);store.close()

def test_read_only_completion_and_failed_node_log(tmp_path):
    w={'entry':'read','nodes':[node('read','read_document')],'edges':[]};store,id,asset=saved(tmp_path,w)
    run=run_workflow(store,id,'읽기','unused',file_ids=[asset['id']],llm=FakeLLM());assert run['status']=='succeeded';assert run['artifact'] is None
    failed=run_workflow(store,id,'읽기','unused',llm=FakeLLM());assert failed['status']=='failed';assert failed['steps'][-1]['node']=='read';store.close()

def test_fake_quote_rejected(tmp_path):
    class Bad(FakeLLM):
        def invoke(self,messages):
            data=json.loads(messages[-1]['content']);d=data['documents'][0]
            return Facts(facts=[Fact(field='제목',value='가짜',source_id=d['source_id'],quote='가짜 원문')])
    store,id,asset=saved(tmp_path,flow());run=run_workflow(store,id,'실행','unused',file_ids=[asset['id']],llm=Bad());assert all(r['value'] is None and r['evidence'] is None for r in run['rows']);store.close()

def test_missing_keep_does_not_bypass_other_filter_and_exclude_alone():
    def ctx():return {'rows':[{'stock':None,'country':'US'},{'stock':2,'country':'KR'}],'columns':['stock','country'],'file_id':'a','trace':[]}
    c=ctx();filter_sort_node(c,[{'field':'stock','operator':'lt','value':10,'value_type':'number'},{'field':'country','operator':'eq','value':'KR','value_type':'text'}],[],missing_policy='keep');assert c['rows']==[{'stock':2,'country':'KR'}]
    c=ctx();filter_sort_node(c,[],[],missing_policy='exclude');assert len(c['rows'])==1

def test_passthrough_port_must_exist_in_actual_input():
    w=flow(True);w['nodes'][3]['bindings'].append(bind('text','check','text'))
    assert any('전달하지 않는' in s for s in workflow_errors(w,list(dict.fromkeys(n['tool'] for n in w['nodes'])),[]))

def test_semantic_branch_uses_real_user_reply(tmp_path):
    from workflow_engine.executors.schemas import Decision, InputAssessment
    class ReplyLLM:
        def with_structured_output(self,schema,**kw):self.schema=schema;return self
        def invoke(self,messages):
            data=json.loads(messages[-1]['content'])
            if self.schema==InputAssessment:return InputAssessment(valid=data['answer'] in ['해결됐어요','미해결이에요'],clarification='해결 여부를 알려주세요.')
            return Decision(decision=data['inputs']['answer']=='해결됐어요',reason='직원 응답')
    w={'entry':'ask','nodes':[node('ask','ask_user',question='해결됐나요?',instructions='해결 여부가 명확해야 합니다.'),node('check','condition',[bind('answer','ask','answer')],condition_operator='semantic',instructions='직원이 해결됐다고 답했는가'),node('yes','finish',instructions='해결 완료'),node('no','finish',instructions='미해결 문의 정리 필요')],'edges':[edge('ask','check'),edge('check','yes','yes'),edge('check','no','no')]}
    store,id,_=saved(tmp_path,w);first=run_workflow(store,id,'실행','unused',llm=ReplyLLM());assert first['status']=='needs_input'
    second=run_workflow(store,id,'실행','unused',run_id=first['id'],answer='잘 모르겠어요',llm=ReplyLLM());assert second['status']=='needs_input';assert not second.get('outputs',{}).get('check')
    final=run_workflow(store,id,'실행','unused',run_id=first['id'],answer='해결됐어요',llm=ReplyLLM());assert final['status']=='succeeded';assert final['answer']=='해결 완료';assert all(s['node']!='no' for s in final['steps']);store.close()

def test_business_deep_agent_tool_registration_and_quote_validation(tmp_path,monkeypatch):
    import workflow_engine.executors.schemas as module
    from workflow_engine.executors import agent_factory
    class Business:
        def invoke(self,*args,**kwargs):return {'structured_response':module.GroundedAnswer(text='자료 안내',citations=[module.Citation(source_id='doc:1:0',quote='실제 원문')])}
    def create(**kwargs):
        assert kwargs['tools'][0].name=='inspect_evidence'
        assert kwargs['tools'][0].invoke({})[0]['quote']=='실제 원문'
        return Business()
    monkeypatch.setattr(agent_factory,'create_deep_agent',create)
    class Review:
        def with_structured_output(self,*args,**kwargs):return self
        def invoke(self,*args):return module.GroundedAnswer(text='자료 안내',citations=[module.Citation(source_id='doc:1:0',quote='실제 원문')])
    n=node('answer','compose_answer',[bind('documents','read','documents')])
    result=execute_node(n,{'request':'질문','outputs':{'read':{'documents':[{'source_id':'doc:1:0','file_id':'doc','file_name':'a.pdf','page':1,'offset':0,'text':'실제 원문'}]}}},None,{'instructions':'근거 답변'},Review())
    assert result['text']=='자료 안내';assert result['evidence'][0]['quote']=='실제 원문'


def test_original_quote_normalizes_layout_only():
    from documents.resources.quotes import original_quote
    text='네트워크 관리\n자에게 문의하십시오.'
    assert original_quote(text,'네트워크 관리자에게 문의하십시오.')==text
    assert original_quote(text,'네트워크 설정을 변경하십시오.') is None


def test_explicit_empty_filter_is_not_overridden_by_global_exclusion():
    c={'rows':[{'a':None},{'a':1}],'columns':['a'],'trace':[],'file_id':'a'}
    filter_sort_node(c,[{'field':'a','operator':'is_empty','value':None,'compare_field':None,'value_type':'text'}],[],missing_policy='exclude')
    assert c['rows']==[{'a':None}]


def test_backwards_data_binding_is_rejected_without_recursion():
    w={'entry':'read','nodes':[node('read','read_table'),node('group','aggregate_table',[bind('table','filter','table')],fields=['status']),node('filter','filter_sort',[bind('table','group','table')])],'edges':[edge('read','group'),edge('group','filter')]}
    assert workflow_errors(w,['read_table','aggregate_table','filter_sort'],[{'id':'a','kind':'table','columns':['status']}])


def test_receipt_style_mistakes_explain_the_wrong_link_and_the_document_path():
    # The draft Qwen produced for "영수증 이미지를 엑셀로 정리": documents wired to a table-only
    # tool and an edge to a finish node that does not exist.
    w={'entry':'read','nodes':[node('read','read_document'),node('save','write_result',[bind('table','read','table')],format='csv')],
       'edges':[edge('read','save'),edge('save','finish')]}
    issues=workflow_errors(w,['read_document','write_result'],[])
    missing=next(s for s in issues if s.startswith('존재하지 않는 노드에 연결했습니다'))
    assert 'save → finish' in missing and '없는 노드: finish' in missing
    port=next(s for s in issues if s.startswith('입출력 포트가 존재하지 않습니다: save'))
    assert 'read(read_document)의 출력은 documents' in port and 'table 자료가 필요' in port
    assert 'extract_information(records) → export_result' in port
    extracted={'entry':'read','nodes':[node('read','read_document'),node('extract','extract_information',[bind('documents','read','documents')],fields=['상품명','금액']),
               node('save','write_result',[bind('table','extract','records')],format='csv')],'edges':[edge('read','extract'),edge('extract','save')]}
    tools3=['read_document','extract_information','write_result']
    assert any(s.startswith('입출력 자료형이 맞지 않습니다: save') and 'write_result 대신 export_result(records)' in s for s in workflow_errors(extracted,tools3,[]))
    extracted['nodes'][2]['bindings']=[bind('table','extract','table')]
    assert any(s.startswith('입출력 포트가 존재하지 않습니다: save') and 'write_result 대신 export_result(records)' in s for s in workflow_errors(extracted,tools3,[]))
    w['nodes'][1]['bindings']=[bind('rows','read','documents')]
    assert any('write_result에는 rows 입력이 없습니다. 받을 수 있는 입력: table' in s for s in workflow_errors(w,['read_document','write_result'],[]))


def test_node_port_edge_references_resolve_only_to_a_real_node_port():
    """Real Deep Agent draft: edges 'read_table_1:table' → 'filter_sort_1:table'."""
    from workflow_engine.workflow import normalize_connections
    nodes=[node('read_table_1','read_table',file_ids=['f']),node('filter_sort_1','filter_sort',[bind('table','read_table_1','table')]),
           node('write_result_1','write_result',[bind('table','filter_sort_1','table')],format='json')]
    wf={'entry':'read_table_1','nodes':copy.deepcopy(nodes),
        'edges':[edge('read_table_1:table','filter_sort_1:table'),edge('filter_sort_1:table','write_result_1:table')]}
    assert workflow_errors(wf,['read_table','filter_sort','write_result'],[{'id':'f','kind':'table','columns':['거래ID','비용']}])
    fixed=normalize_connections(copy.deepcopy(wf))
    assert fixed['edges']==[edge('read_table_1','filter_sort_1'),edge('filter_sort_1','write_result_1')]
    assert not workflow_errors(fixed,['read_table','filter_sort','write_result'],[{'id':'f','kind':'table','columns':['거래ID','비용']}])
    # Unknown node, a port the node does not own, or no port: left for validation to reject.
    # (write_result has no binding here, so no single binding chain can replace the edges.)
    unchained=copy.deepcopy(nodes);unchained[2]['bindings']=[]
    for source in ('missing_1:table','read_table_1:artifact','read_table_1:','start'):
        kept=normalize_connections({'entry':'read_table_1','nodes':copy.deepcopy(unchained),'edges':[edge(source,'filter_sort_1')]})
        assert kept['edges'][0]['source']==source


def test_dotted_node_port_references_resolve_like_colon_ones():
    """Real Deep Agent draft (repro run 6): edges 'read_table_1.table' → 'filter_sort_1.table'."""
    from workflow_engine.workflow import normalize_connections
    nodes=[node('read_table_1','read_table',file_ids=['f']),node('filter_sort_1','filter_sort',[bind('table','read_table_1','table')]),
           node('write_result_1','write_result',[bind('table','filter_sort_1','table')],format='json')]
    wf={'entry':'read_table_1','nodes':copy.deepcopy(nodes),
        'edges':[edge('read_table_1.table','filter_sort_1.table'),edge('filter_sort_1.table','write_result_1.table')]}
    fixed=normalize_connections(copy.deepcopy(wf))
    assert fixed['edges']==[edge('read_table_1','filter_sort_1'),edge('filter_sort_1','write_result_1')]
    assert not workflow_errors(fixed,['read_table','filter_sort','write_result'],[{'id':'f','kind':'table','columns':['거래ID','비용']}])
    unchained=copy.deepcopy(nodes);unchained[2]['bindings']=[]
    for source in ('missing_1.table','read_table_1.artifact','read_table_1.','read_table_1.table.x'):
        kept=normalize_connections({'entry':'read_table_1','nodes':copy.deepcopy(unchained),'edges':[edge(source,'filter_sort_1')]})
        assert kept['edges'][0]['source']==source


def _drafted(nodes,edges,entry):
    """Real post-fix drafts (live repro after G1-G4): correct bindings, broken edges."""
    names=[n for n,_ in nodes];chain=[]
    for i,(id,tool) in enumerate(nodes):
        extra={'file_ids':['f']} if tool=='read_table' else {'format':'json'} if tool=='write_result' else {}
        port={'filter_sort':'table','write_result':'table','finish':'artifact'}.get(tool)
        src_out={'read_table':'table','filter_sort':'table','write_result':'artifact'}
        bindings=[bind(port,names[i-1],src_out[nodes[i-1][1]])] if i else []
        chain.append(node(id,tool,bindings,**extra))
    return {'entry':entry,'nodes':chain,'edges':[edge(a,b) for a,b in edges]}


@pytest.mark.parametrize('ids,edges,entry,duplicate',[
    (['read_table_1','filter_sort_1','write_result_1','finish_1'],[('start','read_table_1'),('read_table_1','filter_sort_1'),('filter_sort_1','write_result_')],'read_table_1',False),
    (['read_table_node','filter_sort_node','write_result_node','finish_node'],[('read_table_node','filter_sort_node'),('filter_sort_node','write_result_node')],'read_table_node',False),
    (['read_table_node_1','filter_sort_node_2','write_result_node_3','finish_node_4'],[('read_table_node_1','filter_sort_node_2'),('filter_sort_node_0','write_result_node_3')],'read_table_node_1',True),
    (['read_table_1','filter_sort_1','write_result_1','finish_1'],[('read_table_1','filter_sort_1'),('filter_sort_1','write_result_1'),('write_result_1','finish_1')],'read_table_1',True)])
def test_unusable_edges_follow_an_unbranched_binding_chain(ids,edges,entry,duplicate):
    from workflow_engine.workflow import normalize_connections
    tools=['read_table','filter_sort','write_result','finish']
    wf=_drafted(list(zip(ids,tools)),edges,entry)
    if duplicate:wf['nodes'][3]['bindings']*=2  # exact repeat seen in the draft
    tool_ids=tools
    assert workflow_errors(wf,tool_ids,[{'id':'f','kind':'table','columns':['비용']}])
    fixed=normalize_connections(copy.deepcopy(wf))
    assert fixed['edges']==[edge(a,b) for a,b in zip(ids,ids[1:])] and fixed['entry']==ids[0]
    assert len(fixed['nodes'][3]['bindings'])==1
    assert not workflow_errors(fixed,tool_ids,[{'id':'f','kind':'table','columns':['비용']}])


def test_usable_branched_or_ambiguous_workflows_keep_their_edges():
    from workflow_engine.workflow import normalize_connections
    ok=_drafted([('r','read_table'),('s','filter_sort'),('w','write_result')],[('r','s'),('s','w')],'r')
    assert normalize_connections(copy.deepcopy(ok))==ok
    branched=copy.deepcopy(ok);branched['nodes'].append(node('c','condition',[bind('table','s','table')],condition_operator='empty'))
    branched['edges']=[edge('r','s'),edge('s','zzz')]
    assert normalize_connections(copy.deepcopy(branched))['edges']==branched['edges']
    fanout=copy.deepcopy(ok);fanout['nodes'].append(node('w2','write_result',[bind('table','s','table')],format='csv'))
    fanout['edges']=[edge('r','s'),edge('s','missing')]
    assert normalize_connections(copy.deepcopy(fanout))['edges']==fanout['edges']


@pytest.mark.parametrize('failure', ['report', 'review_report', 'review_guard'])
def test_report_save_failure_keeps_monitoring_diagnostics(tmp_path, monkeypatch, failure):
    from workflow_engine.lifecycle.run_lifecycle import RunLifecycle
    monkeypatch.setenv('A4B_RELEASE', 'review-test-release')
    from workflow_engine import runner
    publish = RunLifecycle.publish
    def fail_publish(self, run_id, token, target, content):
        if failure == 'report' or (failure == 'review_report' and str(target).endswith('.review.json')):
            raise OSError('private filesystem detail')
        return publish(self, run_id, token, target, content)
    monkeypatch.setattr(RunLifecycle, 'publish', fail_publish)
    if failure != 'report':
        monkeypatch.setattr(runner, 'collect_reviews', lambda outputs: [{'test': True}])
        monkeypatch.setattr(runner, 'is_review_report_artifact', lambda *args: failure != 'review_guard')
    store, identifier, asset = saved(tmp_path, flow())
    try:
        run = run_workflow(store, identifier, '추출', 'unused', file_ids=[asset['id']], llm=FakeLLM())
        assert run['status'] == 'failed'
        assert run['diagnostics']['trace_id'] == run['id']
        assert run['diagnostics']['model']
        assert run['diagnostics']['release'] == 'review-test-release'
        assert 'private filesystem detail' not in json.dumps(run['diagnostics'])
        assert store.test(run['id'])['diagnostics'] == run['diagnostics']
    finally:
        store.close()


@pytest.mark.parametrize('late_failure', [False, True])
def test_retry_resets_previous_diagnostics(tmp_path, monkeypatch, late_failure):
    from workflow_engine.lifecycle.run_lifecycle import RunLifecycle
    store, identifier, asset = saved(tmp_path, flow())
    class RemoteError(Exception):
        status_code = 503
        def errors(self, **kwargs):
            return [{'loc': ('old',), 'type': 'missing'}]
    def broken(*args):
        raise RemoteError('old provider failure')
    try:
        monkeypatch.setenv('A4B_RELEASE', 'old-release')
        first = run_workflow(store, identifier, '추출', 'unused', file_ids=[asset['id']], llm=FakeLLM(), executor=broken)
        assert first['diagnostics']['http_status'] == 503
        assert first['diagnostics']['validation']
        monkeypatch.setenv('A4B_RELEASE', 'new-release')
        if late_failure:
            def no_disk(*args, **kwargs):
                raise OSError('disk full')
            monkeypatch.setattr(RunLifecycle, 'publish', no_disk)
        result = run_workflow(store, identifier, '추출', 'unused', run_id=first['id'], retry=True, llm=FakeLLM())
        assert result['status'] == ('failed' if late_failure else 'succeeded')
        if late_failure:
            assert result['diagnostics']['release'] == 'new-release'
            assert 'http_status' not in result['diagnostics']
            assert result['diagnostics']['validation'] == []
        else:
            assert not result.get('diagnostics')
        assert store.test(result['id']).get('diagnostics') == result.get('diagnostics')
    finally:
        store.close()


def test_multiple_exports_publish_one_archive_and_resume_after_disk_error(tmp_path, monkeypatch):
    from zipfile import ZipFile
    from workflow_engine.lifecycle.run_lifecycle import RunLifecycle

    workflow = flow()
    workflow['nodes'].append(node('csv', 'export_result', [bind('records', 'extract', 'records')], format='csv'))
    workflow['edges'].append(edge('out', 'csv'))
    store, identifier, asset = saved(tmp_path, workflow)
    try:
        with monkeypatch.context() as patch:
            def no_disk(*args, **kwargs):
                raise OSError('disk full')
            patch.setattr(RunLifecycle, 'publish', no_disk)
            failed = run_workflow(store, identifier, '추출', 'unused', file_ids=[asset['id']], llm=FakeLLM())
        assert failed['status'] == 'failed'
        assert failed['answer'] == failed['error']
        assert failed['artifact'] is None and failed['report_path'] is None

        def must_use_checkpoint(*args):
            pytest.fail('저장 실패 후에는 완료된 단계를 다시 실행하지 않습니다.')
        run = run_workflow(store, identifier, '추출', 'unused', run_id=failed['id'],
                           retry=True, llm=FakeLLM(), executor=must_use_checkpoint)
        assert run['status'] == 'succeeded' and run['id'] == failed['id']
        assert run['artifact']['mime'] == 'application/zip'
        with ZipFile(tmp_path / run['report_path']) as archive:
            assert archive.namelist() == ['001/추출_결과.json', '002/추출_결과.csv']
            assert json.loads(archive.read(archive.namelist()[0]))[0]['value'] == '테스트'
            assert '테스트' in archive.read(archive.namelist()[1]).decode('utf-8-sig')
    finally:
        store.close()
