"""Contract/fault tests with explicit extraction fixtures, NOT LLM accuracy."""
import json
from copy import deepcopy

import pytest
from langgraph.types import Command

from agent_builder.condition_readiness import (
    input_issues, judge_conditions, plan_issues, prepare_conditions, source_catalog, validate_conditions,
)
from agent_builder.graph import build_graph
from agent_builder.models import AgentDraft
from agent_builder.nodes.condition_check import make_condition_check
from agent_builder.nodes.save import make_save_node
from agent_builder.storage import Storage
from agent_builder.test_builder import initial, draft
from agent_builder.test_workflow import node, bind, edge
from agent_builder.workflow_runtime import run_workflow


def state(text='비용이 100만원 초과인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'):
    return {**initial(), 'messages': [{'role': 'user', 'content': text}], 'resources': []}


def proposal(s, values=None, operations=None):
    catalog, _ = source_catalog(s)
    source = next(e['id'] for e in reversed(catalog) if e['source_id'] == 'user')
    values = {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000',
              'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'} if values is None else values
    return {'operations': ['filter'] if operations is None else operations,
            'conditions': [{'key': k, 'value': v, 'status': 'missing' if v is None else 'specified', 'evidence_ids': [source] if v is not None else []} for k, v in values.items()],
            'unsupported_actions': []}


def checked(s, raw):
    return validate_conditions(raw, s, source_catalog(s)[0])


class Fake:
    def __init__(self, packets):
        self.packets = iter(packets); self.calls = 0
    def invoke(self, messages):
        self.calls += 1
        value = next(self.packets)
        if isinstance(value, Exception): raise value
        return {'content': json.dumps(value, ensure_ascii=False)}


def workflow_draft(s, threshold=1000000, operator='gt'):
    plan = {'filters': [{'field': '비용', 'operator': operator, 'value': threshold, 'compare_field': None, 'value_type': 'number'}],
            'sort': [], 'match': 'all', 'missing_policy': 'error', 'duplicate_policy': 'keep', 'duplicate_field': None, 'output_format': 'json'}
    workflow = {'entry': 'read', 'nodes': [node('read', 'read_table'),
        node('filter', 'filter_sort', [bind('table', 'read', 'table')], execution_plan=plan),
        node('out', 'write_result', [bind('table', 'filter', 'table')], format='json')],
        'edges': [edge('read', 'filter'), edge('filter', 'out')]}
    result = {**draft(), 'name': '비용 선별', 'purpose': '요청 금액 조건 적용', 'execution_plan': plan, 'workflow': workflow,
              'requirements': [{'description': '사용자 조건', 'source_id': 'user', 'quote': s['messages'][0]['content']}], 'questions': []}
    return AgentDraft.model_validate(result).model_dump()


def test_required_condition_omitted_by_model_is_derived_by_code():
    s = state('비용이 큰 행만 골라줘.')
    result = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gt'}))
    assert result['decision'] == 'clarify'
    assert result['unresolved'] == ['filter.value']
    assert result['questions'][0]['key'] == 'filter.value'


@pytest.mark.parametrize('threshold', ['0', '999999', 'NaN', 'Infinity'])
def test_invented_or_nonfinite_threshold_is_not_ready(threshold):
    s = state(); raw = proposal(s)
    next(c for c in raw['conditions'] if c['key'] == 'filter.value')['value'] = threshold
    assert checked(s, raw)['decision'] == 'clarify'


def test_unit_normalized_numeric_value_is_grounded():
    s = state(); result = checked(s, proposal(s))
    assert result['decision'] == 'ready'
    assert result['slots']['filter.value']['value'] == 1000000


def test_foreign_evidence_and_duplicate_slots_are_rejected():
    s = state(); raw = proposal(s)
    raw['conditions'][0]['evidence_ids'] = ['other-session:1']
    with pytest.raises(ValueError): checked(s, raw)
    raw = proposal(s); raw['conditions'].append(deepcopy(raw['conditions'][0]))
    with pytest.raises(ValueError): checked(s, raw)


