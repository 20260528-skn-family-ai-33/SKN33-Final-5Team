"""Generated drafts must contain a plan, an actual question, or an unsupported reason.

These are structural alternatives, not a task verdict. No plan, question or
answer is supplied here. Legacy editable drafts keep their permissive model.
"""
from copy import deepcopy

PROMPT = '''
최종 초안은 다음 중 하나로 반환합니다.
1. 설계할 수 있으면 실제 workflow 객체를 작성하고 questions와 unsupported는 빈 목록으로 둡니다.
2. 필수 조건이 미정이면 questions에 비어 있지 않은 확인 질문을 작성하고, 확인된 노드와 연결은 workflow에 미완료 초안으로 보존합니다. 구성할 노드가 아직 없을 때만 workflow=null로 대기합니다. 질문이 남은 workflow는 미리보기이며 완료·승인된 설계가 아닙니다.
3. 등록 도구로 수행할 수 없으면 workflow=null로 두고 unsupported에 비어 있지 않은 실제 이유를 작성합니다.
workflow=null이면서 questions와 unsupported가 모두 빈 응답은 미완성입니다.
도구 이름이나 requirements·explanation에 업무를 나열하는 것은 workflow를 대신하지 않습니다.
이미 제공된 조건을 다시 묻거나, 생성을 끝내기 위해 미지원 사유를 만들어내지 마세요.
'''


class DraftOutcomeError(ValueError):
    code = 'invalid_draft_outcome'


def validate_generated_outcome(raw):
    """Reject an invalid outcome without modifying the model's response."""
    instructions=raw.get('instructions')
    if not isinstance(instructions,str) or not instructions.strip():
        raise DraftOutcomeError('최상위 instructions에 사용자 업무 지시사항이 필요합니다.')
    workflow=raw.get('workflow')
    questions=raw.get('questions')
    unsupported=raw.get('unsupported')
    for field,values in [('questions',questions),('unsupported',unsupported)]:
        if not isinstance(values,list) or any(not isinstance(v,str) or not v.strip() for v in values):
            raise DraftOutcomeError(field+'에는 공백이 아닌 질문 또는 사유만 작성해야 합니다.')
    if isinstance(workflow,dict) and workflow and not unsupported:
        return
    if workflow is None and bool(questions) != bool(unsupported):
        return
    raise DraftOutcomeError('실제 workflow 또는 확인 질문이나 미지원 사유가 필요합니다. '
                            '질문에는 미완료 workflow를 보존할 수 있지만, 미지원 사유와 질문·workflow를 함께 반환할 수 없습니다. '
                            'workflow=null과 빈 questions·unsupported를 함께 반환할 수 없습니다.')


def _nonnull(schema):
    result=deepcopy(schema)
    if result.get('type')=='null':return None
    if 'anyOf' in result:
        alternatives=[v for item in result['anyOf'] if (v:=_nonnull(item)) is not None]
        if not alternatives:return None
        result['anyOf']=alternatives
    if isinstance(result.get('type'),list):
        result['type']=[kind for kind in result['type'] if kind!='null']
        if not result['type']:return None
    result.pop('default',None)
    return result


def outcome_schema(schema):
    """Use complete object alternatives, not decoder-optional if/then rules.

    Run after graph/ordered-step/scoped schema transformations. Each branch
    keeps every original field constraint; required names, enums, node limits
    and references are not inferred or filled. Impossible pending branches are
    omitted when the caller already forbids questions/unsupported outcomes.
    """
    base=deepcopy(schema)
    props=base.get('properties',{})
    from .draft_instructions import DESCRIPTION
    # Constrain newly generated output only. Stored/editable legacy drafts keep
    # AgentDraft's existing contract and are never rewritten on load.
    props['instructions']['minLength']=max(1,props['instructions'].get('minLength',0))
    props['instructions']['description']=DESCRIPTION
    required={'workflow','questions','unsupported'}
    if not required.issubset(props):
        raise ValueError('AgentDraft outcome schema requires workflow/questions/unsupported properties')
    definitions=base.pop('$defs',None)
    base['required']=list(dict.fromkeys([*base.get('required',[]),*sorted(required)]))
    branches=[]

    def allow_empty(spec):
        if spec.get('minItems',0)>0:return False
        spec['maxItems']=0
        return True

    for mode in ('workflow','questions','unsupported'):
        branch=deepcopy(base);p=branch['properties']
        if mode=='workflow':
            nonnull=_nonnull(p['workflow'])
            if nonnull is None or not all(allow_empty(p[f]) for f in ('questions','unsupported')):continue
            p['workflow']=nonnull
        else:
            # A nonnullable caller (e.g. READY table scope) must remain so.
            wf=p['workflow']
            nullable=(wf.get('type')=='null' or
                isinstance(wf.get('type'),list) and 'null' in wf['type'] or
                any(item.get('type')=='null' for item in wf.get('anyOf',[])))
            other='unsupported' if mode=='questions' else 'questions'
            if not nullable or p[mode].get('maxItems',1)<1 or not allow_empty(p[other]):continue
            # Clarification may retain the caller's graph/ordered preview. Its
            # original shape and bounds still apply; unsupported has no graph.
            if mode=='unsupported':p['workflow']={'type':'null'}
            p[mode]['minItems']=max(1,p[mode].get('minItems',0))
            # AgentDraft declares homogeneous string arrays. Min length is a
            # decoder aid; the runtime also rejects Unicode whitespace strings.
            p[mode]['items']['minLength']=max(1,p[mode]['items'].get('minLength',0))
        branches.append(branch)
    if not branches:raise ValueError('AgentDraft constraints permit no outcome')
    # LangChain's model binding requires a named object with root properties.
    # A bare anyOf works for a local JSON validator but fails before HTTP.
    # Keep the common contract at the root and narrow outcomes in the branches.
    result=branches[0] if len(branches)==1 else {**deepcopy(base),'anyOf':branches}
    if definitions is not None:result['$defs']=definitions
    return result
