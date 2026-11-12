"""Narrow source checks for destructive simplifications observed in beta feedback.

These checks reject mismatches; they never infer replacement business values or
generate a workflow. The normal requirement ledger and planner still own design.
"""
import re

from agent_builder.capabilities.capability_judgement import active_request_messages


_REVIEW = re.compile(r'(?:사람\s*검토|사람(?:이|의)\s*(?:직접\s*)?(?:검토|확인))(?:\s*\(human\s+review\))?|human\s+review', re.I)
_FINAL = re.compile(r'최종\s*(?:메모|보고서)')
_CANCEL = re.compile(r'\s*(?:단계|노드)?\s*(?:은|는|을|를)?\s*(?:없이|생략|제외|취소|하지\s*마|하지\s*않|빼)')
_QUESTION = re.compile(r'(?:뭐야|뭔가요|무슨\s*뜻|설명해|무엇인가요)')


def review_request(state):
    review = final = False
    for message in active_request_messages(state):
        if message.get('role') != 'user':
            continue
        text = message['content']
        if len(text) < 50 and _QUESTION.search(text) and not re.search(r'추가|연결|만들|포함', text):
            continue
        # Quoted document/cell values are data. Parenthesized node type names
        # remain instructions, as in "사람 검토(Human Review)".
        text = re.sub(r'["“][^"”]*["”]|\x27[^\x27]*\x27', ' ', text)
        for pattern, kind in ((_REVIEW, 'review'), (_FINAL, 'final')):
            for match in pattern.finditer(text):
                wanted = not bool(_CANCEL.match(text[match.end():]))
                if kind == 'review':
                    review = wanted
                else:
                    final = wanted
    return review, final


def review_workflow_issues(state, draft):
    review, final = review_request(state)
    if not review or draft.get('questions') or draft.get('unsupported'):
        return []
    if not draft.get('workflow'):
        return ['요청한 사람 검토가 빠졌습니다. 검토 의견을 기다리는 워크플로우를 연결하세요.']
    from workflow_engine.execution_path import execution_path
    try:
        path = execution_path(draft['workflow'])
    except ValueError:
        return ['사람 검토 단계의 실행 경로를 확인하지 못했습니다.']
    # Conditional review contracts are validated by requirement_coverage.
    # The unresolved execution prefix cannot establish their downstream order.
    if any(n['tool'] == 'condition' for n in path):
        return []
    reviews = []
    for node in path:
        settings = node.get('settings', {})
        previews = [b for b in node.get('bindings', []) if b['input'] in ('text', 'records')]
        if (node['tool'] == 'ask_user' and not settings.get('operation')
                and not settings.get('instructions') and settings.get('question')
                and len(previews) == 1):
            reviews.append(node)
    if not reviews:
        return ['요청한 사람 검토가 빠졌습니다. 분석 결과를 보여주고 실제 검토 의견을 기다리는 단계를 연결하세요.']
    text = '\n'.join(m['content'] for m in active_request_messages(state) if m.get('role') == 'user')
    if re.search(r'(?:한\s*번|1\s*번)만\s*(?:멈|대기|검토|받)', text) and sum(n['tool']=='ask_user' for n in path) != 1:
        return ['한 번만 요청한 사람 검토가 여러 번 연결됐습니다. 검토 의견은 한 번만 받도록 연결하세요.']
    if final:
        positions = {node['id']: i for i, node in enumerate(path)}
        for node in path:
            if node['tool'] != 'compose_answer' or node.get('settings', {}).get('operation'):
                continue
            for reviewed in reviews:
                if positions[reviewed['id']] >= positions[node['id']]:
                    continue
                bindings = node.get('bindings', [])
                answer = any(b['input']=='answer' and b['output']=='answer' and b['node_id']==reviewed['id'] for b in bindings)
                preview = next((b for b in reviewed['bindings'] if b['input']=='text'), None)
                analysis = not preview or any(b['input']=='text' and b['output']=='text'
                    and b['node_id'] in (preview['node_id'], reviewed['id']) for b in bindings)
                if answer and analysis:
                    return []
        return ['최종 메모에 분석 결과와 사람 검토 의견이 연결되지 않았습니다. 분석 결과(text)와 검토 답변(answer)을 이후 답변 작성 단계의 입력으로 연결하세요.']
    return []


