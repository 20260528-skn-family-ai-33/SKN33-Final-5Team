"""Regression checks for canonical reports, candidate updates and approval history."""
import copy
import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_builder.review_state import collect_reviews, candidate_reply, is_review_report_artifact
from agent_builder.review_decisions import decide, approved_csv
from agent_builder.storage import Storage
from agent_builder.test_node_connections import save
from agent_builder.test_reply_review import seeded, output
from agent_builder.test_workflow import node, bind, edge
from agent_builder.workflow_runtime import run_workflow, execute_node


def test_latest_candidate_survives_later_stale_pass_through():
    original = {'id': 'review:0', **output()}
    updated = {**original, 'candidate_reply': '첫 수정', 'candidate_revision': 1}
    final = {**original, 'candidate_reply': '최종 수정', 'candidate_revision': 2}
    outputs = {'review': {'reviews': [original]}, 'first': {'_review_updates': [updated]},
               'second': {'_review_updates': [final]}, 'condition': {'reviews': [original]}}
    result = collect_reviews(outputs)
    assert len(result) == 1
    assert candidate_reply(result[0]) == '최종 수정'
    assert result[0]['report'] == original['report']
    assert candidate_reply(original) == original['report']['revised_reply']


def report_flow(config):
    config = copy.deepcopy(config)
    config['workflow']['nodes'] += [
        node('decision', 'condition', [bind('reviews', 'review', 'reviews')], condition_operator='any_revision'),
        node('report', 'review_summary', [bind('reviews', 'decision', 'reviews')]),
        node('save', 'write_result', [bind('table', 'report', 'table')], format='csv'),
        node('done', 'finish', instructions='통과 안내')]
    config['workflow']['edges'] += [edge('review', 'decision'), edge('decision', 'report', 'yes'),
                                    edge('decision', 'done', 'no'), edge('report', 'save')]
    config['tool_ids'] = ['read_table', 'reply_review', 'condition', 'review_summary', 'write_result', 'finish']
    return config


def test_condition_reports_are_unique_and_decisions_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setattr('agent_builder.reply_review.run_review', lambda *a: output())
    st, id = seeded(tmp_path, table=True)
    parent = save(st, report_flow(st.agent(id)['config']))
    run = run_workflow(st, parent, '검수', 'unused', llm=object())
    assert run['status'] == 'succeeded', run.get('error')
    assert [r['id'] for r in run['reviews']] == ['review:0', 'review:1']
    assert len(run['rows']) == 2
    report = json.loads((tmp_path / run['review_report_path']).read_text())
    assert len(report['reviews']) == 2
    rid = run['id']
    st.close()
    st = Storage(tmp_path)
    decide(st, rid, 'review:0', 'approve', '운영자가 승인한 문장')
    again = decide(st, rid, 'review:0', 'approve', '운영자가 승인한 문장')
    assert len(again['reviews']) == 2
    decide(st, rid, 'review:1', 'reject', '')
    rows = list(csv.DictReader(io.StringIO(approved_csv(st, parent)['content'].lstrip('\ufeff'))))
    assert len(rows) == 1
    assert rows[0]['approved_reply'] == '운영자가 승인한 문장'
    assert st.test(rid)['approval_status'] == 'decided'
    st.close()


def test_summary_uses_latest_candidate_without_modifying_original():
    original = {'id': 'review:0', **output()}
    updated = {**original, 'candidate_reply': '후속 최종 문장', 'candidate_revision': 1}
    state = {'outputs': {'review': {'reviews': [original]}, 'compose': {'_review_updates': [updated]}}}
    result = execute_node(node('summary', 'review_summary', [bind('reviews', 'review', 'reviews')]), state, None, {}, None)
    assert result['table']['rows'][0]['candidate_reply'] == '후속 최종 문장'
    assert result['table']['rows'][0]['approval_state'] == 'pending'
    assert original['report']['revised_reply'] == output()['report']['revised_reply']


def test_non_report_artifact_is_not_published_after_review(tmp_path, monkeypatch):
    monkeypatch.setattr('agent_builder.reply_review.run_review', lambda *a: output())
    st, id = seeded(tmp_path, table=True)
    config = report_flow(st.agent(id)['config'])
    parent = save(st, config)

    def executor(n, state, storage, cfg, llm):
        if n['tool'] == 'write_result':
            return {'artifact': {'name': 'candidate.csv', 'content': '미승인 응대', 'mime': 'text/csv'}}
        return execute_node(n, state, storage, cfg, llm)

    run = run_workflow(st, parent, '검수', 'unused', llm=object(), executor=executor)
    assert run['status'] == 'failed'
    assert '승인' in run['error']
    assert run['artifact'] is None
    assert not run.get('report_path')
    assert not (tmp_path / 'reports' / (run['id'] + '.csv')).exists()
    st.close()


def test_legacy_diagnostic_artifact_is_identified_by_its_actual_writer_input(tmp_path, monkeypatch):
    monkeypatch.setattr('agent_builder.reply_review.run_review', lambda *a: output())
    st, id = seeded(tmp_path, table=True)
    config = report_flow(st.agent(id)['config'])
    parent = save(st, config)

    def legacy_writer(n, state, storage, cfg, llm):
        result = execute_node(n, state, storage, cfg, llm)
        if n['tool'] == 'write_result':
            result['artifact'].pop('review_report')
        return result

    run = run_workflow(st, parent, '검수', 'unused', llm=object(), executor=legacy_writer)
    assert run['status'] == 'succeeded', run.get('error')
    assert run['approval_status'] == 'pending'
    assert 'review_report' not in run['artifact']
    assert (tmp_path / run['report_path']).is_file()
    assert is_review_report_artifact(config, run['outputs'], run['artifact'])
    unrelated = {**run['artifact'], 'name': '최종_응대.csv'}
    assert not is_review_report_artifact(config, run['outputs'], unrelated)
    assert not is_review_report_artifact(config, {}, {**unrelated, 'review_report': 'true'})
    st.close()
