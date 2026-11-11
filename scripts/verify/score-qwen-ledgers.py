"""Build a review template or score ledger evidence against a frozen corpus."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from qwen_live_support import digest


ROOT = Path(__file__).resolve().parents[2]


def inventory(row):
    """Count each requirements candidate and each newly emitted accepted ledger once."""
    candidate, accepted, unparsed = [], [], []
    for i, call in enumerate(row.get('model_calls', [])):
        if call['stage'] != 'requirements' or call['status'] != 'returned':
            continue
        raw = call.get('raw_output')
        try:
            if isinstance(raw,list):
                if len(raw)!=1:raise ValueError('Cannot identify a single SDK generation')
                raw=raw[0]
            content=raw['content']
            if isinstance(content,list):
                content=''.join(part['text'] for part in content if part.get('type')=='text')
            proposal = json.loads(content)
            tasks = proposal['tasks']
            if not isinstance(tasks, list) or any(not isinstance(task,dict) for task in tasks):
                raise TypeError('tasks must be a list')
        except (KeyError, TypeError, ValueError):
            unparsed.append(i)
            continue
        candidate.extend(f'call:{i}/task:{j}' for j in range(len(tasks)))
    for i, stage in enumerate(row.get('stages', [])):
        if stage['stage'] != 'requirements':
            continue
        ledger = stage.get('parsed_state', {}).get('requirement_ledger')
        if isinstance(ledger, dict):
            accepted.extend(f'stage:{i}/task:{j}' for j in range(len(ledger.get('tasks', []))))
    return {'candidate_task_ids':candidate, 'accepted_task_ids':accepted, 'unparsed_requirement_call_indices':unparsed}


def fixed_task_ids(case):
    gold = case['gold']
    return [task['id'] for task in gold['workflow']['tasks']] if gold['final_routing'] == 'READY' else []


def review_template(rows, corpus):
    cases = {c['id']:c for c in corpus['cases']}
    return {'gold_sha256':corpus['gold_sha256'], 'cases':{
        r['id']:{'source_case_sha256':digest(r), 'evidence':None, 'owner_checks':None,
                 'duplicate_candidate_ids':None, 'duplicate_accepted_ids':None,
                 'required_work':dict.fromkeys(fixed_task_ids(cases[r['id']])), 'inventory':inventory(r)}
        for r in rows}}


def question_observations(row):
    counts = Counter()
    stale, question_fields = [], []
    previous = set()
    turns = row.get('turns', [])
    for i, turn in enumerate(turns):
        response = turn['response']
        pending = response.get('pending_questions') or []
        draft = (response.get('draft') or {}).get('questions') or []
        counts['persisted_responses'] += 1
        counts['pending_questions'] += len(pending)
        if i:
            prior = turns[i-1]['response']
            counts['persisted_followup_transitions'] += 1
            counts['same_session_and_thread'] += (bool(row.get('session_id')) and
                row['session_id']==row.get('thread_id')==prior.get('id')==response.get('id'))
            if 'messages' in prior and 'messages' in response:
                counts['observable_history_transitions'] += 1
                counts['prior_history_prefix_preserved'] += response['messages'][:len(prior['messages'])]==prior['messages']
            if isinstance(prior.get('revision'),int) and isinstance(response.get('revision'),int):
                counts['observable_revision_transitions'] += 1
                counts['revision_increased'] += response['revision']>prior['revision']
        for j,q in enumerate(pending or [{'text':t} for t in draft]):
            question_fields.append({'case_id':row['id'],'turn_index':i,'question_index':j,
                'source':'pending_questions' if pending else 'draft_fallback','response_status':response.get('status'),
                'question_id':q.get('id'),'key':q.get('key')})
        if not pending:
            counts['draft_fallback_questions'] += len(draft)
            if response.get('status') != 'needs_clarification':
                for j, question in enumerate(draft):
                    if question in previous:
                        stale.append({'case_id':row['id'], 'turn_index':i, 'draft_question_index':j,
                                      'response_status':response.get('status'), 'question_id':None})
        previous = {q['text'] for q in pending} if pending else set(draft)
    for attempt in row.get('turn_attempts', []):
        counts['submitted_turns'] += 1
        if attempt['status'] == 'worker_error':
            counts['worker_error_without_response'] += 1
        if attempt['turn_index'] > 0:
            counts['submitted_followups'] += 1
            if attempt['status'] == 'returned':counts['returned_followups'] += 1
            if attempt.get('question_id'):
                counts['submitted_question_ids'] += 1
                if attempt['question_id'] in attempt['prior_pending_question_ids']:
                    counts['submitted_question_ids_matching_pending'] += 1
    return counts, stale, question_fields


def checked_duplicates(label, key, available):
    duplicates = label[key]
    if not isinstance(duplicates, list) or len(duplicates) != len(set(duplicates)) or set(duplicates)-set(available):
        raise ValueError(f'{key} must identify reviewed observed task units')
    return len(duplicates)


def score_label(row, case, label, observed):
    if label.get('source_case_sha256') != digest(row) or not label.get('evidence'):
        raise ValueError(f'{row["id"]}: source hash and review evidence required')
    counts = Counter()
    checks = label['owner_checks']
    if not isinstance(checks, list):raise ValueError('owner_checks requires a reviewed list')
    seen = set()
    for check in checks:
        index, key = check['candidate_call_index'], check['owner_key']
        identity = (index,key)
        if (type(index) is not int or not 0 <= index < len(row['model_calls']) or
                not isinstance(key,str) or not key or identity in seen or not check.get('evidence')):
            raise ValueError('Invalid or duplicate candidate call/owner key evidence')
        call = row['model_calls'][index]
        if call.get('status') != 'returned' or call.get('stage') not in ('requirements','condition_check'):
            raise ValueError('Owner comparison requires a returned condition/requirement candidate')
        seen.add(identity)
        candidate, accepted, retained = (check[k] for k in ('candidate_conflict','accepted_conflict','conflicting_candidate_retained'))
        if type(candidate) is not bool or (accepted is not None and type(accepted) is not bool):
            raise ValueError('Invalid reviewed conflict judgment')
        if candidate and accepted is not None:
            if type(retained) is not bool or (retained and not accepted):
                raise ValueError('Conflicting candidate retention contradicts accepted judgment')
            counts['assessable_conflicting_candidates'] += 1
            counts['retained_conflicting_candidates'] += retained
        elif retained is not None:
            raise ValueError('Retention is only evaluated for an assessable conflicting candidate')
        counts['candidate_claims'] += 1
        counts['candidate_conflicts'] += candidate
        if accepted is not None:
            counts['assessable_accepted_claims'] += 1
            counts['accepted_conflicts'] += accepted
        else:
            counts['accepted_claims_unobserved'] += 1
    for scope in ('candidate','accepted'):
        ids = observed[f'{scope}_task_ids']
        counts[f'{scope}_task_units'] += len(ids)
        counts[f'duplicate_{scope}_units'] += checked_duplicates(label,f'duplicate_{scope}_ids',ids)
    work = label['required_work']
    if set(work) != set(fixed_task_ids(case)) or any(v not in ('present','omitted','not_reached') for v in work.values()):
        raise ValueError(f'{row["id"]}: each frozen required task needs a reviewed disposition')
    if 'not_reached' in work.values() and len(set(work.values())) != 1:
        raise ValueError('Ledger not reached applies to the whole final opportunity')
    counts.update(f'work_{disposition}' for disposition in work.values())
    return counts


def ratio(n,d):
    return {'numerator':n,'denominator':d,'value':n/d if d else None}


def summarize(rows, corpus, labels, require_complete=False):
    if labels['gold_sha256'] != corpus['gold_sha256']:raise ValueError('Review gold differs from corpus')
    cases = {c['id']:c for c in corpus['cases']}
    if set(labels['cases'])-{r['id'] for r in rows}:raise ValueError('Review refers to an unmeasured case')
    totals, questions = Counter(), Counter()
    missing, stale, unparsed, reviewed, question_fields = [], [], [], [], []
    for row in rows:
        q, stale_rows, fields = question_observations(row)
        questions.update(q); stale.extend(stale_rows); question_fields.extend(fields)
        observed = inventory(row)
        unparsed.extend({'case_id':row['id'],'call_index':i} for i in observed['unparsed_requirement_call_indices'])
        label = labels['cases'].get(row['id'])
        if label is None:
            missing.append(row['id']);continue
        totals.update(score_label(row,cases[row['id']],label,observed));reviewed.append(row['id'])
    if require_complete and missing:raise ValueError('Required case reviews are missing')
    c = totals
    reached = c['work_present']+c['work_omitted']
    return {'measured_case_count':len(rows),'reviewed_case_ids':reviewed,'unreviewed_case_ids':missing,
            'semantic_counts':dict(totals),'backend_question_and_turn_counts':dict(questions),
            'question_fields':question_fields,'stale_draft_reexposures':stale,'unparsed_returned_requirement_candidates':unparsed,
            'metrics':{
                'candidate_owner_conflict_rate':ratio(c['candidate_conflicts'],c['candidate_claims']),
                'accepted_owner_conflict_rate':ratio(c['accepted_conflicts'],c['assessable_accepted_claims']),
                'conflicting_candidate_retention_rate':ratio(c['retained_conflicting_candidates'],c['assessable_conflicting_candidates']),
                'candidate_duplicate_task_rate':ratio(c['duplicate_candidate_units'],c['candidate_task_units']),
                'accepted_duplicate_task_rate':ratio(c['duplicate_accepted_units'],c['accepted_task_units']),
                'accepted_ledger_task_omission_rate':ratio(c['work_omitted'],reached),
                'end_to_end_ledger_task_unrepresented_rate':ratio(c['work_omitted']+c['work_not_reached'],reached+c['work_not_reached']),
                'stale_draft_reexposure_per_fallback_question':ratio(len(stale),questions['draft_fallback_questions']),
                'question_id_match_per_submitted_question_id':ratio(questions['submitted_question_ids_matching_pending'],questions['submitted_question_ids'])}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    parser.add_argument('--corpus',type=Path,default=ROOT/'docs/qwen-live-evaluation-cases.json')
    parser.add_argument('--case',action='append',help='Limit a partial template or review to selected case IDs')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--template',action='store_true');mode.add_argument('--labels',type=Path)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--require-complete',action='store_true')
    args=parser.parse_args()
    raw=args.run.read_bytes();run=json.loads(raw);corpus=json.loads(args.corpus.read_text(encoding='utf-8'))
    if digest({k:v for k,v in corpus.items() if k!='gold_sha256'})!=corpus['gold_sha256'] or run['gold_sha256']!=corpus['gold_sha256']:
        raise ValueError('Frozen corpus and run do not match')
    rows=[r for r in run['cases'] if r['status'] in ('completed','failed')]
    if args.case:
        if set(args.case)-{r['id'] for r in rows}:raise ValueError('Requested case has no measured result')
        rows=[r for r in rows if r['id'] in args.case]
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('Duplicate measured case')
    if args.require_complete and (run['status']!='completed' or {r['id'] for r in rows}!={c['id'] for c in corpus['cases']}):
        raise ValueError('Final scoring requires the full completed frozen corpus')
    if args.template:
        result=review_template(rows,corpus)
    else:
        label_bytes=args.labels.read_bytes();labels=json.loads(label_bytes)
        result=summarize(rows,corpus,labels,args.require_complete)
        result['labels_file_sha256']=hashlib.sha256(label_bytes).hexdigest()
        result['gold_sha256']=corpus['gold_sha256']
    result['source_run']=str(args.run);result['source_run_file_sha256']=hashlib.sha256(raw).hexdigest()
    with args.output.open('x',encoding='utf-8') as handle:
        json.dump(result,handle,ensure_ascii=False,indent=2);handle.write('\n')


if __name__=='__main__':main()
