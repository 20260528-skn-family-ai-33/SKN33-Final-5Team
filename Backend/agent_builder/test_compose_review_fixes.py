"""Mocked models exercise real search, graph, checkpoints and SQLite approval."""
import copy
import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_builder import workflow_runtime as runtime
from agent_builder.review_decisions import approved_csv, decide
from agent_builder.storage import Storage
from agent_builder.test_node_connections import save
from agent_builder.test_reply_review import REQUEST, output, seeded
from agent_builder.test_workflow import bind, edge, node


class MockModel:
    def __init__(self, answers=None, keywords=None):
        self.answers = answers or ['최종 수정 응대: 환불 신청 후 3일 이내에 안내합니다.']
        self.keywords = ['환불'] if keywords is None else keywords
        self.search_calls = 0
        self.correction_calls = 0

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        return self

    def invoke(self, messages):
        if self.schema is runtime.SearchTerms:
            self.search_calls += 1
            return runtime.SearchTerms(keywords=self.keywords)
        answer = self.answers[min(self.correction_calls, len(self.answers) - 1)]
        self.correction_calls += 1
        return {'text': answer, 'citations': [{'evidence_id': 'e1'}]}


def mock_business(monkeypatch):
    observed = []

    def factory(**kwargs):
        evidence = kwargs['tools'][0].invoke({})
        assert evidence and evidence[0]['evidence_id'] == 'e1'

        class Business:
            def invoke(self, values, *args, **kw):
                observed.append(values)
                return {'structured_response': {'text': '모의 작성 초안', 'citations': [{'evidence_id': 'e1'}]}}

        return Business()

    monkeypatch.setattr(runtime, 'create_deep_agent', factory)
    return observed


def compose_config(storage, baseline, with_review=False, count=1, table=False):
    config = copy.deepcopy(storage.agent(baseline)['config'])
    nodes = [node('docs', 'read_document')]
    edges = []
    previous = 'docs'
    if with_review:
        if table:
            nodes.append(node('table', 'read_table'))
            edges.append(edge(previous, 'table'))
            previous = 'table'
        bindings = [bind('documents', 'docs', 'documents')]
        if table:
            bindings.append(bind('table', 'table', 'table'))
        nodes.append(node('review', 'reply_review', bindings,
                          fields=['customer_message', 'reply_draft'] if table else []))
        edges.append(edge(previous, 'review'))
        previous = 'review'
    for index in range(count):
        name = f'answer{index + 1}'
        bindings = [bind('documents', 'docs', 'documents')]
        if with_review:
            # Deliberately refer to the original review even on subsequent edits.
            bindings.append(bind('reviews', 'review', 'reviews'))
        nodes.append(node(name, 'compose_answer', bindings, instructions='검수 의견으로 응대를 수정하세요.'))
        edges.append(edge(previous, name))
        previous = name
    config.update(tool_ids=list(dict.fromkeys(n['tool'] for n in nodes)),
                  workflow={'entry': 'docs', 'nodes': nodes, 'edges': edges})
    return config


def test_long_documents_run_real_search_and_keep_verified_evidence(tmp_path, monkeypatch):
    storage, baseline = seeded(tmp_path)
    try:
        policy = storage.agent(baseline)['config']['document_ids'][0]
        text = REQUEST.policy_text + '\n' + ('배송 안내만 포함하는 다른 문서입니다.\n' * 3000)
        asset = storage.asset(policy)
        asset.update(text=text, pages=[text])
        storage.db.execute('UPDATE builder_assets SET json=? WHERE id=?',
                           (json.dumps(asset), policy))
        storage.db.commit()
        config = compose_config(storage, baseline)
        agent = save(storage, config)
        model = MockModel()
        observed = mock_business(monkeypatch)
        run = runtime.run_workflow(storage, agent, REQUEST.customer_message, 'unused', llm=model)
        assert run['status'] == 'succeeded', run.get('error')
        assert sum(len(d['text']) for d in run['outputs']['docs']['documents']) > 45000
        assert model.search_calls == 1 and model.correction_calls == 1
        assert len(observed) == 1
        answer = run['outputs']['answer1']
        assert answer['text'] == model.answers[0]
        assert REQUEST.policy_text in answer['evidence'][0]['quote']
        assert answer['evidence'][0]['file_id'] == policy
        assert '_review_updates' not in answer
    finally:
        storage.close()


def test_latest_rewrite_is_persisted_as_approval_candidate(tmp_path, monkeypatch):
    storage, baseline = seeded(tmp_path)
    mock_business(monkeypatch)
    monkeypatch.setattr('agent_builder.reply_review.run_review', lambda *args: output())
    config = compose_config(storage, baseline, with_review=True)
    agent = save(storage, config)
    model = MockModel()
    run = runtime.run_workflow(storage, agent, REQUEST.customer_message, 'unused',
                               reply_draft=REQUEST.reply_draft, llm=model)
    assert run['status'] == 'succeeded', run.get('error')
    original_report = copy.deepcopy(run['outputs']['review']['reviews'][0]['report'])
    candidate = run['reviews'][0]
    assert candidate['report'] == original_report
    assert candidate['report']['revised_reply'] != model.answers[0]
    assert candidate['candidate_reply'] == model.answers[0]
    assert candidate['candidate_node_id'] == 'answer1'
    assert candidate['candidate_revision'] == 1
    assert candidate['candidate_evidence'] == run['outputs']['answer1']['evidence']
    storage.close()

    storage = Storage(tmp_path)
    try:
        restored = storage.test(run['id'])
        assert restored['reviews'][0]['candidate_reply'] == model.answers[0]
        decided = decide(storage, run['id'], candidate['id'], 'approve', candidate['candidate_reply'])
        assert decided['reviews'][0]['decision']['edited_reply'] == model.answers[0]
        rows = list(csv.DictReader(io.StringIO(approved_csv(storage, agent)['content'].lstrip('\ufeff'))))
        assert len(rows) == 1 and rows[0]['approved_reply'] == model.answers[0]
        assert decided['outputs']['review']['reviews'][0]['report'] == original_report
    finally:
        storage.close()


