"""Feedback regressions: preserve user intent and pending clarification state."""
from copy import deepcopy

import pytest
from langgraph.types import Command

from agent_builder.design.request_guards import review_request, request_guard_issues, unary_filter_issue
from agent_builder.design.document_rows_template import document_rows_reply, document_rows_applies, QUESTION
from agent_builder.design.graph import build_graph
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import initial, draft
from tests.design.test_document_rows_template import DOC, REQUEST, asked_state, no_planner
from tests.workflow_engine.test_workflow import node, bind, edge


def request(text, **extra):
    return {**initial(), 'messages': [{'role': 'user', 'content': text}], **extra}


@pytest.mark.parametrize('operator,text,valid', [
    ('is_empty', '완료 주문만 남겨줘', False),
    ('not_empty', '완료 주문만 남겨줘', False),
    ('is_empty', '완료 주문만 남겨줘. 빈값은 오류로 처리', False),
    ('is_empty', '상태가 빈 값인 행만 남겨줘', True),
    ('not_empty', '상태가 빈 값인 행을 제외해줘', True),
    ('is_empty', '상태가 빈 값인 행을 제외해줘', False),
    ('not_empty', '상태가 빈 값인 행만 남겨줘', False),
    ('not_empty', '상태 값이 있는 행만 남겨줘', True),
    ('not_empty', '상태가 비어있지 않은 행만 남겨줘', True),
    ('is_empty', '상태가 비어있지 않은 행만 남겨줘', False),
    ('eq', '완료 주문만 남겨줘', True),
])
def test_categorical_requests_cannot_turn_into_empty_filters(operator, text, valid):
    rule = dict(field='상태', operator=operator, value=None, compare_field=None)
    assert (unary_filter_issue(rule, text) is None) is valid


def test_empty_operator_cannot_discard_a_comparison_value():
    assert unary_filter_issue(dict(field='상태', operator='is_empty', value='완료'), '상태 is_empty')


@pytest.mark.parametrize('text,expected', [
    ('계약서를 분석하고 사람 검토(Human Review) 후 최종 메모 작성', (True, True)),
    ('사람 검토(Human Review)는 생략해줘', (False, False)),
    ('사람 검토 없이 최종 보고서 작성', (False, True)),
    ('사람 검토가 뭐야?', (False, False)),
    ('셀에 "사람 검토"라는 문구를 넣어줘', (False, False)),
])
def test_review_instructions_distinguish_cancellation_and_data(text, expected):
    assert review_request(request(text)) == expected


def review_draft():
    nodes = [node('read', 'read_document'),
             node('analysis', 'compose_answer', [bind('documents', 'read', 'documents')]),
             node('review', 'ask_user', [bind('text', 'analysis', 'text')], question='검토 의견은?'),
             node('memo', 'compose_answer', [bind('text', 'analysis', 'text'), bind('answer', 'review', 'answer')])]
    return {**draft(), 'workflow': {'entry': 'read', 'nodes': nodes,
            'edges': [edge(a['id'], b['id']) for a, b in zip(nodes, nodes[1:])]}}


def test_review_must_pause_and_final_memo_must_consume_the_answer():
    state = request('계약서를 분석하고 사람 검토 후 최종 메모를 작성해줘')
    config = review_draft()
    assert request_guard_issues(state, config) == []
    config['workflow']['nodes'][2]['settings']['instructions'] = '자동 승인'
    assert any('사람 검토' in issue for issue in request_guard_issues(state, config))
    config = review_draft()
    config['workflow']['nodes'][-1]['bindings'].pop()
    assert any('최종 메모' in issue for issue in request_guard_issues(state, config))
    config = review_draft()
    config['workflow']['nodes'][-1]['bindings'].pop(0)
    assert any('분석 결과' in issue for issue in request_guard_issues(state, config))


def test_compound_contract_request_is_not_replaced_by_three_node_csv_recipe():
    state = request('계약서를 표로 분석하고 사람 검토(Human Review) 후 최종 메모를 작성해줘', resources=[DOC])
    assert not document_rows_applies(state)
    assert document_rows_reply(state) is None
    shortened = {**draft(), 'workflow': {'entry': 'read', 'nodes': [node('read', 'read_document')], 'edges': []}}
    assert any('사람 검토' in issue for issue in request_guard_issues(state, shortened))