@pytest.mark.parametrize('frequency,weekday,expected', [('daily', None, 'ready'), ('weekly', None, 'clarify'), ('weekly', '0', 'ready')])
def test_schedule_conditional_required_weekday(frequency, weekday, expected):
    s = state('한국 시간 매주 월요일 8시 0분에 실행해줘.')
    # Isolate weekday validation from the separate managed-file prerequisite.
    s['resources'] = [{'id': 'table', 'kind': 'table', 'columns': ['비용']}]
    values = {'schedule.frequency': frequency, 'schedule.hour': '8', 'schedule.minute': '0', 'schedule.timezone': 'Asia/Seoul'}
    if weekday is not None: values['schedule.weekday'] = weekday
    result = checked(s, proposal(s, values, ['schedule']))
    assert result['decision'] == expected


@pytest.mark.parametrize('key,value', [('schedule.hour', '24'), ('schedule.minute', '60'), ('schedule.weekday', '7'), ('schedule.timezone', 'Moon/Base')])
def test_invalid_schedule_values_are_not_ready(key, value):
    s = state('한국 시간 매주 월요일 8시에 실행해줘.')
    values = {'schedule.frequency': 'weekly', 'schedule.hour': '8', 'schedule.minute': '0', 'schedule.timezone': 'Asia/Seoul', 'schedule.weekday': '0'}
    values[key] = value
    assert checked(s, proposal(s, values, ['schedule']))['decision'] == 'clarify'


def test_conflicting_condition_is_never_arbitrarily_resolved():
    s = state(); raw = proposal(s)
    item = next(c for c in raw['conditions'] if c['key'] == 'filter.value')
    item.update(status='conflict', value=None)
    result = checked(s, raw)
    assert result['decision'] == 'clarify'
    assert '서로 다릅니다' in result['questions'][0]['text']


@pytest.mark.parametrize('fault', ['db', 'partial', 'supplement', 'version'])
def test_system_fault_skips_llm_and_does_not_become_business_question(fault):
    s = state(); s['resources'] = [{'id': 'doc', 'kind': 'document', 'text': '근거', 'version': '1'}]
    if fault == 'db': s['source_health'] = {'db_status': 'timeout'}
    if fault == 'partial': s['resources'][0]['parser_status'] = 'partial'
    if fault == 'supplement': s['resources'][0]['parser_metadata'] = {'supplement_report': {'deferred_pages': [2]}}
    if fault == 'version': s['condition_ledger'] = {'resource_versions': {'doc': '0'}}
    fake = Fake([]); result = judge_conditions(s, fake)
    assert result['ledger']['decision'] == 'system_error'
    assert result['ledger']['questions'] == []
    assert result['model_calls'] == fake.calls == 0


def test_full_document_lexical_retrieval_includes_late_evidence_and_reports_coverage():
    s = state('비용의 큰 금액 기준으로 골라줘.')
    s['resources'] = [{'id': 'doc', 'kind': 'document', 'pages': ['관계 없는 내용. ' * 3000, '비용의 큰 금액 기준은 100만원 초과이다.']}]
    catalog, coverage = source_catalog(s)
    assert any(e.get('page') == 2 and '100만원' in e['quote'] for e in catalog)
    assert coverage[0]['scanned_chunks'] > coverage[0]['returned_chunks']
    assert not coverage[0]['complete_context']


def test_prepared_input_contains_fault_metadata_and_original_messages():
    s = state(); s['resources'] = [{'id': 'doc', 'kind': 'document', 'text': '기준', 'parser_status': 'partial', 'warnings': ['일부 변환 오류'], 'version': 'v2'}]
    messages, _, _ = prepare_conditions(s)
    value = json.loads(messages[-1]['content'])['target_case']
    assert value['messages'] == s['messages']
    assert value['resources'][0]['parser_status'] == 'partial'
    assert value['resources'][0]['version'] == 'v2'


def test_reference_contract_and_demos_are_not_target_user_history():
    s = state(); messages, _, catalog = prepare_conditions(s)
    target=json.loads(messages[-1]['content'])['target_case']
    assert target['messages']==s['messages']
    assert 'required_conditions' not in target and 'supported_tools' not in target
    assert '목요일' not in json.dumps(target,ensure_ascii=False)
    assert all(not e['id'].startswith('demo') for e in catalog)
    assert 'scheduling.py' in messages[0]['content']