def test_second_rewrite_uses_latest_candidate_without_mutating_review(tmp_path, monkeypatch):
    storage, baseline = seeded(tmp_path)
    try:
        observed = mock_business(monkeypatch)
        monkeypatch.setattr('agent_builder.reply_review.run_review', lambda *args: output())
        model = MockModel(answers=['첫 번째 수정 응대', '두 번째 최종 응대'])
        agent = save(storage, compose_config(storage, baseline, with_review=True, count=2))
        run = runtime.run_workflow(storage, agent, REQUEST.customer_message, 'unused',
                                   reply_draft=REQUEST.reply_draft, llm=model)
        assert run['status'] == 'succeeded', run.get('error')
        assert len(run['reviews']) == 1
        assert run['reviews'][0]['candidate_reply'] == model.answers[1]
        assert run['reviews'][0]['candidate_revision'] == 2
        assert '첫 번째 수정 응대' in observed[1]['messages'][0]['content']
        assert run['outputs']['review']['reviews'][0]['report']['revised_reply'] == output()['report']['revised_reply']
    finally:
        storage.close()


def test_batch_reviews_rejected_before_compose_model_call(tmp_path, monkeypatch):
    storage, baseline = seeded(tmp_path, table=True)
    try:
        observed = mock_business(monkeypatch)
        monkeypatch.setattr('agent_builder.reply_review.run_review', lambda *args: output())
        agent = save(storage, compose_config(storage, baseline, with_review=True, table=True))
        model = MockModel()
        run = runtime.run_workflow(storage, agent, REQUEST.customer_message, 'unused', llm=model)
        assert run['status'] == 'failed'
        assert '검수 1건씩' in run['error']
        assert not observed and model.search_calls == model.correction_calls == 0
        assert 'answer1' not in run.get('outputs', {})
    finally:
        storage.close()


def test_duplicate_input_review_ids_produce_only_one_candidate(monkeypatch):
    mock_business(monkeypatch)
    review = {'id': 'review:0', **output()}
    docs = [{'source_id': 'policy:1:0', 'file_name': '정책.txt', 'page': None, 'text': REQUEST.policy_text}]
    state = {'request': REQUEST.customer_message,
             'outputs': {'docs': {'documents': docs}, 'review': {'reviews': [review, copy.deepcopy(review)]}}}
    compose = node('answer', 'compose_answer', [bind('documents', 'docs', 'documents'), bind('reviews', 'review', 'reviews')])
    result = runtime.execute_node(compose, state, None, {'instructions': '응대 수정'}, MockModel())
    assert len(result['_review_updates']) == 1
    assert result['_review_updates'][0]['candidate_revision'] == 1
    assert 'candidate_reply' not in review


def test_missing_search_match_preserves_safe_approval_candidate(monkeypatch):
    # Search still executes; no fabricated AI answer replaces missing evidence.
    monkeypatch.setattr(runtime, 'create_deep_agent', lambda **kw: pytest.fail('검색 근거 없이 모델을 호출함'))
    review = {'id': 'review:0', **output()}
    state = {'request': REQUEST.customer_message,
             'outputs': {'docs': {'documents': [{'source_id': 'policy:1:0', 'file_name': '정책.txt',
                                                 'page': None, 'text': '배송 안내\n' * 10000}]},
                         'review': {'reviews': [review]}}}
    compose = node('answer', 'compose_answer', [bind('documents', 'docs', 'documents'), bind('reviews', 'review', 'reviews')])
    model = MockModel(keywords=['환불'])
    result = runtime.execute_node(compose, state, None, {'instructions': '응대 수정'}, model)
    assert model.search_calls == 1 and model.correction_calls == 0
    assert result['text'] == '연결된 문서에서 관련 근거를 찾지 못했습니다.'
    assert result['_review_updates'][0]['candidate_reply'] == result['text']
    assert result['_review_updates'][0]['candidate_evidence'] == []


def test_overlong_review_rewrite_fails_instead_of_creating_unapprovable_candidate(tmp_path, monkeypatch):
    storage, baseline = seeded(tmp_path)
    try:
        mock_business(monkeypatch)
        monkeypatch.setattr('agent_builder.reply_review.run_review', lambda *args: output())
        agent = save(storage, compose_config(storage, baseline, with_review=True))
        run = runtime.run_workflow(storage, agent, REQUEST.customer_message, 'unused',
                                   reply_draft=REQUEST.reply_draft, llm=MockModel(answers=['가' * 12001]))
        assert run['status'] == 'failed'
        assert '12,000자' in run['error']
        assert 'answer1' not in run.get('outputs', {})
        retried = runtime.run_workflow(storage, agent, REQUEST.customer_message, 'unused',
                                       run_id=run['id'], retry=True, llm=MockModel())
        assert retried['status'] == 'succeeded', retried.get('error')
        assert retried['reviews'][0]['candidate_revision'] == 1
        assert 'candidate_reply' not in retried['outputs']['review']['reviews'][0]
    finally:
        storage.close()