def test_explicit_first_turn_fields_and_natural_answer_avoid_reasking():
    first = request(REQUEST + '\n추출 항목: 품명, 실입고', resources=[DOC])
    result = document_rows_reply(first)
    assert result['draft']['workflow']['nodes'][1]['settings']['fields'] == ['품명', '실입고']
    result = document_rows_reply(asked_state('품명, 실입고를 추출해줘'))
    assert result['draft']['workflow']['nodes'][1]['settings']['fields'] == ['품명', '실입고']


def test_explanation_preserves_question_and_restart_can_answer_it(tmp_path):
    store = Storage(tmp_path)
    state = request(REQUEST, resources=[DOC])
    cfg = {'configurable': {'thread_id': state['session_id']}}
    graph, connection = build_graph(store, conversation=no_planner)
    first = graph.invoke(state, cfg)
    before = deepcopy(first['draft'])
    helped = graph.invoke(Command(resume={'type': 'message', 'content': '추출 항목이 뭐야?'}), cfg)
    assert helped['status'] == 'needs_clarification'
    assert helped['draft'] == before
    assert helped.get('pending_questions', []) == first.get('pending_questions', [])
    assert not helped.get('requirement_answers') and not helped.get('condition_answers')
    assert helped['__interrupt__'][0].value['questions'] == [QUESTION]
    connection.close()
    store.close()
    store = Storage(tmp_path)
    graph, connection = build_graph(store, conversation=no_planner)
    try:
        done = graph.invoke(Command(resume={'type': 'message', 'content': '품명, 실입고'}), cfg)
        assert done['status'] == 'ready', done.get('issues')
        assert done['draft']['workflow']['nodes'][1]['settings']['fields'] == ['품명', '실입고']
    finally:
        connection.close()
        store.close()


def test_typed_answer_is_never_intercepted_as_a_help_question(monkeypatch):
    from agent_builder.design.nodes import await_input
    question = dict(id='q', key='filter.value', text='기준값은?', source='requirement', options=[])
    answer = dict(type='message', content='뭐야?', question_id='q')
    monkeypatch.setattr(await_input, 'interrupt', lambda _: answer)
    state = request('상태로 선별해줘', draft=draft(['기준값은?']), issues=[], pending_questions=[question])
    result = await_input.await_input_node(state)
    assert not result['clarification_help']
    assert result['requirement_answers'][0]['answer'] == '뭐야?'


def test_help_keeps_stable_checklist_ids_and_does_not_record_a_condition(monkeypatch):
    from agent_builder.design.nodes import await_input
    question = dict(id='stable', key='filter.value', text='기준값은?', source='condition', options=['10', '20'])
    monkeypatch.setattr(await_input, 'interrupt', lambda _: dict(type='message', content='기준값이 뭐야?'))
    state = request('조건에 따라 선별', draft=draft(['기준값은?']), issues=[],
                    pending_questions=[question], condition_enforced=True)
    before = deepcopy(state)
    result = await_input.await_input_node(state)
    assert result['clarification_help']
    assert 'condition_answers' not in result and 'pending_questions' not in result
    assert '10, 20' in result['messages'][-1]['content']
    assert state == before


def test_failure_notice_is_actionable_without_echoing_provider_data():
    import httpx
    from openai import APIConnectionError, APITimeoutError
    from agent_builder.design.failure_notice import requirement_failure_notice
    wire = httpx.Request('POST', 'https://example.test/private-key')
    for error, expected in [(APIConnectionError(request=wire), '연결 상태'),
                            (APITimeoutError(request=wire), '응답 시간'),
                            (ValueError('private-key bad_schema'), '입력 자료 → 처리 조건')]:
        notice = requirement_failure_notice('model_output_error', error)
        assert expected in notice and '조건은 보존' in notice
        assert 'private-key' not in notice and 'bad_schema' not in notice


def test_requirement_extraction_cannot_drop_human_review_or_invent_empty_filter():
    from agent_builder.design.requirement_ledger import extract_plan
    state = request('계약서를 분석하고 사람 검토 후 최종 메모를 작성해줘')
    raw = dict(tasks=[dict(source_message=0, description='계약 분석',
                           spec=dict(kind='document_analysis'))], unresolved=[])
    with pytest.raises(ValueError, match='사람 검토'):
        extract_plan(raw, state)
    state = request('완료 주문만 CSV로 남겨줘')
    raw['tasks'][0]['spec'] = dict(kind='filter', match='all', filters=[
        dict(field='상태', operator='is_empty', value=None, compare_field=None, value_type='text')])
    with pytest.raises(ValueError, match='빈값 선별 근거'):
        extract_plan(raw, state)


