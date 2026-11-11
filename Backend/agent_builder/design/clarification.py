"""Pause a recoverable business decision without authorizing an unfinished draft."""
from copy import deepcopy
import hashlib
import json

from contracts.models import AgentDraft


def clarification_pause(state, stage, questions, *, detail, preserve_draft=False):
    texts=list(dict.fromkeys(q.strip() for q in questions if q.strip()))[:3]
    if not texts:
        raise ValueError('사용자에게 확인할 질문이 비어 있습니다.')
    source='requirements' if stage=='requirements' else 'reconciliation'
    identity=json.dumps([state.get('session_id'),[m for m in state.get('messages',[]) if m['role']=='user'],
                        state.get('resources',[]),stage,texts],
                        ensure_ascii=False,sort_keys=True)
    pending=[dict(id=hashlib.sha256(f'{identity}:{i}'.encode()).hexdigest()[:24],
                  key=f'{source}.{i}',source=source,text=text,options=[])
             for i,text in enumerate(texts)]
    raw=deepcopy(state.get('draft')) if preserve_draft else None
    if raw:
        raw.update(questions=texts,question_options=[[] for _ in texts])
    else:
        raw=dict(name='업무 조건 확인',purpose='사용자 확인 후 업무 설계',
            instructions='원래 요청과 사용자 답변을 보존하여 업무를 설계합니다.',
            tool_ids=[],document_ids=[r['id'] for r in state.get('resources',[])],requirements=[],
            questions=texts,question_options=[[] for _ in texts],unsupported=[],explanation=detail[:2000],
            execution_plan=dict(filters=[],sort=[],match='all',missing_policy='ask',
                duplicate_policy='keep',duplicate_field=None,output_format='json'))
    try:draft=AgentDraft.model_validate(raw).model_dump()
    except ValueError:
        if not preserve_draft:raise
        return clarification_pause(state,stage,texts,detail=detail,preserve_draft=False)
    return dict(draft=draft,pending_questions=pending,status='needs_clarification',
                confirmed=False,saved_agent_id=None,issues=[],semantic_issues=[],
                messages=state.get('messages',[])+[dict(role='assistant',content=detail+'\n'+'\n'.join(texts))],
                trace=state.get('trace',[])+[dict(node='coordination',label='판정 조정 및 추가 확인',
                    status='needs_input',detail=detail,stage=stage,model_calls=0)])