def test_unversioned_content_change_is_detected():
    s=state();s['resources']=[{'id':'doc','kind':'document','text':'원래 기준'}]
    s['condition_ledger']=checked(s,proposal(s))
    s['resources'][0]['text']='새로운 기준'
    assert any('버전' in issue for issue in input_issues(s))


def test_single_work_contract_and_mixed_request_hold():
    s=state();old=proposal(s)
    raw={'operation':'filter','conditions':old['conditions'],'unsupported_actions':[]}
    assert checked(s,raw)['decision']=='ready'
    raw['operation']='mixed'
    assert checked(s,raw)['decision']=='manual_review'


def test_weekday_is_canonicalized_from_unique_literal_source():
    s=state('한국 시간으로 매주 화요일 오전 9시 30분에 실행해줘.')
    s['resources'] = [{'id': 'table', 'kind': 'table', 'columns': ['비용']}]
    raw=proposal(s,{'schedule.frequency':'weekly','schedule.hour':'9','schedule.minute':'30',
                    'schedule.timezone':'Asia/Seoul','schedule.weekday':'2'},['schedule'])
    result=checked(s,raw)
    assert result['decision']=='ready'
    assert result['slots']['schedule.weekday']['value']==1
    assert any('정규화' in issue for issue in result['invalid_values'])


def test_sqlite_corruption_is_not_converted_to_empty_user_context(tmp_path,monkeypatch):
    import sqlite3
    from agent_builder import worker
    (tmp_path/'agent-builder.sqlite').write_bytes(b'broken local test database')
    calls=[]
    monkeypatch.setattr(worker,'build_graph',lambda *a,**kw:calls.append(1))
    with pytest.raises(sqlite3.DatabaseError):
        worker.handle({'directory':str(tmp_path),'action':'message','id':'test','revision':0,'content':'기준은 100만원'})
    assert calls==[]


def test_timezone_cannot_be_invented_from_an_iana_valid_default():
    s=state('매주 수요일 7시 0분에 실행해줘. 시간대는 아직 정하지 않았어.')
    raw=proposal(s,{'schedule.frequency':'weekly','schedule.hour':'7','schedule.minute':'0',
                    'schedule.weekday':'2','schedule.timezone':'Asia/Seoul'},['schedule'])
    result=checked(s,raw)
    assert result['decision']=='clarify'
    assert 'schedule.timezone' in result['unresolved']


def test_literal_policies_cannot_be_silently_omitted_by_extractor():
    s=state('비용이 100만원 초과인 행. 빈 값은 오류, 중복 유지, JSON으로 저장.')
    raw=proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':'1000000'})
    result=checked(s,raw)
    assert result['slots']['missing.policy']['value']=='error'
    assert result['slots']['duplicate.policy']['value']=='keep'
    assert result['slots']['output.format']['value']=='json'


