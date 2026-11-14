"""Narrow source checks for destructive simplifications observed in beta feedback.

These checks reject mismatches; they never infer replacement business values or
generate a workflow. The normal requirement ledger and planner still own design.
"""
import re

from agent_builder.capabilities.capability_judgement import active_request_messages


_REVIEW = re.compile(r'(?:사람\s*검토|사람(?:이|의)\s*(?:직접\s*)?(?:검토|확인))(?:\s*\(human\s+review\))?|human\s+review', re.I)
_FINAL = re.compile(r'최종\s*(?:메모|보고서)')
_CANCEL = re.compile(r'\s*(?:단계|노드)?\s*(?:은|는|을|를|가|이)?\s*(?:없이|생략|제외|취소|하지\s*마|하지\s*않|빼|필요\s*(?:없|하지\s*않))')
_REVIEWED = re.compile(r'\s*(?:한|된|완료된|했던)\s+(?:주문|행|계약|문서|자료|항목|결과)')
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
                if kind == 'review' and _REVIEWED.match(text[match.end():]):
                    continue
                wanted = not bool(_CANCEL.match(text[match.end():]))
                if kind == 'review':
                    review = wanted
                else:
                    final = wanted
    return review, final


def _missing_review_scope(gate, nodes, clause):
    """Prove the records inspected by has_missing refer to the requested item."""
    source=gate
    seen=set()
    while source['id'] not in seen:
        seen.add(source['id'])
        if source['tool']=='extract_information' and not source['settings'].get('operation'):
            fields=source['settings'].get('fields',[])
            break
        if source['tool'] not in ('condition','ask_user') or source['settings'].get('operation'):
            return False
        bindings=[b for b in source.get('bindings',[]) if b['input']=='records' and b['output']=='records']
        if len(bindings)!=1 or bindings[0]['node_id'] not in nodes:return False
        source=nodes[bindings[0]['node_id']]
    else:return False
    # Parse the entire scope, not merely any field name occurring inside it.
    # "조항 또는 담당자" means either field; "모두"/AND must not become any.
    scope = re.split(r'(?:있는|없는|있을|없을)\s*(?:경우|때)', clause, maxsplit=1)[0].strip()
    prefix = r'(?:누락(?:된)?|빈\s*값(?:인)?|결측(?:된)?)\s*'
    field=gate['settings'].get('condition_field')
    names = '|'.join(re.escape(f) for f in sorted(set(fields), key=len, reverse=True))
    if not names:
        return False
    matched = re.fullmatch(prefix + r'(?P<names>(?:' + names + r')(?:\s*(?:또는|혹은)\s*(?:' + names + r'))*)(?:이|가)?', scope)
    if matched:
        named = set(re.split(r'\s*(?:또는|혹은)\s*', matched['names']))
        if len(named)==1:
            return field in named or (not field and set(fields)==named)
        return not field and named == set(fields)
    # A real extracted field named "항목" is specific; only otherwise is the
    # generic phrase evidence of an all-field request.
    if re.fullmatch(prefix + r'(?:(?:전체|모든)\s*)?(?:항목|필드)(?:이|가)?', scope):
        return bool(fields) and not field
    return False


def review_workflow_issues(state, draft):
    review, final = review_request(state)
    if not review or draft.get('questions') or draft.get('unsupported'):
        return []
    if not draft.get('workflow'):
        return ['요청한 사람 검토가 빠졌습니다. 검토 의견을 기다리는 워크플로우를 연결하세요.']
    workflow = draft['workflow']
    by = {n['id']: n for n in workflow['nodes']}
    from .workflow_paths import workflow_paths
    try:
        paths = workflow_paths(workflow)
    except (ValueError, KeyError):
        return ['사람 검토 단계의 실행 경로를 확인하지 못했습니다.']
    text = '\n'.join(m['content'] for m in active_request_messages(state) if m.get('role') == 'user')
    conditional = bool(re.search(r'(?:경우(?:에만|만)?|때(?:만)?|이면|하면)\s*(?:사람\s*검토|human\s+review)', text, re.I))
    if conditional:
        # A review on an arbitrary branch is not evidence of the requested gate.
        # Only prove the supported missing-value condition; unknown predicates
        # must be clarified instead of accepting an inverted branch.
        match = re.search(r'(?:누락|빈\s*값|결측)[^.\n;]{0,50}?'
                          r'(?P<polarity>있는|없는|있을|없을)\s*(?:경우|때)(?:에만|만)?\s*'
                          r'(?:사람\s*검토|human\s+review)', text, re.I)
        gates = [n for n in by.values() if n['tool']=='condition' and not n['settings'].get('operation')
                 and n['settings'].get('condition_operator')=='has_missing'
                 and match and _missing_review_scope(n,by,match[0])]
        if not match or len(gates)!=1:
            return ['사람 검토가 필요한 조건과 분기 방향을 확인하지 못했습니다.']
        gate = gates[0]['id']
        wanted = 'no' if match['polarity'].startswith('없') else 'yes'
        only = bool(re.search(r'(?:경우|때)(?:에만|만)', match[0]))
        issues = []
        for path, edges in paths:
            decision = next((e['branch'] for e in edges if e['source']==gate), None)
            if decision is None:
                issues.append(['사람 검토 조건을 거치지 않는 실행 경로가 있습니다.'])
                continue
            start = next(i for i,n in enumerate(path) if n['id']==gate) + 1
            if any(n['tool']=='ask_user' for n in path[:start]):
                issues.append(['조건 판정 전에 사람 검토가 연결됐습니다.'])
            if decision == wanted:
                issues.append(_review_path_issues(path[start:], final, text))
            elif any(n['tool']=='ask_user' for n in path[start:]):
                issues.append(['사람 검토를 요청하지 않은 조건 분기에 검토가 연결됐습니다.'] if only
                              else _review_path_issues(path[start:], final, text))
        return list(dict.fromkeys(i for group in issues for i in group))
    issues = [_review_path_issues(path, final, text) for path, _ in paths]
    return list(dict.fromkeys(i for group in issues for i in group))


