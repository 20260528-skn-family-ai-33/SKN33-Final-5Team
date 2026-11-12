"""Replay one-turn synthetic table cases from recorded requirement responses and draft(s)."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Backend'))

from agent_builder.capabilities.capability_gate import input_fingerprint, require_save_ready
from agent_builder.conditions.condition_check import audit_conditions
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_ledger import make_requirement_check
from infrastructure.persistence.storage import Storage
from qwen_live_support import can_execute_locally, digest
from workflow_engine.runner import run_workflow


class Recorded:
    def __init__(self, calls):
        self.responses = iter(c['raw_output']['content'] for c in calls if c['stage'] == 'requirements')
        self.count = 0

    def invoke(self, messages):
        self.count += 1
        return {'content': next(self.responses)}


def replay(case, gold, directory):
    if (len(case['turns']) != 1 or not gold['resource_state']['synthetic_only']
            or gold['gold']['execution']['external_execution_allowed']):
        raise ValueError('Requires a single-turn synthetic table case without external execution')
    stages = case['stages']
    snapshot = next(s for s in stages if s['stage'] == 'requirements')
    response = case['turns'][0]['response']
    state = dict(messages=[{'role':'user', 'content':case['request']}], resources=response['resources'],
                 session_id=case['session_id'], model=response['model'], credential_id='local-model',
                 user_timezone=gold['user_timezone'], **snapshot['ledger_before'])
    state['capability_assessment'] = next(s['parsed_state']['capability_assessment'] for s in stages
                                          if s['stage'] == 'capability_check')
    state['capability_enforced'] = True
    state['capability_fingerprint'] = input_fingerprint(state)
    transport = Recorded(case['model_calls'])
    state.update(make_requirement_check('recorded', transport, 'enforce')(state))
    if state.get('requirement_route') not in ('plan', 'template'):
        raise ValueError(f"{case['id']}: requirement replay returned {state.get('requirement_route')}")
    drafts = [s['parsed_state']['draft'] for s in stages if s['stage'] == 'conversation' and s['parsed_state'].get('draft')]
    if not drafts:
        raise ValueError('No recorded workflow draft to replay')
    result = []
    for index, draft in enumerate(drafts):
        current = {**state, 'draft': deepcopy(draft), 'issues': [], 'semantic_issues': [], 'trace': []}
        current.update(audit_conditions(current))
        current.update(validate_node(current))
        if current['status'] != 'ready' or current['issues']:
            raise ValueError(f"{case['id']}: {current['issues']}")
        current['confirmed'] = True
        require_save_ready(current)
        row = {'draft': index + 1, 'validator': 'ready', 'approved_save_gate': True}
        if gold['gold']['execution']['mode'] == 'local_synthetic':
            if not can_execute_locally(current['draft']):
                raise ValueError('Recorded draft is outside the synthetic execution allowlist')
            with tempfile.TemporaryDirectory(dir=directory, ignore_cleanup_errors=True) as scratch:
                storage = Storage(scratch)
                try:
                    assets = gold['resource_state']['resources']
                    for asset in assets:
                        storage.add_asset({k:v for k,v in asset.items() if k != 'rows'}, asset.get('rows', []))
                    agent_id = storage.save_agent(current)
                    run = run_workflow(storage, agent_id, '', '', file_ids=[a['id'] for a in assets])
                    if run['status'] != 'succeeded' or run['rows'] != gold['gold']['execution']['expected_rows']:
                        raise ValueError(f"{case['id']}: synthetic output differs")
                    row['expected_rows_verified'] = len(run['rows'])
                finally:
                    storage.close()
        result.append(row)
    return {'id': case['id'], 'saved_requirement_responses_used': transport.count, 'drafts': result}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--corpus', required=True, type=Path)
    parser.add_argument('--case', required=True, action='append')
    parser.add_argument('--scratch', required=True, type=Path)
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_bytes())
    run = json.loads(args.run.read_bytes())
    if digest({k:v for k,v in corpus.items() if k != 'gold_sha256'}) != corpus['gold_sha256'] or run['gold_sha256'] != corpus['gold_sha256']:
        raise ValueError('Frozen corpus and trace must match')
    gold_by_id = {c['id']: c for c in corpus['cases']}
    runs = {c['id']: c for c in run['cases']}
    args.scratch.mkdir(parents=True, exist_ok=True)
    for identifier in args.case:
        print(json.dumps(replay(runs[identifier], gold_by_id[identifier], args.scratch)))
