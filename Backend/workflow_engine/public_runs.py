"""Bound public Gmail responses while preserving complete persisted checkpoints."""


def public_run(run):
    documents=[document for output in run.get('outputs',{}).values()
               for document in output.get('documents',[])
               if document.get('parser_engine')=='gmail' or str(document.get('source_id','')).startswith('gmail:')]
    gmail_evidence=[item for item in run.get('evidence',[])
                    if item.get('parser_engine')=='gmail' or str(item.get('source_id','')).startswith('gmail:')]
    if sum(len(item.get('text','')) for item in documents+gmail_evidence)<=45000:
        return run
    result={key:value for key,value in run.items() if key not in ('outputs','evidence','messages')}
    evidence=[]
    for item in run.get('evidence',[])[:500]:
        quote=item.get('quote') or item.get('text','')
        evidence.append({**{key:value for key,value in item.items() if key not in ('text','quote')},'quote':quote[:1000]})
    result['evidence']=evidence
    result['messages']=[message for message in run.get('messages',[]) if message.get('role')!='assistant']
    result['public_projection']={'outputs_omitted':True,'evidence_preview_characters':1000,
                                 'evidence_returned':len(evidence),'evidence_total':len(run.get('evidence',[])),
                                 'evidence_truncated':len(run.get('evidence',[]))>len(evidence),
                                 'quote_truncated':any(len(item.get('quote') or item.get('text',''))>1000 for item in run.get('evidence',[])[:500])}
    return result


def public_result(value):
    if not isinstance(value,dict):return value
    if 'config_snapshot' in value and 'outputs' in value:return public_run(value)
    result=value
    if 'run' in value and isinstance(value['run'],dict):result={**result,'run':public_run(value['run'])}
    if 'tests' in value and isinstance(value['tests'],list):
        tests=[]
        for index,run in enumerate(value['tests']):
            projected=public_run(run)
            if index and projected is not run:
                projected={**projected,'answer':projected.get('answer','')[:4000],'evidence':[],
                           'public_projection':{**projected['public_projection'],'history_preview':True,'evidence_returned':0,
                                                'evidence_truncated':bool(projected['public_projection']['evidence_total'])}}
            tests.append(projected)
        result={**result,'tests':tests}
    return result
