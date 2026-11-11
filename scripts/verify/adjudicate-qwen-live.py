"""Aggregate explicit evidence review, separately from untouched live artifacts.

Labels must be written by a reviewer comparing stages/workflows to frozen gold.
No model self-evaluation or route-only semantic pass is accepted.
"""
import argparse
import json
from pathlib import Path
from qwen_live_support import metrics, digest, task_scores

ROOT=Path(__file__).resolve().parents[2]


def same(key,a,b):
    if key.endswith('formula') and isinstance(a,str) and isinstance(b,str):
        return ''.join(a.split())==''.join(b.split())
    return a==b


def score(record,case,label):
    gold=case['gold'];values={}
    def ratio(name,n,d):
        assert 0<=n<=d,(record['id'],name,n,d)
        values[name]={'numerator':n,'denominator':d}
    assert label['evidence'], 'Review needs a trace/workflow evidence explanation'
    ratio('intent_accuracy',int(label['intent_correct']),1)
    observed=label['observed_slots'];target=gold['slots']
    tp=sum(k in observed and same(k,v,observed[k]) for k,v in target.items())
    fp=sum(k in observed and not same(k,v,observed[k]) for k,v in target.items())+len(label.get('extra_unsupported_slots',[]))
    fn=len(target)-tp
    ratio('slot_extraction_f1',2*tp,2*tp+fp+fn)
    questions=[(i,q) for i,t in enumerate(record.get('turns',[])) for q in (t['response'].get('pending_questions') or [{'text':x} for x in (t['response'].get('draft') or {}).get('questions',[])])]
    labels=label['questions']
    assert len(questions)==len(labels),(record['id'],'question label count',len(questions),len(labels))
    asked={s for q in labels for s in q.get('necessary_slots',[])}
    missing=set(gold['missing_required_slots'])
    ratio('necessary_question_recall',len(asked&missing),len(missing))
    ratio('unnecessary_question_rate',sum(q['unnecessary'] for q in labels),len(labels))
    followup=[q for (i,_),q in zip(questions,labels) if i>0]
    ratio('repeated_question_rate',sum(q.get('repeated',False) for q in followup),len(followup))
    changed={k:v for k,v in gold['final_slots'].items() if k not in target or not same(k,v,target[k])}
    if len(record.get('turns',[]))>1:
        actual=label['final_observed_slots']
        ratio('answer_incorporation_accuracy',sum(k in actual and same(k,v,actual[k]) for k,v in changed.items()),len(changed))
    tasks=gold['workflow']['tasks']
    if gold['final_routing']=='READY':
        coverage=label['task_coverage']
        assert len(coverage)==len(tasks),(record['id'],'task label count')
        presence=task_scores({'workflow':record.get('workflow')},gold)
        ratio('tool_operation_accuracy',sum(p['tool_operation_present'] for p in presence),len(tasks))
        ratio('requirement_coverage',sum(coverage),len(tasks))
        record['reviewed_task_scores']=[{**p,'semantic_coverage':c} for p,c in zip(presence,coverage)]
    if case['category']=='multiturn' and case['answer_policy']:
        actual=label.get('final_observed_slots',{})
        done=(record.get('final_route')=='READY' and all(label['task_coverage'])
              and all(k in actual and same(k,v,actual[k]) for k,v in gold['final_slots'].items()))
        ratio('clarification_completion_rate',int(done),1)
    record['adjudicated_metrics']=values
    record['adjudication_evidence']=label['evidence']
    return record


def main():
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);p.add_argument('labels',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();run=json.loads(a.run.read_text(encoding='utf-8'));labels=json.loads(a.labels.read_text(encoding='utf-8'))
    corpus=json.loads((ROOT/'docs/qwen-live-evaluation-cases.json').read_text(encoding='utf-8'))
    assert run['gold_sha256']==corpus['gold_sha256']==labels['gold_sha256']
    cases={c['id']:c for c in corpus['cases']}
    rows=[]
    for r in run['cases']:
        if r['id'] in labels['cases']:r=score(r,cases[r['id']],labels['cases'][r['id']])
        rows.append(r)
    result={'source_run':str(a.run),'source_run_sha256':digest(run),'labels_sha256':digest(labels),
            'gold_sha256':corpus['gold_sha256'],'reviewer':'Codex trace/JSON review; not an independent human gold review',
            'slot_scope':'Frozen gold slot keys plus explicitly reviewed unsupported invented values; missing=FN, wrong=FP+FN. Defaults outside this scope excluded.',
            'reviewed_cases':len(labels['cases']),'live_metrics':metrics(rows),
            'all_backend_metrics_including_code_recipes':metrics([{**r,'live_model_calls':max(1,r.get('live_model_calls',0))} for r in rows if r['status'] in ('completed','failed')]),
            'split_metrics':{s:metrics([r for r in rows if cases[r['id']]['split']==s]) for s in ('development','holdout')},
            'case_reviews':[{k:r[k] for k in ('id','actual_route','final_route','adjudicated_metrics','adjudication_evidence','reviewed_task_scores') if k in r} for r in rows]}
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'reviewed':len(labels['cases']),'metrics':result['live_metrics']},ensure_ascii=False))


if __name__=='__main__':main()