@pytest.mark.parametrize('text',['빈 값은 제외하지 마.','빈 값은 오류가 아니야.','빈 값은 제외, 빈 값은 오류.'])
def test_negated_or_conflicting_policy_is_not_asserted(text):
    s=state('비용 100만원 초과. '+text)
    result=checked(s,proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':'1000000'}))
    assert result['decision']=='clarify'
    assert result['slots']['missing.policy']['status']=='conflict'


def test_latest_explicit_policy_overrides_previous_turn():
    s=state('비용 100만원 초과. 빈 값은 오류, 중복은 유지.')
    s['messages'] += [{'role':'user','content':'빈 값은 제외로 바꿔줘.'}]
    raw=proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':'1000000'})
    raw['conditions'][0]['evidence_ids']=['user:0:0'];raw['conditions'][1]['evidence_ids']=['user:0:0'];raw['conditions'][2]['evidence_ids']=['user:0:0']
    result=checked(s,raw)
    assert result['slots']['missing.policy']['value']=='exclude'
    assert result['slots']['duplicate.policy']['value']=='keep'


@pytest.mark.parametrize('operator,expected',[('gt','C'),('gte','BC')])
def test_template_filter_applies_boundary_to_actual_rows(tmp_path,operator,expected):
    from agent_builder.condition_templates import compile_conditions
    s=state('비용 100만원 '+('초과' if operator=='gt' else '이상')+'인 행을 JSON으로 저장. 빈 값은 오류, 중복 유지.')
    s['resources']=[{'id':'a','name':'비용.csv','kind':'table','columns':['항목','비용'],'profile':{},'text':''}]
    raw=proposal(s);next(c for c in raw['conditions'] if c['key']=='filter.operator')['value']=operator
    s['condition_ledger']=checked(s,raw);s.update(compile_conditions(s),confirmed=True)
    store=Storage(tmp_path);store.add_asset(s['resources'][0],[{'항목':'A','비용':900000},{'항목':'B','비용':1000000},{'항목':'C','비용':1100000}])
    identifier=store.save_agent(s);run=run_workflow(store,identifier,'실행','',file_ids=['a'])
    assert run['status']=='succeeded'
    assert ''.join(row['항목'] for row in run['rows'])==expected
    assert json.loads((tmp_path/run['report_path']).read_text(encoding='utf-8-sig'))==run['rows']
    assert json.loads(run['artifact']['content'])==run['rows']
    store.close()


@pytest.mark.parametrize('policy,expected',[('first',1),('last',2)])
def test_template_deduplication_retention_policy(tmp_path,policy,expected):
    from agent_builder.condition_templates import compile_conditions
    s=state('이메일 중복은 '+('첫' if policy=='first' else '마지막')+' 행을 JSON으로 저장해줘.')
    s['resources']=[{'id':'a','name':'중복.csv','kind':'table','columns':['이메일','값'],'profile':{},'text':''}]
    raw=proposal(s,{'duplicate.field':'이메일','duplicate.policy':policy,'output.format':'json'},['deduplicate'])
    s['condition_ledger']=checked(s,raw);s.update(compile_conditions(s),confirmed=True)
    store=Storage(tmp_path);store.add_asset(s['resources'][0],[{'이메일':'a@example.com','값':1},{'이메일':'a@example.com','값':2}])
    identifier=store.save_agent(s);run=run_workflow(store,identifier,'실행','',file_ids=['a'])
    assert run['status']=='succeeded' and run['rows'][0]['값']==expected and len(run['rows'])==1
    assert json.loads((tmp_path/run['report_path']).read_text(encoding='utf-8-sig'))==[{'이메일':'a@example.com','값':expected}]
    store.close()


def test_template_ambiguous_table_or_sort_is_held_without_llm():
    from agent_builder.condition_templates import template_node
    s=state();s['condition_ledger']=checked(s,proposal(s))
    s['resources']=[{'id':i,'kind':'table','columns':['비용']} for i in ['a','b']]
    assert template_node(s)['status']=='validation_failed'
    s['resources']=[];s['messages'][0]['content']+=' 오름차순으로 정렬해줘.'
    assert template_node(s)['condition_route']=='stop'


def test_template_schedule_uses_confirmed_timezone_and_weekday():
    from agent_builder.condition_templates import compile_conditions
    s=state('한국 시간 매주 금요일 오후 3시 0분에 JSON으로 저장해줘.')
    s['resources']=[{'id':'a','kind':'table','columns':['값'],'text':''}]
    raw=proposal(s,{'schedule.frequency':'weekly','schedule.hour':'15','schedule.minute':'0','schedule.timezone':'Asia/Seoul','schedule.weekday':'4'},['schedule'])
    s['condition_ledger']=checked(s,raw);compiled=compile_conditions(s)['draft']
    assert compiled['schedule']=={'frequency':'weekly','hour':15,'minute':0,'weekday':4,'timezone':'Asia/Seoul'}
    assert plan_issues(s['condition_ledger'],compiled)==[]


def test_answer_updates_target_and_preserves_unmentioned_confirmed_conditions():
    s = state(); first = checked(s, proposal(s))
    s.update(condition_ledger=first, messages=s['messages'] + [{'role': 'assistant', 'content': '변경할 기준은요?'}, {'role': 'user', 'content': '200만원 초과로 바꿔줘.'}])
    second = checked(s, proposal(s, {'filter.operator': 'gt', 'filter.value': '2000000'}))
    assert second['slots']['filter.value']['value'] == 2000000
    assert second['slots']['missing.policy']['value'] == 'error'
    assert second['slots']['duplicate.policy']['value'] == 'keep'
    assert second['slots']['filter.field']['value'] == '비용'


def test_changed_value_without_latest_answer_evidence_is_not_silently_accepted():
    s = state('비용 100만원 초과, 참고액은 200만원.'); first = checked(s, proposal(s))
    s.update(condition_ledger=first, messages=s['messages'] + [{'role': 'user', 'content': '파일 이름만 바꿔줘.'}])
    raw = proposal(s, {'filter.value': '2000000'})
    raw['conditions'][0]['evidence_ids'] = ['user:0:0']
    assert checked(s, raw)['slots']['filter.value']['status'] == 'conflict'


def test_question_ids_are_session_specific():
    a = state('비용이 큰 것만 골라줘.'); b = {**a, 'session_id': 'other'}
    raw = proposal(a, {'filter.field': '비용'})
    assert checked(a, raw)['questions'][0]['id'] != checked(b, raw)['questions'][0]['id']


def test_model_failure_is_held_not_unsupported():
    result = make_condition_check(transport=Fake([TimeoutError()]), mode='enforce')(state())
    assert result['condition_ledger']['decision'] == 'system_error'
    assert result['condition_route'] == 'stop'
    assert result['draft']['unsupported'] == result['draft']['questions'] == []


def test_off_makes_no_model_call_and_shadow_does_not_change_route():
    fake = Fake([])
    assert make_condition_check(transport=fake, mode='off')(state())['condition_route'] == 'conversation'
    s = state(); raw = proposal(s, {'filter.field': '비용'})
    result = make_condition_check(transport=Fake([raw]), mode='shadow')(s)
    assert result['condition_route'] == 'conversation'
    assert 'draft' not in result
    assert result['condition_ledger']['decision'] == 'clarify'


def test_plan_audit_catches_boundary_missing_policy_output_and_dropped_schedule():
    s = state(); ledger = checked(s, proposal(s)); d = workflow_draft(s)
    assert plan_issues(ledger, d) == []
    d['workflow']['nodes'][1]['settings']['execution_plan']['filters'][0]['operator'] = 'gte'
    assert any('선별' in e for e in plan_issues(ledger, d))
    d = workflow_draft(s); d['workflow']['nodes'][1]['settings']['execution_plan']['missing_policy'] = 'keep'
    assert any('missing.policy' in e for e in plan_issues(ledger, d))
    d = workflow_draft(s); d['workflow']['nodes'][-1]['settings']['format'] = 'csv'
    assert any('출력' in e for e in plan_issues(ledger, d))
    ledger['operations'].append('schedule'); ledger['slots']['schedule.hour'] = {'status': 'specified', 'value': 8}
    assert any('예약' in e for e in plan_issues(ledger, workflow_draft(s)))


def test_save_rechecks_conditions_even_after_confirmation(tmp_path):
    s = state(); s.update(condition_enforced=True, condition_ledger=checked(s, proposal(s)), draft=workflow_draft(s, operator='gte'), confirmed=True)
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError, match='확정 업무 조건'): make_save_node(store)(s)
        assert not store.agents()
    finally: store.close()


