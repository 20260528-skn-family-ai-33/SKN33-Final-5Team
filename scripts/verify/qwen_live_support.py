"""Evaluation-only helpers. Unknown/adjudication-needed scores never count as success."""
import hashlib
import json
import math
import re
from statistics import median


METRICS = ['intent_accuracy', 'slot_extraction_f1', 'routing_macro_f1', 'necessary_question_recall',
           'unnecessary_question_rate', 'repeated_question_rate', 'answer_incorporation_accuracy',
           'clarification_completion_rate', 'workflow_schema_validity', 'tool_operation_accuracy',
           'requirement_coverage', 'workflow_execution_success', 'latency_p50_seconds', 'latency_p95_seconds']


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def sanitize(value, secrets=()):
    if isinstance(value, dict):
        return {str(k): sanitize(v, secrets) for k, v in value.items()
                if not re.search(r'authorization|api.?key|access.?token|refresh.?token|password|cookie', str(k), re.I)}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret: value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [REDACTED]', value)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[REDACTED]', value)
        value = re.sub(r'[\w.+-]+@(?!(?:[\w.-]+\.)?invalid\b)[\w.-]+\.[A-Za-z]{2,}', '[REDACTED_EMAIL]', value)
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(type(value).__name__)


ORIGINAL_GOLD_SHA256 = 'e939c051a940915c4061b45221e7f1353b8ce2157677d379ae193de13b1300da'


def validate_corpus(corpus, catalog, profile='original-52', expected_gold_sha=None):
    copy = dict(corpus); claimed = copy.pop('gold_sha256')
    if digest(copy) != claimed: raise ValueError('Frozen gold digest mismatch')
    cases = corpus['cases']; ids = [c['id'] for c in cases]
    if len(ids) != len(set(ids)): raise ValueError('Duplicate case ID')
    if profile == 'original-52':
        if claimed != ORIGINAL_GOLD_SHA256 or len(cases) != 52:
            raise ValueError('Original 52-case frozen gold digest mismatch')
    elif profile == 'runpod-rebuild':
        if not expected_gold_sha or claimed != expected_gold_sha or claimed == ORIGINAL_GOLD_SHA256:
            raise ValueError('New corpus must match its independently frozen --expected-gold-sha')
        if len(cases) < 30 or any(not c['eligible'] for c in cases):
            raise ValueError('New corpus requires at least 30 eligible cases')
        if any(c['split'] not in ('development', 'holdout') for c in cases):
            raise ValueError('Unknown corpus split')
    else:
        raise ValueError('Unknown corpus profile')
    for case in cases:
        if not case['resource_state']['synthetic_only']: raise ValueError('Non-synthetic resource')
        if case['gold']['execution']['external_execution_allowed']: raise ValueError('External execution prohibited')
        for task in case['gold']['tools_and_operations']:
            for option in task['alternatives']:
                tool = catalog.get(option['tool'])
                if tool is None: raise ValueError('Unknown gold tool')
                if option['operation'] is not None and option['operation'] not in [o['id'] for o in tool.get('operation_contracts', [])]:
                    raise ValueError('Unknown gold operation')
    return True


def choose_answer(case, question, answered):
    """Do not inject a scripted answer when the actual question is unrelated/ambiguous."""
    text = question.get('text', '')
    matches = [a for a in case['answer_policy'] if any(term in text for term in a['question_terms'])]
    if not matches: return {'status':'unmatched_question', 'text':None, 'slots':[]}
    texts = list(dict.fromkeys(a['text'] for a in matches))
    if len(texts) != 1: return {'status':'ambiguous_question', 'text':None, 'slots':[]}
    slots = [a['slot'] for a in matches]
    if all(s in answered for s in slots): return {'status':'repeated_question', 'text':None, 'slots':slots}
    return {'status':'matched', 'text':texts[0], 'slots':slots}


def route(result):
    return {'ready':'READY','saved':'READY','needs_clarification':'CLARIFY','unsupported':'UNSUPPORTED'}.get(result.get('status'),'SYSTEM_ERROR')


def can_execute_locally(draft):
    nodes = ((draft or {}).get('workflow') or {}).get('nodes', [])
    allowed = {'read_table','filter_sort','aggregate_table','calculate_columns','write_result','finish'}
    # No model-authored file paths, connectors, arbitrary agents, HTTP or saved nested workflows.
    return bool(nodes) and all(n.get('tool') in allowed and not (n.get('settings') or {}).get('operation') for n in nodes)


def task_scores(draft, gold):
    nodes = ((draft or {}).get('workflow') or {}).get('nodes', [])
    scores = []
    for task in gold['workflow']['tasks']:
        matched = [n['id'] for n in nodes if any(n.get('tool')==a['tool'] and (n.get('settings') or {}).get('operation')==a['operation'] for a in task['alternatives'])]
        scores.append({'task_id':task['id'],'tool_operation_present':bool(matched),'candidate_node_ids':matched,
                       'semantic_coverage':None,'dependency_correct':None})
    # Presence is only a partial score; parameters/data flow require independent checks or adjudication.
    return scores


def metrics(records):
    out = {name:{'value':None,'numerator':0,'denominator':0,'unit':'case','status':'not_measured'} for name in METRICS}
    observed = [r for r in records if r.get('status') in ('completed','failed') and r.get('live_model_calls',0)>0]
    routed = [r for r in observed if r.get('expected_route') and r.get('actual_route')]
    if routed:
        f1 = {}
        for label in ['READY','CLARIFY','UNSUPPORTED']:
            tp=sum(r['expected_route']==label and r['actual_route']==label for r in routed)
            fp=sum(r['expected_route']!=label and r['actual_route']==label for r in routed)
            fn=sum(r['expected_route']==label and r['actual_route']!=label for r in routed)
            f1[label]=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None
        values=[v for v in f1.values() if v is not None]
        out['routing_macro_f1']={'value':sum(values)/len(values),'denominator':len(routed),'unit':'case',
                                 'per_class':f1,'status':'measured','system_errors_count_as_expected_class_false_negative':True}
    mapping={'workflow_schema_validity':'schema_valid','workflow_execution_success':'execution_success'}
    for name,key in mapping.items():
        selected=[r for r in observed if isinstance(r.get(key),bool)]
        if selected:out[name]={'value':sum(r[key] for r in selected)/len(selected),'numerator':sum(r[key] for r in selected),
                               'denominator':len(selected),'unit':'case','status':'measured'}
    times=sorted(r['elapsed_seconds'] for r in observed if isinstance(r.get('elapsed_seconds'),(int,float)))
    if times:
        for name,value in [('latency_p50_seconds',median(times)),('latency_p95_seconds',times[max(0,math.ceil(.95*len(times))-1)])]:
            out[name]={'value':value,'denominator':len(times),'unit':'case','status':'measured','estimator':'median / nearest-rank p95'}
    # Semantic intent/slots, question necessity and requirement correctness cannot be
    # manufactured from a model's self-evaluation. Consume separately reviewed labels only.
    for name in METRICS:
        samples=[r.get('adjudicated_metrics',{}).get(name) for r in observed]
        samples=[s for s in samples if isinstance(s,dict) and s.get('denominator',0)>0]
        if samples:
            n=sum(s['numerator'] for s in samples);d=sum(s['denominator'] for s in samples)
            out[name]={'value':n/d,'numerator':n,'denominator':d,'unit':'reviewed opportunity','status':'adjudicated'}
    return out