def test_user_confirmation_cannot_save_a_missing_review(tmp_path):
    from agent_builder.design.nodes.validation import validate_node
    from agent_builder.design.nodes.save import make_save_node
    text = '계약서를 분석하고 사람 검토 후 최종 메모를 작성해줘'
    config = review_draft()
    config['tool_ids'] = ['read_document', 'compose_answer', 'ask_user']
    config['requirements'] = [dict(source_id='user', description='검토', quote=text)]
    config['workflow']['nodes'] = config['workflow']['nodes'][:2]
    config['workflow']['edges'] = [edge('read', 'analysis')]
    config['tool_ids'].remove('ask_user')
    state = request(text, draft=config, confirmed=True)
    checked = validate_node(state)
    assert checked['status'] != 'ready'
    assert any('사람 검토' in issue for issue in checked['issues'])
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError, match='검증'):
            make_save_node(store)(state)
        assert store.agents() == []
    finally:
        store.close()


def test_inactive_global_filter_cannot_block_a_node_with_its_own_plan():
    config = review_draft()
    config['execution_plan']['filters'] = [dict(field='상태', operator='is_empty', value=None)]
    assert request_guard_issues(request('문서를 요약해줘'), config) == []


def test_generation_retry_keeps_original_turns_and_reopened_ready_state(tmp_path, monkeypatch):
    from service.worker import handle
    from infrastructure.persistence.ids import uid
    import service.handlers.design as worker
    real = worker.build_graph
    def graph(*args, **kwargs):
        return real(*args, **{**kwargs,'capability_mode':'off','condition_mode':'off',
                            'requirement_mode':'off','verifier_mode':'off'})
    monkeypatch.setattr(worker, 'build_graph', graph)
    session = handle(dict(directory=str(tmp_path),action='new_session',model='fixture',credential_id=uid()))
    job = dict(directory=str(tmp_path),id=session['id'],revision=0,action='message')
    seen = []
    def planner(state):
        seen.append(deepcopy(state['messages']))
        if len(seen) == 1:
            raise RuntimeError('temporary planner failure')
        return {'draft':draft()}
    with pytest.raises(RuntimeError):
        handle({**job,'content':initial()['messages'][0]['content']},conversation=planner)
    done = handle({**job,'content':'다시 시도'},conversation=planner)
    assert seen[1] == seen[0]
    assert done['status'] == 'ready', done.get('issues')
    reopened = handle(dict(directory=str(tmp_path),id=session['id'],action='session'))
    assert reopened['status'] == 'ready' and reopened['draft'] == done['draft']
    assert reopened['messages'] == done['messages']


def sealed_specs(text, specs, resources):
    from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest
    state = request(text, resources=resources, requirement_required=True)
    ledger = dict(tasks=[dict(id=f'r{i}',spec=s,source_message=0,quote=text,description=s['kind'],depends_on=[])
                         for i,s in enumerate(specs)], unresolved=[])
    state.update(requirement_ledger=ledger,requirement_digest=plan_digest(ledger),
                 requirement_fingerprint=ledger_fingerprint(state))
    return state


def native_schema():
    from contracts.models import AgentDraft
    schema = AgentDraft.model_json_schema()
    base = schema['$defs']['WorkflowNode']; variants = []
    for tool in ('read_table','calculate_columns','filter_sort','write_result','read_document','compose_answer','ask_user'):
        variant = deepcopy(base)
        variant['properties']['tool'] = {'enum':[tool]}
        variant['properties']['settings'] = deepcopy(schema['$defs']['NodeSettings'])
        variant['properties']['settings']['properties']['operation'] = {'type':'null'}
        variants.append(variant)
    schema['$defs']['WorkflowNode'] = {'anyOf':variants}
    return schema