def test_checkpoint_answer_restart_approval_and_real_table_execution(tmp_path):
    s = state('비용이 큰 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    first_raw = proposal(s, {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': None, 'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'})
    after = {**s, 'messages': s['messages'] + [{'role': 'assistant', 'content': '선별할 기준 값은 얼마인가요?'}, {'role': 'user', 'content': '100만원 초과로 해줘.'}]}
    second_raw = proposal(after, {'filter.value': '1000000'})
    calls = []
    def conversation(st):
        calls.append(1)
        return {'draft': workflow_draft(st), 'confirmed': False, 'semantic_issues': []}
    store = Storage(tmp_path); cfg = {'configurable': {'thread_id': s['session_id']}}
    graph, conn = build_graph(store, conversation=conversation, condition_transport=Fake([first_raw]), condition_mode='enforce')
    first = graph.invoke(s, cfg)
    assert first['status'] == 'needs_clarification' and not calls and not store.agents()
    assert first['messages'][-1]['role']=='assistant'
    assert first['pending_questions'][0]['text'] in first['messages'][-1]['content']
    question_id = first['pending_questions'][0]['id']
    conn.close(); store.close()
    store = Storage(tmp_path)
    graph, conn = build_graph(store, conversation=conversation, condition_transport=Fake([second_raw]), condition_mode='enforce')
    rejected=graph.invoke(Command(resume={'type': 'message', 'content': '100만원 초과', 'question_id': 'other'}), cfg)
    assert rejected['__interrupt__'][0].value['error']=='현재 대화의 확인 질문에 답변해주세요.'
    second = graph.invoke(Command(resume={'type': 'message', 'content': '100만원 초과로 해줘.', 'question_id': question_id}), cfg)
    assert second['status'] == 'ready' and len(calls) == 1
    assert second['condition_answers'][0]['question_ids'] == [question_id]
    assert second['condition_ledger']['slots']['missing.policy']['value'] == 'error'
    assert not store.agents()
    saved = graph.invoke(Command(resume={'type': 'confirm'}), cfg)
    assert saved['status'] == 'saved' and len(store.agents()) == 1
    graph.invoke(Command(resume={'type': 'confirm'}), cfg)
    assert len(store.agents()) == 1
    asset = {'id': 'table', 'name': '비용.csv', 'kind': 'table', 'columns': ['항목', '비용'], 'profile': {}}
    store.add_asset(asset, [{'항목': 'A', '비용': 1000000}, {'항목': 'B', '비용': 1500000}])
    result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=['table'])
    assert result['status'] == 'succeeded'
    assert result['rows'] == [{'항목': 'B', '비용': 1500000}]
    conn.close(); store.close()


