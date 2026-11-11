"""Replay archived meaning/requirement responses and generated graphs. No network or execution."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Backend'))

from agent_builder.capabilities.capability_gate import input_fingerprint, require_save_ready
from agent_builder.conditions.condition_check import audit_conditions
from agent_builder.design.gmail_summary import gmail_summary_reply
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.request_meaning import make_meaning_node
from agent_builder.design.requirement_ledger import make_requirement_check
from agent_builder.design.temporal_context import ensure_temporal_context
from qwen_live_support import digest


class Captured:
    def __init__(self, case, stage):
        self.responses = iter(call['raw_output']['content'] for call in case['model_calls'] if call['stage'] == stage)

    def invoke(self, messages):
        content = next(self.responses, None)
        if content is None:
            raise ValueError('No further recorded response')
        return dict(content=content)


def verify_summary_consumers(state, frozen):
    ledger = state.get('requirement_ledger') or {}
    tasks = ledger.get('tasks', [])
    roles = {'read':'read_document', 'document_analysis':'summary', 'export':'markdown_output', 'email_send':'send'}
    by_role = {roles[t['spec']['kind']]: t for t in tasks if t['spec']['kind'] in roles}
    units = frozen['gold']['review_contract']['ledger_units']
    if (len(tasks) != len(by_role) or set(by_role) != set(units['requested_steps'])
            or state.get('requirement_route') != 'plan' or ledger['unresolved'] != units['unresolved_user_inputs']
            or state.get('pending_questions') != []):
        raise ValueError('Saved summary requirements differ from the frozen tasks or clarification policy')
    role_by_id = {t['id']: role for role, t in by_role.items()}
    dependencies = {(role_by_id[parent], role) for role, t in by_role.items() for parent in t['depends_on']}
    delivery = by_role['send']['spec']
    explicit = units['explicit_slot_values']
    file_ids = [r['id'] for r in frozen['resource_state']['resources']]
    if (len(role_by_id) != len(tasks) or dependencies != set(map(tuple, units['dependencies']))
            or by_role['read_document']['spec']['file_ids'] != file_ids
            or by_role['markdown_output']['spec']['format'] != explicit['output.format']
            or delivery != dict(kind='email_send', to=explicit['recipient'], subject=explicit['subject'], body=None)
            or by_role['summary']['spec']['request'] != frozen['request']):
        raise ValueError('Summary source, delivery slots, or dependencies differ from frozen gold')
    return {role: dict(id=t['id'], depends_on=t['depends_on']) for role,t in by_role.items()}


def replay_delivery_responses(state, calls, drafts, frozen):
    """Feed immutable model replies back through compilation, audit, validation and the save gate."""
    from jsonschema import Draft202012Validator
    from langchain_core.messages import AIMessage
    from agent_builder.design.nodes.conversation import make_conversation
    from workflow_engine.execution_path import artifact_origin, artifact_output_ids, execution_path

    replies = [c['raw_output'] for c in calls['model_calls'] if c['stage'] == 'conversation']
    if len(replies) != len(drafts):
        raise ValueError('Cannot pair every recorded generation with its archived draft')
    results = []
    for attempt, (reply, archive) in enumerate(zip(replies, drafts), start=1):
        if len(reply) != 1 or reply[0].get('tool_calls'):
            raise ValueError('Expected a single, complete recorded assistant reply')
        content = reply[0]['content']
        result = dict(attempt=attempt, raw_content_sha256=hashlib.sha256(content.encode('utf-8')).hexdigest())
        # The captured transport can return older output regardless of the new
        # decoder. Record schema rejection separately from the runtime defenses.
        def agent_factory(**options):
            result['raw_permitted_by_decoder'] = Draft202012Validator(
                options['response_format'].schema).is_valid(json.loads(content))
            return SimpleNamespace(invoke=lambda payload, config: {'messages': [AIMessage(content=content)]})

        current = {**state, 'issues': [], 'semantic_issues': [], 'trace': []}
        settings = {'A4B_MAIN_PROVIDER':'ollama', 'A4B_PLANNER_MODE':'deepagent', 'A4B_BINDING_REPAIR_MODE':'off',
                    'A4B_REQUIREMENT_GENERATION_MODE':'baseline', 'A4B_TOOL_ADVICE_MODE':'hint',
                    'A4B_TOOL_ADVICE_BACKEND':'svm56', 'A4B_TOOL_CATALOG_MODE':'model'}
        with patch.dict(os.environ, settings), \
                patch('agent_builder.design.nodes.conversation.create_deep_agent', agent_factory):
            current.update(make_conversation(None, state['model'], model_factory=object)(current))
        result['generated_graph_matches_archive'] = current['draft']['workflow'] == archive['workflow']
        current.update(audit_conditions(current))
        current.update(validate_node(current))
        result.update(status=current['status'], issues=current['issues'])
        results.append(result)
        if current['status'] != 'ready' or current['issues']:
            continue
        require_save_ready({**current, 'confirmed':True})
        workflow = current['draft']['workflow']
        nodes = execution_path(workflow)
        summary, = [n for n in nodes if n['tool']=='compose_answer']
        writer, = [n for n in nodes if n['tool']=='export_result']
        send, = [n for n in nodes if n['tool']=='email.send']
        source, = [n for n in nodes if n['tool']=='read_document']
        slots = frozen['gold']['final_slots']
        source_ids = [r['id'] for r in frozen['resource_state']['resources']]
        args = json.loads(send['settings']['arguments_json'])
        summary_text = dict(node_id=summary['id'], output='text')
        origins = {artifact_origin(workflow, n) for n in artifact_output_ids(workflow)}
        if (source['settings']['file_ids'] != source_ids or summary['bindings'] != [
                dict(input='documents', node_id=source['id'], output='documents')]
                or writer['settings']['format'] != 'markdown' or writer['bindings'] != [dict(input='text', **summary_text)]
                or origins != {writer['id']} or send['bindings'] != [dict(input='body', **summary_text)]
                or send['settings']['operation'] != 'execute' or set(args) != {'to','subject','body'}
                or args['to'] not in (slots['recipient'], [slots['recipient']]) or args['subject'] != slots['subject']
                or args['body'] != {'$binding':'body'}):
            raise ValueError('Generated workflow does not share the frozen summary between the file and email body')
        original_send, = [n for n in archive['workflow']['nodes'] if n['id'] == send['id']]
        unchanged_graph = deepcopy(workflow)
        unchanged_send = next(n for n in unchanged_graph['nodes'] if n['id'] == send['id'])
        unchanged_send['bindings'] = original_send['bindings']
        unchanged_send['settings']['arguments_json'] = original_send['settings']['arguments_json']
        if unchanged_graph != archive['workflow']:
            raise ValueError('Replay changed fields outside the intended email arguments and bindings')
        result.update(save_gate='passed', shared_output=summary_text, recipients=args['to'], subject=args['subject'])
    if any(r['status'] != 'ready' or r['issues'] for r in results):
        raise ValueError('Recorded delivery replay failed: ' + json.dumps(results, ensure_ascii=False))
    return results


def replay(case, frozen, turn=0):
    stages = [s for s in case['stages'] if s['turn_index']==turn]
    original = case['turns'][turn]
    snapshot = next(s for s in stages if s['stage'] == 'requirements')
    state = dict(messages=[], resources=original['response']['resources'],
                 session_id=case['session_id'], model=original['response']['model'], credential_id='local-model',
                 user_timezone=frozen['user_timezone'], **snapshot['ledger_before'])
    for prior in case['turns'][:turn+1]:
        state['messages'].append(dict(role='user', content=prior['user']))
        state.update(ensure_temporal_context(state, now=prior['reference_time']))
    calls = dict(model_calls=[m for m in case['model_calls'] if m['turn_index']==turn])
    state.update(make_meaning_node('replay', transport=Captured(calls, 'request_meaning'))(state))
    state['capability_assessment'] = next(s['parsed_state']['capability_assessment'] for s in stages if s['stage']=='capability_check')
    state.update(capability_enforced=True, capability_fingerprint=input_fingerprint(state))
    state.update(make_requirement_check('replay', Captured(calls, 'requirements'), 'enforce')(state))
    answer = dict(id=case['id'], turn=turn, requirements=state.get('requirement_route'), questions=state.get('pending_questions'),
                  task_kinds=[t['spec']['kind'] for t in (state.get('requirement_ledger') or {}).get('tasks', [])])
    drafts = [s['parsed_state']['draft'] for s in stages if s['stage']=='conversation' and s['parsed_state'].get('draft')]
    answer['recorded_drafts'] = len(drafts)
    if frozen['gold']['intent'] == 'summary_artifact_and_mail':
        answer['verified_ledger'] = verify_summary_consumers(state, frozen)
    if not drafts and 'verified_ledger' in answer:
        answer['workflow_verification'] = 'no_recorded_draft'
        return answer
    if 'verified_ledger' in answer:
        answer['raw_response_replays'] = replay_delivery_responses(state, calls, drafts, frozen)
        return answer
    if not drafts or state.get('requirement_route') not in ('plan','template'):
        raise ValueError(f"{case['id']}: no eligible archived graph for calendar verification")
    template = gmail_summary_reply(state)
    if template:
        state.update(template)
        answer['workflow_source'] = 'existing_gmail_compiler'
    else:
        state.update(draft=deepcopy(drafts[-1]), issues=[], semantic_issues=[])
        answer['workflow_source'] = 'recorded_final_draft'
    answer['original_arguments'] = next((n['settings']['arguments_json'] for n in drafts[-1]['workflow']['nodes']
                                         if n['tool']=='email.list'), None)
    state.update(audit_conditions(state))
    state.update(validate_node(state))
    answer['status'], answer['issues'] = state['status'], state['issues']
    answer['arguments'] = next((json.loads(n['settings']['arguments_json']) for n in state['draft']['workflow']['nodes']
                                if n['tool']=='email.list'), None)
    if state['status']=='ready' and not state['issues']:
        require_save_ready({**state, 'confirmed':True})
        answer['save_gate'] = 'passed'
    window = frozen['gold']['review_contract'].get('date_window')
    if window:
        terms = (answer.get('arguments') or {}).get('query', '').split()
        expected = [f"after:{window['start_inclusive_epoch']}", f"before:{window['end_exclusive_epoch']}"]
        bounds = [term for term in terms if term.startswith(('after:', 'before:'))]
        if sorted(bounds) != sorted(expected) or 'is:unread' in terms or answer.get('save_gate') != 'passed':
            raise ValueError(f"{case['id']}: stored graph does not satisfy frozen local day: {answer}")
        answer['frozen_window_verified'] = True
    return answer


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', required=True, type=Path)
    parser.add_argument('--corpus', required=True, type=Path)
    parser.add_argument('--ids', nargs='+', default=['H13','H14','H15','H16','H32'])
    args = parser.parse_args()
    run = json.loads(args.trace.read_bytes())
    corpus = json.loads(args.corpus.read_bytes())
    gold_sha = digest({k:v for k,v in corpus.items() if k != 'gold_sha256'})
    if gold_sha != corpus.get('gold_sha256') or run.get('gold_sha256') != gold_sha:
        raise ValueError('Frozen corpus digest and recorded evaluation digest must match')
    scenarios = {c['id']:c for c in corpus['cases']}
    recorded = {c['id']:c for c in run['cases']}
    if (len(recorded) != len(run['cases']) or len(scenarios) != len(corpus['cases'])
            or not set(args.ids) <= recorded.keys() & scenarios.keys()):
        raise ValueError('Requested case missing or case IDs duplicated')
    for identifier in args.ids:
        row = recorded[identifier]
        if not row['turns']:
            raise ValueError(f'{identifier}: no completed turn to replay')
        for turn in range(len(row['turns'])):
            print(json.dumps(replay(row, scenarios[identifier], turn), ensure_ascii=False))
