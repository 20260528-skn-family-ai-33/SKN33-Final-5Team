"""Approval cannot disappear through selected ports or pinned child workflows."""
import copy
import csv
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from agent_builder.review_decisions import approved_csv, decide
from agent_builder.test_node_connections import save
from agent_builder.test_reply_review import REQUEST, output, seeded
from agent_builder.test_workflow import bind, edge, node
from agent_builder.workflow_runtime import execute_node, run_workflow


REWRITTEN = '환불 신청 후 3일 이내에 안내합니다. 처리 결과는 담당자에게 확인해 주세요.'


class FakeModel:
    def with_structured_output(self, *args, **kwargs):
        return self

    def invoke(self, *args, **kwargs):
        return {'text': REWRITTEN, 'citations': [{'evidence_id': 'e1'}]}


def review_child(tmp_path, monkeypatch):
    st, initial_id = seeded(tmp_path)
    config = copy.deepcopy(st.agent(initial_id)['config'])
    config.update(
        tool_ids=['read_document', 'reply_review', 'compose_answer'],
        workflow={
            'entry': 'docs',
            'nodes': [
                node('docs', 'read_document'),
                node('review', 'reply_review', [bind('documents', 'docs', 'documents')]),
                node('answer', 'compose_answer', [bind('documents', 'docs', 'documents'), bind('reviews', 'review', 'reviews')]),
            ],
            'edges': [edge('docs', 'review'), edge('review', 'answer')],
        },
    )
    monkeypatch.setattr('agent_builder.reply_review.run_review', lambda *args: output())

    class Business:
        def invoke(self, *args, **kwargs):
            return {'structured_response': FakeModel().invoke()}

    monkeypatch.setattr('agent_builder.workflow_runtime.create_deep_agent', lambda **kwargs: Business())
    child_id = save(st, config)
    return st, child_id, config


def reuse_config(base, child_id, output_node='answer', output_port='text', instance='call'):
    config = copy.deepcopy(base)
    config.update(
        tool_ids=['saved_workflow'],
        workflow={
            'entry': instance,
            'nodes': [node(instance, 'saved_workflow', agent_id=child_id, agent_version=1, output_node=output_node, output_port=output_port)],
            'edges': [],
        },
    )
    return config


def add_export(config, instance='call'):
    config = copy.deepcopy(config)
    config['tool_ids'].append('export_result')
    config['workflow']['nodes'].append(node('export', 'export_result', [bind('text', instance, 'text')], format='markdown'))
    config['workflow']['edges'].append(edge(instance, 'export'))
    return config


def run_review(st, agent_id, **kwargs):
    return run_workflow(st, agent_id, REQUEST.customer_message, 'unused', reply_draft=REQUEST.reply_draft, llm=FakeModel(), **kwargs)


def test_reused_text_keeps_review_and_exports_only_human_approved_reply(tmp_path, monkeypatch):
    st, child_id, base = review_child(tmp_path, monkeypatch)
    try:
        parent_id = save(st, reuse_config(base, child_id))
        run = run_review(st, parent_id)
        assert run['status'] == 'succeeded', run.get('error')
        assert run['outputs']['call']['text'] == REWRITTEN
        assert run['approval_status'] == 'pending'
        assert len(run['reviews']) == 1
        assert run['reviews'][0]['id'] == 'call:0'
        assert run['reviews'][0]['source_review_id'] == 'review:0'
        assert run['reviews'][0]['candidate_reply'] == REWRITTEN
        assert run['reviews'][0]['report']['revised_reply'] != REWRITTEN
        assert (tmp_path / run['review_report_path']).is_file()
        assert run['artifact'] is None
        assert list(csv.DictReader(io.StringIO(approved_csv(st, parent_id)['content'].lstrip('\ufeff')))) == []
        decide(st, run['id'], 'call:0', 'approve', '사람이 수정한 최종 응대')
        rows = list(csv.DictReader(io.StringIO(approved_csv(st, parent_id)['content'].lstrip('\ufeff'))))
        assert len(rows) == 1
        assert rows[0]['approved_reply'] == '사람이 수정한 최종 응대'
    finally:
        st.close()


@pytest.mark.parametrize('nested', [False, True])
def test_saving_export_after_review_dependency_is_rejected(tmp_path, monkeypatch, nested):
    st, child_id, base = review_child(tmp_path, monkeypatch)
    try:
        output_node = 'answer'
        if nested:
            child_id = save(st, reuse_config(base, child_id))
            output_node = 'call'
        unsafe = add_export(reuse_config(base, child_id, output_node=output_node))
        versions_before = st.db.execute('SELECT COUNT(*) FROM agent_versions').fetchone()[0]
        with pytest.raises(ValueError, match='승인'):
            save(st, unsafe)
        assert st.db.execute('SELECT COUNT(*) FROM agent_versions').fetchone()[0] == versions_before
    finally:
        st.close()


def test_existing_pinned_version_cannot_export_or_retry_past_approval(tmp_path, monkeypatch):
    st, child_id, base = review_child(tmp_path, monkeypatch)
    try:
        parent_id = save(st, reuse_config(base, child_id))
        legacy = copy.deepcopy(st.agent(parent_id))
        legacy['config'] = add_export(legacy['config'])
        run = run_review(st, parent_id, agent_snapshot=legacy)
        assert run['status'] == 'failed'
        assert '승인' in run['error']
        assert run['steps'][-1]['node'] == 'workflow'
        assert run['artifact'] is None
        assert not list((tmp_path / 'reports').glob('*.md'))
        retried = run_review(st, parent_id, run_id=run['id'], retry=True)
        assert retried['status'] == 'failed'
        assert '승인' in retried['error']
        assert retried['artifact'] is None
        assert not list((tmp_path / 'reports').glob('*.md'))
    finally:
        st.close()