def unary_filter_issue(rule, text):
    """A categorical comparison must not silently become an emptiness predicate."""
    operator = rule.get('operator')
    if operator not in ('is_empty', 'not_empty'):
        return None
    if rule.get('value') is not None or rule.get('compare_field'):
        return '빈값 선별에는 비교 값을 넣을 수 없습니다. 값과 비교하려면 비교 조건을 사용하세요.'
    empty = r'빈\s*(?:값|칸|행|셀)|비어\s*있|비었|결측|누락|공백|값(?:이|은)?\s*없|\b(?:null|empty|blank|missing)\b'
    nonempty = r'비어\s*있지\s*않|빈\s*값이\s*아닌|값(?:이)?\s*있는|채워진|입력된|\bnon[- ]?empty\b'
    selection = r'골라|선별|필터|추려|남겨|남기|제외|제거|삭제|(?:행|주문|항목|것)만|\b(?:select|filter|keep|drop|remove)\b'
    field = rule.get('field') or '\0'
    # The predicate must immediately describe this exact column, not another
    # column elsewhere in the sentence (or a suffix such as 상태 in 주문상태).
    target = (r'(?<!\w)["\x27`“]?' + re.escape(field) + r'["\x27`”]?'
              r'(?:\s*(?:열|컬럼|필드))?(?:\s*(?:이|가|은|는|의))?\s*'
              rf'(?=(?:값(?:이|은|는)?\s*)?(?:{nonempty}|{empty}|\b{operator}\b))')
    for clause in re.split(r'[.\n;,。]', text):
        # A missing-value error/keep policy authorizes no additional row selection.
        cleaned = re.sub(r'(?:빈\s*값|결측(?:값)?|누락(?:값)?)\s*(?:은|는|을|를)?\s*(?:오류|유지|에러)', '', clause)
        bound = re.search(target, cleaned, re.I)
        if not bound:
            continue
        cleaned = cleaned[bound.end():]
        if re.match(r'\b' + operator + r'\b', cleaned, re.I):
            return None
        if re.match(rf'(?:값(?:이|은|는)?\s*)?(?:{nonempty})', cleaned, re.I) and re.search(selection, cleaned, re.I):
            if operator == 'not_empty':
                return None
            continue
        if re.match(rf'(?:값(?:이|은|는)?\s*)?(?:{empty})', cleaned, re.I) and re.search(selection, cleaned, re.I):
            removing_empty = bool(re.search(r'(?:제외|제거|삭제|\b(?:drop|remove)\b)', cleaned, re.I))
            if (operator == 'not_empty') == removing_empty:
                return None
    return f"'{rule.get('field', '')}'의 빈값 선별 근거가 없습니다. 원래 요청의 비교 값과 조건을 다시 확인하세요."


def request_guard_issues(state, draft):
    issues = review_workflow_issues(state, draft)
    if draft.get('questions') or draft.get('unsupported'):
        return issues
    text = '\n'.join(m['content'] for m in active_request_messages(state) if m.get('role') == 'user')
    plans = [] if draft.get('workflow') else [draft.get('execution_plan')]
    for node in (draft.get('workflow') or {}).get('nodes', []):
        if node['tool'] == 'filter_sort' and not node.get('settings', {}).get('operation'):
            plans.append(node['settings'].get('execution_plan') or draft.get('execution_plan'))
    for plan in plans:
        for rule in (plan or {}).get('filters', []):
            issue = unary_filter_issue(rule, text)
            if issue:
                issues.append(issue)
    return list(dict.fromkeys(issues))
