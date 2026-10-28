"""Capture an unverified capability claim; never generate, register or execute code."""
import hashlib
import json
import re
from copy import deepcopy
from .registry import TOOLS

PROTOCOL = 'a4b.tool-gap.v1'


def review_gap_conditions(state):
    """Preserve evidence and conservative review candidates, never infer READY.

    The condition extractor is a single-operation contract and cannot claim
    completeness for an unsupported external action. Candidates are therefore
    explicitly labelled for recheck, even when a phrase appears specified.
    """
    requests=[{'turn':i,'content':m['content']} for i,m in enumerate(state.get('messages',[])) if m.get('role')=='user']
    text='\n'.join(item['content'] for item in requests)
    candidates=[]
    if re.search(r'큰|높은|많은|낮은|작은',text) and re.search(r'비용|금액|매출|가격',text):
        candidates.append({'key':'filter.threshold','description':'선별 기준 금액과 비교 방식 확인','status':'review_required'})
    if re.search(r'매주|매일|예약',text):
        candidates.append({'key':'schedule.timing','description':'예약 요일·시간·시간대 확인','status':'review_required'})
    if re.search(r'이메일|메일|email',text,re.I):
        candidates.append({'key':'delivery.recipient','description':'수신 주소와 전달 조건 확인','status':'review_required'})
    prior=deepcopy(state.get('condition_ledger'))
    return {'protocol':'a4b.gap-condition-review.v1','status':'recheck_required',
            'candidate_conditions':candidates,'previous_condition_ledger':prior,
            'source_user_messages':requests,'complete':False,
            'requires_condition_recheck':True,'allows_generation':False,'allows_execution':False}

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def describe_gap(state, session_id=None):
    if state.get('status') != 'unsupported':
        return None
    from .condition_readiness import input_issues
    if input_issues(state):
        return None
    ledger = state.get('condition_ledger') or {}
    # In enforce mode a model/parse failure must never be relabelled as a missing tool.
    if state.get('condition_enforced') and ledger.get('decision') != 'unsupported':
        return None
    reasons = ledger.get('unsupported_actions', []) if state.get('condition_enforced') else (state.get('draft') or {}).get('unsupported', [])
    if not reasons:
        return None
    catalog = [{'id': t['id'], 'version': t['version'], 'description': t['description'],
                'input_ports': t['input_ports'], 'output_ports': t['output_ports']} for t in TOOLS]
    messages = [{'turn': i, 'content': m['content']} for i, m in enumerate(state.get('messages', [])) if m.get('role') == 'user']
    resources = [{k: r[k] for k in ('id', 'name', 'kind', 'version', 'hash', 'columns', 'parser_status', 'processing_status') if k in r}
                 for r in state.get('resources', [])]
    basis = {'session_id': session_id or state['session_id'], 'user_messages': messages,
             'reported_missing_actions': reasons, 'resources': resources,
             'catalog_sha256': digest(catalog), 'condition_ledger_sha256': digest(ledger)}
    review=state.get('gap_condition_review')
    if review is not None:
        basis['condition_review']=deepcopy(review)
    fingerprint = digest(basis)
    return {'protocol': PROTOCOL, 'gap_id': 'gap_' + fingerprint[:24], 'state_fingerprint': fingerprint,
            **basis, 'catalog': catalog, 'capability_status': 'unverified', 'status': 'needs_spec_review',
            'required_review': ['등록 도구 또는 조합으로 이미 가능한지 확인', '입출력 열·자료형·경계·오류 처리 확정',
                                '순수 JSON 변환과 외부 권한 동작 구분', '독립된 공개·숨긴 정답 사례 준비'],
            'lab_scope': 'pure_json_transform', 'automatic_generation': False,
            'automatic_registration': False, 'workflow_execution_allowed': False}

def tool_gap_node(state):
    # Preserve review candidates separately; never replace the authoritative
    # capability decision or grant generation/approval from this summary.
    review=review_gap_conditions(state) if (state.get('capability_assessment') or {}).get('decision')=='missing_capability' else None
    gap = describe_gap({**state,'gap_condition_review':review})
    return {'tool_gap': gap, 'gap_condition_review':review, 'trace': state.get('trace', []) + [{
        'node': 'tool_gap', 'label': '부족 기능 검토', 'status': 'needs_input',
        'detail': ('미지원 판단을 기록했습니다. 등록 도구 대조와 명세 검토 후 임시 도구 시험이 필요합니다.'
                   if gap else '입력 상태와 미지원 판단을 다시 확인하세요. 도구 생성은 시작하지 않았습니다.'),
        'gap_id': gap['gap_id'] if gap else None}]}

def export_gap(state, session):
    gap = describe_gap(state, session['id'])
    if gap is None:
        raise ValueError('현재 대화에 검토할 미지원 도구 요청이 없습니다.')
    from .tool_gap_choice import creation_intent
    return {**gap, 'revision': session['revision'], 'creation_intent': creation_intent(state, session)}
