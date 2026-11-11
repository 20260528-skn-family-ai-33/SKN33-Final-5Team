"""Reuse reviewed labels only when semantic trace data are exactly unchanged.

Ignored fields are transport/timing/identity metadata, not user-facing conditions.
Different evidence always goes back to explicit review, never auto-passes.
"""
import argparse
import json
from pathlib import Path
from qwen_live_support import digest

IGNORED={'id','evidence_ids','evidence','resource_versions','trace','validated_user_turn'}


def canonical(value):
    if isinstance(value,list):return [canonical(v) for v in value]
    if isinstance(value,dict):return {k:canonical(v) for k,v in value.items() if k not in IGNORED}
    return value


def semantic_evidence(row):
    # Workflow IDs/references MUST stay intact: bindings/dependencies are semantic.
    return {'workflow':row.get('workflow'),'route':row.get('actual_route'),'final_route':row.get('final_route'),
            'schema_valid':row.get('schema_valid'),'execution_success':row.get('execution_success'),
            'turns':[{'user':t['user'],'status':t['response']['status'],
                      'questions':canonical(t['response'].get('pending_questions')),
                      'draft_questions':(t['response'].get('draft') or {}).get('questions'),
                      'schedule':(t['response'].get('draft') or {}).get('schedule'),
                      'condition_ledger':canonical(t['response'].get('condition_ledger')),
                      'issues':t['response'].get('issues')} for t in row.get('turns',[])],
            'parsed':[canonical({k:v for k,v in s['parsed_state'].items() if k in ('request_meaning','capability_assessment','condition_ledger','requirement_ledger')}) for s in row.get('stages',[])]}


def main():
    p=argparse.ArgumentParser();p.add_argument('baseline',type=Path);p.add_argument('after',type=Path);p.add_argument('labels',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();before=json.loads(a.baseline.read_text(encoding='utf-8'));after=json.loads(a.after.read_text(encoding='utf-8'));labels=json.loads(a.labels.read_text(encoding='utf-8'))
    old={r['id']:r for r in before['cases']};out={'gold_sha256':labels['gold_sha256'],'cases':{}};review=[]
    for row in after['cases']:
        id=row['id']
        if id in labels['cases'] and digest(semantic_evidence(row))==digest(semantic_evidence(old[id])):
            out['cases'][id]={**labels['cases'][id],'evidence':labels['cases'][id]['evidence']+' After: semantic trace, workflow IDs/bindings, slots/questions exactly match baseline projection.'}
        else:review.append(id)
    out['requires_review']=review
    a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'reused':list(out['cases']),'requires_review':review}))


if __name__=='__main__':main()