def test_two_review_calls_have_distinct_approval_ids(tmp_path, monkeypatch):
    st, child_id, base = review_child(tmp_path, monkeypatch)
    try:
        parent = reuse_config(base, child_id, instance='first')
        parent['workflow']['nodes'].append(node('second', 'saved_workflow', [bind('text', 'first', 'text')], agent_id=child_id, agent_version=1, output_node='answer', output_port='text'))
        parent['workflow']['edges'].append(edge('first', 'second'))
        parent_id = save(st, parent)
        run = run_review(st, parent_id)
        assert run['status'] == 'succeeded', run.get('error')
        assert {r['id'] for r in run['reviews']} == {'first:0', 'second:0'}
        assert all(re.fullmatch(r'[-\w]+:\d+', r['id']) for r in run['reviews'])
        decide(st, run['id'], 'first:0', 'approve', '첫 번째 승인문')
        decided = decide(st, run['id'], 'second:0', 'reject', '')
        assert decided['approval_status'] == 'decided'
        rows = list(csv.DictReader(io.StringIO(approved_csv(st, parent_id)['content'].lstrip('\ufeff'))))
        assert [r['approved_reply'] for r in rows] == ['첫 번째 승인문']
    finally:
        st.close()


def test_nested_review_metadata_preserves_original_source_id(tmp_path, monkeypatch):
    st, child_id, base = review_child(tmp_path, monkeypatch)
    try:
        middle_id = save(st, reuse_config(base, child_id, instance='inner'))
        parent_id = save(st, reuse_config(base, middle_id, output_node='inner', instance='outer'))
        run = run_review(st, parent_id)
        assert run['status'] == 'succeeded', run.get('error')
        assert len(run['reviews']) == 1
        assert run['reviews'][0]['id'] == 'outer:0'
        assert run['reviews'][0]['source_review_id'] == 'review:0'
        assert run['approval_status'] == 'pending'
        assert decide(st, run['id'], 'outer:0', 'approve', '중첩 실행 최종 응대')['approval_status'] == 'decided'
    finally:
        st.close()


def test_selected_reviews_subset_preserves_public_ids_without_losing_other_reports(tmp_path, monkeypatch):
    st, child_id, base = review_child(tmp_path, monkeypatch)
    try:
        child = copy.deepcopy(base)
        child['tool_ids'] = ['reply_review']
        child['workflow'] = {'entry': 'early', 'nodes': [node('early', 'reply_review'), node('selected', 'reply_review')], 'edges': [edge('early', 'selected')]}
        child_id = save(st, child)
        parent_id = save(st, reuse_config(base, child_id, output_node='selected', output_port='reviews'))
        run = run_review(st, parent_id)
        assert run['status'] == 'succeeded', run.get('error')
        selected = run['outputs']['call']['reviews']
        assert len(selected) == 1
        assert selected[0]['id'] == 'call:0'
        assert selected[0]['source_review_id'] == 'selected:0'
        assert {r['id'] for r in run['reviews']} == {'call:0', 'call:1'}
        assert {r['source_review_id'] for r in run['reviews']} == {'early:0', 'selected:0'}
    finally:
        st.close()


def test_reused_reviews_port_receives_latest_child_candidate(tmp_path, monkeypatch):
    st, child_id, base = review_child(tmp_path, monkeypatch)
    try:
        parent_id = save(st, reuse_config(base, child_id, output_node='review', output_port='reviews'))
        run = run_review(st, parent_id)
        assert run['status'] == 'succeeded', run.get('error')
        selected = run['outputs']['call']['reviews'][0]
        assert selected['id'] == run['reviews'][0]['id'] == 'call:0'
        assert selected['candidate_reply'] == REWRITTEN
        assert selected['candidate_revision'] == 1
    finally:
        st.close()


def test_runtime_export_rejects_hidden_review_metadata_even_without_declared_review_node():
    export = node('export', 'export_result', [bind('text', 'call', 'text')], format='markdown')
    config = {'workflow': {'nodes': [export], 'edges': []}}
    state = {'outputs': {'call': {'text': '승인되지 않은 응대', '_reviews': [{'id': 'call:0', 'report': output()['report']}]}}}
    with pytest.raises(ValueError, match='승인'):
        execute_node(export, state, None, config, None)


def test_reused_review_report_can_still_save_pending_diagnostics(tmp_path, monkeypatch):
    st, child_id, base = review_child(tmp_path, monkeypatch)
    try:
        child = copy.deepcopy(base)
        child['tool_ids'] = ['reply_review', 'review_summary', 'write_result']
        child['workflow'] = {
            'entry': 'review',
            'nodes': [node('review', 'reply_review'), node('summary', 'review_summary', [bind('reviews', 'review', 'reviews')]), node('write', 'write_result', [bind('table', 'summary', 'table')], format='csv')],
            'edges': [edge('review', 'summary'), edge('summary', 'write')],
        }
        child_id = save(st, child)
        parent_id = save(st, reuse_config(base, child_id, output_node='write', output_port='artifact'))
        run = run_review(st, parent_id)
        assert run['status'] == 'succeeded', run.get('error')
        assert run['approval_status'] == 'pending'
        assert run['artifact']['review_report'] is True
        assert (tmp_path / run['report_path']).is_file()
        rows = list(csv.DictReader(io.StringIO(run['artifact']['content'].lstrip('\ufeff'))))
        assert len(rows) == 1 and rows[0]['approval_state'] == 'pending'
    finally:
        st.close()