@pytest.mark.parametrize('extra',['filters','sort'])
def test_single_dedup_contract_blocks_untracked_transform_and_save(tmp_path,extra):
    s=state('주문번호가 같은 행은 마지막 행만 남겨 JSON으로 저장해줘. 빈 값은 오류야.')
    ledger=checked(s,proposal(s,{'duplicate.field':'주문번호','duplicate.policy':'last','missing.policy':'error','output.format':'json'},['deduplicate']))
    d=workflow_draft(s)
    for p in [d['execution_plan'],d['workflow']['nodes'][1]['settings']['execution_plan']]:
        p.update(filters=[],sort=[],duplicate_field='주문번호',duplicate_policy='last')
    assert not plan_issues(ledger,d)
    bad=deepcopy(d)
    for p in [bad['execution_plan'],bad['workflow']['nodes'][1]['settings']['execution_plan']]:
        p[extra]=([{'field':'주문번호','operator':'eq','value':'A','compare_field':None,'value_type':'text'}]
                  if extra=='filters' else [{'field':'주문번호','direction':'ascending','value_type':'text'}])
    assert any('단일 중복 제거 계약' in issue for issue in plan_issues(ledger,bad))
    assert not d['execution_plan'][extra]  # No mutation or silent fix of the bad plan.
    store=Storage(tmp_path)
    try:
        with pytest.raises(ValueError,match='확정 업무 조건'):
            make_save_node(store)({**s,'condition_enforced':True,'condition_ledger':ledger,'draft':bad,'confirmed':True})
        assert not store.agents()
        assert bad['execution_plan'][extra]
    finally:store.close()


def test_decoder_requires_value_and_evidence_for_specified_conditions():
    messages,schema,catalog=prepare_conditions(state())
    specified,unresolved=schema['$defs']['Condition']['anyOf']
    assert specified['properties']['status']['enum']==['specified']
    assert specified['properties']['value']=={'type':'string'}
    assert specified['properties']['evidence_ids']['minItems']==1
    assert specified['properties']['evidence_ids']['items']['enum']==[e['id'] for e in catalog]
    assert unresolved['properties']['value']=={'type':'null'}
    assert unresolved['properties']['status']['enum']==['missing','conflict']
    assert 'value' in specified['required'] and 'evidence_ids' in specified['required']


def test_latest_user_change_is_explicit_without_changing_original_history():
    s=state();original=deepcopy(s)
    s['messages'] += [{'role':'assistant','content':'기준을 확인했습니다.'},{'role':'user','content':'기준은 30만원 초과로 변경해줘.'}]
    before=deepcopy(s);messages,_,_=prepare_conditions(s)
    case=json.loads(messages[-1]['content'])['target_case']
    assert case['messages']==s['messages']
    assert case['latest_user_update']=={'turn':2,'content':s['messages'][2]['content'],'evidence_ids':['user:2:0']}
    assert s==before and original['messages'][0]==s['messages'][0]