def _review_path_issues(path, final, text):
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
    if re.search(r'(?:한\s*번|1\s*번)만\s*(?:멈|대기|검토|받)|정확히\s*(?:한\s*번|1\s*번)', text) and sum(n['tool']=='ask_user' for n in path) != 1:
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
                preview = next(b for b in reviewed['bindings'] if b['input'] in ('text','records'))
                analysis = any(b['input']==preview['input'] and
                    (b['node_id'], b['output']) in ((preview['node_id'],preview['output']), (reviewed['id'],preview['input']))
                    for b in bindings)
                if answer and analysis:
                    return []
        return ['최종 메모에 분석 결과와 사람 검토 의견이 연결되지 않았습니다. 검토한 분석 결과(text 또는 records)와 검토 답변(answer)을 이후 답변 작성 단계의 입력으로 연결하세요.']
    return []


def unary_filter_issue(rule, text, *, state=None):
    """A categorical comparison must not silently become an emptiness predicate."""
    operator = rule.get('operator')
    if operator not in ('is_empty', 'not_empty'):
        return None
    if rule.get('value') is not None or rule.get('compare_field'):
        return '빈값 선별에는 비교 값을 넣을 수 없습니다. 값과 비교하려면 비교 조건을 사용하세요.'
    if state is not None:
        from .compound_table_request import grounded_compound_rule
        if grounded_compound_rule(state, rule):
            return None
    empty = r'빈\s*(?:값|칸|행|셀)|비어\s*있|비었|결측|누락|공백|값(?:이|은)?\s*없|\b(?:null|empty|blank|missing)\b'
    nonempty = r'비어\s*있지\s*않|빈\s*값이\s*아닌|값(?:이)?\s*있는|채워진|입력된|\bnon[- ]?empty\b'
    selection = r'골라|선별|필터|추려|남겨|남기|제외|제거|삭제|\b(?:select|filter|keep|drop|remove)\b'
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
        # The first selection verb belongs to this predicate. A later column's
        # removal verb must not invert an earlier keep operation (and vice versa).
        action = re.search(selection, cleaned, re.I)
        if action:
            negated = re.match(r'\s*(?:하)?지\s*(?:말고|않고|마(?:세요)?|말아(?:줘)?|않)', cleaned[action.end():])
            if negated:
                remainder = cleaned[action.end()+negated.end():].strip()
                # A bare prohibition does not request a new row filter. For a
                # replacement predicate keep the same target column explicitly.
                if re.match(rf'(?:값(?:이|은|는)?\s*)?(?:{nonempty}|{empty})', remainder, re.I):
                    return unary_filter_issue(rule, field+' '+remainder)
                if re.match(selection, remainder, re.I):
                    return unary_filter_issue(rule, field+' '+cleaned[:action.start()]+remainder)
                continue
        removing = bool(action and re.fullmatch(r'제외|제거|삭제|drop|remove', action[0], re.I))
        selected = action or re.search(r'(?:행|주문|항목|것)만', cleaned)
        if re.match(r'\b' + operator + r'\b', cleaned, re.I):
            return None
        if re.match(rf'(?:값(?:이|은|는)?\s*)?(?:{nonempty})', cleaned, re.I) and selected:
            if (operator == 'is_empty') == removing:
                return None
            continue
        if re.match(rf'(?:값(?:이|은|는)?\s*)?(?:{empty})', cleaned, re.I) and selected:
            if (operator == 'not_empty') == removing:
                return None
    return f"'{rule.get('field', '')}'의 빈값 선별 근거가 없습니다. 원래 요청의 비교 값과 조건을 다시 확인하세요."


def request_guard_issues(state, draft):
    issues = review_workflow_issues(state, draft)
    if draft.get('questions') or draft.get('unsupported'):
        return issues
    from .compound_table_request import compound_draft_issues
    issues.extend(compound_draft_issues(state, draft))
    from .output_selection_request import output_selection_issues
    issues.extend(output_selection_issues(state, draft))
    from .numeric_filter_proof import numeric_filter_issues
    issues.extend(numeric_filter_issues(state, draft))
    text = '\n'.join(m['content'] for m in active_request_messages(state) if m.get('role') == 'user')
    plans = [] if draft.get('workflow') else [draft.get('execution_plan')]
    for node in (draft.get('workflow') or {}).get('nodes', []):
        if node['tool'] == 'filter_sort' and not node.get('settings', {}).get('operation'):
            plans.append(node['settings'].get('execution_plan') or draft.get('execution_plan'))
    for plan in plans:
        for rule in (plan or {}).get('filters', []):
            issue = unary_filter_issue(rule, text, state=state)
            if issue:
                issues.append(issue)
    return list(dict.fromkeys(issues))
