"""Replay explicit text predicates by column, separating selection from output edits.

Only complete assertions mutate the contract. Negated/uncertain assertions stay
pending, while an explicit cancellation clears that column. A later mention of a
column in an output/sort instruction cannot invalidate an unchanged predicate.
"""
import re
from agent_builder.conditions.filter_edits import ALL_FILTERS_CANCELLED, addition_intent, in_quoted_text

_NEGATIVE_ACTION = re.compile(
    r'\s*+(?:남기|남겨|고르|골라|선별하|선별해|포함하|포함해|제외하|제외해|삭제하|삭제해)'
    r'(?:주)?\s*+(?:지|진)?\s*+(?:말|마|않|안)')
_POSITIVE_ACTION = re.compile(r'\s*+(?:남겨|골라|선별해|포함해)(?:\s*+(?:줘|주세요))?(?![가-힣])')
_EXCLUDE_ACTION = re.compile(r'\s*+(?:제외해|삭제해|빼줘)(?:\s*+(?:줘|주세요))?(?![가-힣])')


def text_filter_state(state):
    tables = [resource for resource in state.get('resources', []) if resource.get('kind') == 'table']
    if len(tables) != 1:
        return {'predicates': [], 'issues': [], 'cleared_fields': set(), 'replaced_numeric_fields': set()}
    fields = {field for field in tables[0].get('columns', []) if isinstance(field, str) and field}
    if not fields:
        return {'predicates': [], 'issues': [], 'cleared_fields': set(), 'replaced_numeric_fields': set()}
    names = '|'.join(re.escape(field) for field in sorted(fields, key=len, reverse=True))
    assertion = re.compile(
        r'(?<![\w])(?P<field>' + names + r')\s*+(?:열[의은는이가]?\s*+)?(?:이|가|은|는)\s*+'
        r'''(?:'(?P<single>[^'\n]+)'|"(?P<double>[^"\n]+)"|“(?P<curly>[^”\n]+)”|‘(?P<curly_single>[^’\n]+)’|(?P<value>[^\s,'"]+?))인\s+행(?P<only>만|은|을|도)?''')
    active, pending, cleared_fields = {}, {}, set()
    numeric_fields, replaced_numeric_fields = set(), set()
    users = [(index, message) for index, message in enumerate(state.get('messages', [])) if message.get('role') == 'user']
    from agent_builder.conditions.condition_readiness import _COMPARATOR_SPAN, _comparison_subject, _uncertain_comparison, _excludes_named_range, _POLICY_SUBJECTS, _schedule_evidence
    from agent_builder.conditions.filter_edits import filter_clear_events, user_filter_text, filter_clauses
    for source, (turn, message) in enumerate(users):
        text = user_filter_text(state, turn, message)
        if text is None:
            continue
        event = next((entry for entry in state.get('condition_answers', []) if entry.get('turn') == turn), {})
        if len(pending) == 1 and 'filter.match' in event.get('candidate_keys', []):
            field = next(iter(pending))
            if not re.search(r'(?<![\w])' + re.escape(field), text):
                short = re.fullmatch(r"(.+?)만\s*+(?:남겨|골라)(?:\s*+(?:줘|주세요))?[.!?\s]*", text)
                if short:
                    text = field + '가 ' + short[1] + '인 행만 남겨줘'
                elif re.fullmatch(r'(?:조건|필터)\s*+(?:취소|제거|해제)(?:해줘)?[.!?\s]*', text):
                    text = field + ' 조건 취소'
                elif text == '기존 조건 모두 유지' and field in active:
                    pending.pop(field)
                    continue
        if ALL_FILTERS_CANCELLED.fullmatch(text):
            active.clear()
            pending.clear()
            cleared_fields.update(fields)
            numeric_fields.clear()
            replaced_numeric_fields.clear()
            continue
        events = [(start, 'clear', field, None) for start, _, field in filter_clear_events(text, fields)]
        events += [(start, 'pending', field, f'{field} 조건을 취소할지 확인해주세요. 유지하거나 취소할 조건을 알려주세요.')
                   for start, _, field in filter_clear_events(text, fields, uncertain=True)]
        spans = list(_COMPARATOR_SPAN.finditer(text))
        for index, span in enumerate(spans):
            if in_quoted_text(text, span.start()) or _uncertain_comparison(text[span.end():]) or _excludes_named_range(text, span.end()):
                continue
            prefix = text[spans[index - 1].end() if index else 0:span.start()]
            events.extend((span.start(), 'numeric', field, None) for field in fields if _comparison_subject(field, prefix))
        for offset, clause in filter_clauses(text):
            matches = list(assertion.finditer(clause))
            for index, match in enumerate(matches):
                if in_quoted_text(text, offset + match.start()):
                    continue
                field = match['field']
                tail = clause[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(clause)]
                prefix = clause[:match.start()]
                if _NEGATIVE_ACTION.match(tail):
                    events.append((offset + match.start(), 'negative', field, None))
                    continue
                if re.search(r'또는|혹은|이거나|이든|아니면', prefix) and not re.search(r'(?:이고|그리고|또한)\s*+$', prefix):
                    events.append((offset + match.start(), 'pending', field, f'{field} 조건과 앞 조건을 어떻게 결합할지 확인해주세요.'))
                    continue
                excluded = bool(_EXCLUDE_ACTION.match(tail))
                bare = match['only'] == '만' and bool(re.match(r'\s*+(?:$|(?:CSV|JSON|파일|결과)(?:으로|로|\s))', tail, re.I))
                positive = bool(_POSITIVE_ACTION.match(tail)) or bare
                required = bool(re.match(r'\s*+이어야\s*+(?:해|합니다)', tail))
                if not (excluded or positive or required):
                    events.append((offset + match.start(), 'pending', field, f'{field} 조건을 적용할지 확인해주세요.'))
                    continue
                rule = dict(field=field, operator='ne' if excluded else 'eq', value=next(value for value in (match['single'], match['double'], match['curly'], match['curly_single'], match['value']) if value is not None),
                            compare_field=None, value_type='text')
                if 'number' in (tables[0].get('profile', {}).get(field, {}) or {}).get('types', []):
                    from agent_builder.conditions.condition_readiness import _amounts
                    from agent_builder.conditions.input_resolution import _PLAIN_AMOUNT, executable_number
                    amounts = _amounts(rule['value'], field) if _PLAIN_AMOUNT.fullmatch(rule['value'].strip()) else set()
                    if len(amounts) != 1 or not executable_number(next(iter(amounts))):
                        events.append((offset + match.start(), 'pending', field, f'{field}의 숫자 기준값을 확인해주세요.'))
                        continue
                    rule.update(value=float(next(iter(amounts))), value_type='number')
                events.append((offset + match.start(), 'predicate', field, rule))
            for field in sorted(fields):
                subject = re.search(r'(?<![\w])' + re.escape(field) + r'\s*+(?:선별\s*+)?(?:조건|필터|기준)', clause)
                if not subject or in_quoted_text(text, offset + subject.start()):
                    continue
                if not re.search(r'바꿔|바꾸|변경|수정|정할|미정|나중', clause[subject.end():]):
                    continue
                other_scope = (any(re.search(pattern, clause) for pattern in _POLICY_SUBJECTS.values())
                               or bool(_schedule_evidence(clause, follow_up=True))) and not re.search(r'필터|선별', clause)
                if not other_scope:
                    events.append((offset + subject.start(), 'pending', field, f'{field}의 새 선별 조건을 알려주세요.'))
        for _, kind, field, value in sorted(events, key=lambda event: event[0]):
            if kind == 'clear':
                active.pop(field, None)
                pending.pop(field, None)
                cleared_fields.add(field)
                numeric_fields.discard(field)
                replaced_numeric_fields.discard(field)
            elif kind == 'numeric':
                active.pop(field, None)
                pending.pop(field, None)
                cleared_fields.discard(field)
                numeric_fields.add(field)
                replaced_numeric_fields.discard(field)
            elif kind == 'negative':
                if field not in cleared_fields:
                    pending[field] = f'{field} 조건을 적용할지 확인해주세요. 적용할 조건을 적거나 {field} 조건 취소라고 알려주세요.'
            elif kind == 'pending':
                pending[field] = value
            elif kind == 'predicate':
                intent = addition_intent(text)
                if intent == 'denied':
                    continue
                if intent == 'uncertain' or (intent == 'add' and (field in numeric_fields or field in active and active[field][1] != value)):
                    pending[field] = f'{field}의 기존 값과 새 값을 함께 적용할지, 교체할지 확인해주세요.'
                    continue
                active[field] = (source, value)
                if field in numeric_fields:
                    replaced_numeric_fields.add(field)
                    numeric_fields.discard(field)
                pending.pop(field, None)
                cleared_fields.discard(field)
    return {'predicates': list(active.values()), 'issues': list(pending.values()), 'cleared_fields': cleared_fields,
            'replaced_numeric_fields': replaced_numeric_fields}


def text_filter_issues(state, draft):
    contract = text_filter_state(state)
    issues = list(contract['issues'])
    nodes = (draft.get('workflow') or {}).get('nodes', [])
    plans = [node['settings'].get('execution_plan') or draft.get('execution_plan') or {}
             for node in nodes if node['tool'] == 'filter_sort' and not node['settings'].get('operation')]
    if not nodes:
        plans = [draft.get('execution_plan') or {}]
    if any(rule.get('field') in contract['cleared_fields'] for plan in plans for rule in plan.get('filters', [])):
        issues.append('사용자가 취소한 열의 선별 조건이 실행 설정에 남아 있습니다.')
    from .requirement_coverage import _arguments
    for node in nodes:
        if node['tool'] == 'filter_sort' and node['settings'].get('operation') == 'filter':
            try:
                predicates = _arguments(node).get('predicates', [])
                if any(rule.get('column') in contract['cleared_fields'] for rule in predicates):
                    issues.append('사용자가 취소한 열의 선별 조건이 확장 실행 설정에 남아 있습니다.')
            except (ValueError, TypeError, AttributeError):
                issues.append('확장 선별 조건의 실행 인자를 확인해주세요.')
    return issues