def test_output_projection_does_not_lose_sealed_formula_and_order_status():
    from agent_builder.design.requirement_generation import constrain_request_settings, generation_requirements
    from jsonschema import Draft202012Validator
    text = '완료 주문만, 수량×단가를 주문금액으로, 100000 이상, 주문번호 오름차순, 주문번호·주문금액을 CSV로 저장'
    rules = [dict(field='주문상태',operator='eq',value='완료',compare_field=None,value_type='text'),
             dict(field='주문금액',operator='gte',value=100000,compare_field=None,value_type='number')]
    specs = [dict(kind='read',file_ids=['orders']),dict(kind='compute',formula='수량*단가',output_column='주문금액'),
             dict(kind='filter',filters=rules,match='all'),
             dict(kind='sort',sort=[dict(field='주문번호',direction='ascending',value_type='text')]),
             dict(kind='select_fields',fields=['주문번호','주문금액']),dict(kind='export',format='csv')]
    state = sealed_specs(text,specs,[dict(id='orders',kind='table',columns=['주문번호','수량','단가','주문상태'])])
    assert generation_requirements(state)[1] == []
    original = native_schema()
    schema = constrain_request_settings(original,state)
    variants = {v['properties']['tool']['enum'][0]:v['properties']['settings']['properties']
                for v in schema['$defs']['WorkflowNode']['anyOf']}
    formula = Draft202012Validator(variants['calculate_columns']['calculation_formula'])
    assert formula.is_valid('수량*단가') and not formula.is_valid('step_4')
    plan = variants['filter_sort']['execution_plan']
    filter_schema = Draft202012Validator(plan['properties']['filters'])
    assert filter_schema.is_valid(rules)
    assert not filter_schema.is_valid([{**rules[0],'operator':'is_empty'},rules[1]])
    assert original == native_schema()


def test_contract_review_generation_reserves_analysis_review_and_final_memo():
    from agent_builder.design.requirement_generation import generation_requirements, constrain_request_settings
    text = ('계약 분석(LLM), 사람 검토(Human Review), 최종 메모(LLM) 순서로 연결한다. '
            '중요 조항 5개의 조항 번호, 핵심 내용, 위험, 확인할 사항을 표로 작성한다. '
            '검토 의견을 한 번만 받고 최종 메모는 검토 의견과 계약서 사실을 구분한다. Markdown 출력.')
    state = sealed_specs(text,[dict(kind='document_analysis',request=text),dict(kind='human_review')],[DOC])
    assert not document_rows_applies(state)
    assert generation_requirements(state)[1] == ['read_document','compose_answer','ask_user','compose_answer']
    schema = constrain_request_settings(native_schema(),state)
    assert schema['$defs']['WorkflowDefinition']['properties']['nodes']['minItems'] == 4
    assert any('사람 검토' in issue for issue in request_guard_issues(state,draft()))


@pytest.mark.parametrize('symbol,formula', [('×','수량*단가'),('÷','수량/단가')])
def test_written_arithmetic_symbols_keep_formula_and_output_column(symbol, formula):
    from agent_builder.design.compute_grounding import reconcile_compute
    state = request(f'새 계산 열을 만들어줘. 주문금액=수량{symbol}단가',
                    resources=[dict(id='orders',kind='table',columns=['수량','단가'])])
    raw = dict(tasks=[dict(source_message=0,description='계산',spec=dict(kind='compute',formula='step_4',output_column='wrong'))],unresolved=[])
    result = reconcile_compute(raw,state)
    assert result['tasks'][0]['spec'] == dict(kind='compute',formula=formula,output_column='주문금액')
    assert result['unresolved'] == []


def test_natural_field_addition_preserves_ready_recipe_and_original_fields(tmp_path):
    store = Storage(tmp_path)
    state = request(REQUEST+'\n추출 항목: 품명, 실입고',resources=[DOC])
    cfg = {'configurable':{'thread_id':state['session_id']}}
    graph, connection = build_graph(store,conversation=no_planner)
    try:
        first = graph.invoke(state,cfg)
        assert first['status'] == 'ready'
        added = graph.invoke(Command(resume=dict(type='message',content='유통기한도 추가해줘')),cfg)
        assert added['status'] == 'ready',added.get('issues')
        assert added['draft']['workflow']['nodes'][1]['settings']['fields'] == ['품명','실입고','유통기한']
        assert added['draft']['document_ids'] == first['draft']['document_ids']
        assert added['draft']['requirements'][:-1] == first['draft']['requirements']
    finally:
        connection.close();store.close()


def test_field_addition_never_replaces_a_customized_or_compound_workflow():
    state = request(REQUEST+'\n추출 항목: 품명',resources=[DOC])
    state.update(document_rows_reply(state))
    state['messages'].append(dict(role='user',content='실입고도 추가해줘'))
    before = deepcopy(state)
    assert document_rows_reply(state)['draft']['workflow']['nodes'][1]['settings']['fields'] == ['품명','실입고']
    assert state == before
    state['draft']['instructions'] += '\n검토한 뒤 저장'
    assert document_rows_reply(state) is None
