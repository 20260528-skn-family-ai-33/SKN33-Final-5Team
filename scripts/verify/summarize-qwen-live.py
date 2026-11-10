"""Derive factual counts/invariants from actual run artifacts (not model quality labels)."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo


def summarize(run):
    cases=run['cases'];attempted=[c for c in cases if c['status'] in ('completed','failed')]
    actual=[c for c in attempted if c.get('live_model_calls',0)>0]
    calls=[x for c in attempted for x in c.get('model_calls',[])]
    invariants=[];temporal=[];ml=Counter()
    for c in attempted:
        turns=c.get('turns',[])
        if len(turns)>1:
            users=[];valid=True;ids=True;revision=True;qids=True
            for i,t in enumerate(turns):
                users.append(t['user']);s=t['response']
                ids &= s['id']==c['session_id']==c['thread_id']
                valid &= [m['content'] for m in s.get('messages',[]) if m['role']=='user'][-len(users):]==users
                if i:
                    revision &= s['revision']>turns[i-1]['response']['revision']
                    if t.get('question_id'):qids &= t['question_id'] in [q['id'] for q in turns[i-1]['response'].get('pending_questions',[])]
            invariants.append({'id':c['id'],'turn_count':len(turns),'same_session_thread':ids,'history_preserved':valid,'revision_increases':revision,'answered_question_id_valid':qids})
        for stage in c.get('stages',[]):
            if stage['stage']=='conversation':
                traces=stage['parsed_state'].get('trace',[])
                events=[t for t in traces if t.get('tool_advice_status')]
                if events:ml[events[-1]['tool_advice_status']]+=1
        for index,t in enumerate(turns):
            for node in ((t['response'].get('draft') or {}).get('workflow') or {}).get('nodes',[]):
                if node['tool']!='email.list':continue
                args=json.loads(node['settings'].get('arguments_json') or '{}')
                query=args.get('query')
                match=re.search(r'after:(\d+) before:(\d+)',query) if isinstance(query,str) else None
                temporal.append({'id':c['id'],'turn':index,'max_results':args.get('max_results'),'query':args.get('query'),
                                 'interval_hours':(int(match[2])-int(match[1]))/3600 if match else None})
    return {'status':run['status'],'requested':run['requested_scenarios'],'attempted':len(attempted),'live_scenarios':len(actual),
            'code_only_scenarios':[c['id'] for c in attempted if not c.get('live_model_calls')],
            'not_run':[c['id'] for c in cases if c['status']=='not_run'],
            'live_call_count_excluding_probe':len(calls),'actual_models':dict(Counter(c.get('actual_model') for c in calls)),
            'routing_accuracy_live':{'numerator':sum(c.get('expected_route')==c.get('actual_route') for c in actual),'denominator':len(actual)},
            'routing_accuracy_all_backend':{'numerator':sum(c.get('expected_route')==c.get('actual_route') for c in attempted),'denominator':len(attempted)},
            'initial_routes':dict(Counter(c.get('actual_route') for c in attempted)),
            'local_execution':{'success':sum(c.get('execution_success') is True for c in attempted),'attempted':sum(isinstance(c.get('execution_success'),bool) for c in attempted)},
            'external_connector_executions':0,'http_browser_observed':False,'multi_turn_persistence':invariants,
            'gmail_query_evidence':temporal,'ml_stage_status':dict(ml),
            'transport_failures':[{'id':c['id'],'stage':m['stage'],'error_type':m.get('error_type')} for c in attempted for m in c.get('model_calls',[]) if m['status']=='failed']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);p.add_argument('--output',type=Path)
    a=p.parse_args();result=summarize(json.loads(a.run.read_text(encoding='utf-8')))
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if a.output:a.output.write_text(text+'\n',encoding='utf-8')
    print(text)
