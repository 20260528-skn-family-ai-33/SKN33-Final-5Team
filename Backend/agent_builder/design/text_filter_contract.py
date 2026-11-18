"""Replay explicit text predicates by column, separating selection from output edits.

Only complete assertions mutate the contract. Negated/uncertain assertions stay
pending, while an explicit cancellation clears that column. A later mention of a
column in an output/sort instruction cannot invalidate an unchanged predicate.
"""
import re
from agent_builder.conditions.filter_edits import addition_intent, in_quoted_text, selection_action_uncertain, denied_filter_change
from agent_builder.conditions.filter_dialogue import KEEP_ALL, PendingFilterDecision
from .text_filter_parser import text_atoms, disjunctive_clause, uncovered_selection_fields

_POSITIVE_ACTION = re.compile(
    r'\s*+(?:(?:남겨|골라|선별해|포함해)(?:\s*+(?:줘|주세요))?|(?:남긴|고른|선별한)\s+(?:뒤|후|다음)'
    r'|남기고(?=\s+(?:(?:CSV|JSON)(?:으로|로)|파일로)\s+저장)'
    r'|(?:남기고|골라서)(?=\s+[^.!?;。！？\n]*?(?:(?:으로|로)\s+정렬(?:해|하)'
    r'|(?:오름차순|내림차순)\s*(?:으로|로)?\s+정렬(?:해(?:\s*(?:줘|주세요))?|하세요)?\s*[.!;\n]?\s*$)))(?![가-힣])', re.I)
_EXCLUDE_ACTION = re.compile(r'\s*+(?:제외해|삭제해|빼줘)(?:\s*+(?:줘|주세요))?(?![가-힣])')


def text_filter_state(state):
    tables = [resource for resource in state.get('resources', []) if resource.get('kind') == 'table']
    if len(tables) != 1:
        return {'predicates': [], 'issues': [], 'cleared_fields': set(), 'replaced_numeric_fields': set()}
    fields = {field for field in tables[0].get('columns', []) if isinstance(field, str) and field}
    if not fields:
        return {'predicates': [], 'issues': [], 'cleared_fields': set(), 'replaced_numeric_fields': set()}
    active, pending, cleared_fields = {}, {}, set()
    numeric_fields, replaced_numeric_fields = set(), set()

    def ask(field, message, reason='predicate', proposed=()):
        pending[field] = PendingFilterDecision(reason, message, reason == 'cancel' or field in active, proposed)

    users = [(index, message) for index, message in enumerate(state.get('messages', [])) if message.get('role') == 'user']
    from agent_builder.conditions.condition_readiness import _comparison_spans, _comparison_subject, _compound_filter_in_turn, _uncertain_comparison, _excludes_named_range, _POLICY_SUBJECTS, _schedule_evidence
    from agent_builder.conditions.filter_edits import filter_edit_events, user_filter_text, filter_clauses
    for source, (turn, message) in enumerate(users):
        text = user_filter_text(state, turn, message)
        if text is None:
            continue
        event = next((entry for entry in state.get('condition_answers', []) if entry.get('turn') == turn), {})
        if 'filter.match' in event.get('candidate_keys', []) and text.strip().rstrip('.!?') == KEEP_ALL:
            for field in list(pending):
                if pending[field].can_keep:
                    pending.pop(field)
            continue
        from agent_builder.conditions.input_resolution import answer_kind
        nonanswer = answer_kind(text) != 'answer' or text.strip().rstrip('.!?') in {'취소', '아니', '아뇨', '상관없어', '상관없어요'}
        if nonanswer and 'filter.match' in event.get('candidate_keys', []):
            continue  # Do not turn refusal/uncertainty into a literal predicate.
        if len(pending) == 1 and 'filter.match' in event.get('candidate_keys', []):
            field = next(iter(pending))
            if not re.search(r'(?<![\w])' + re.escape(field), text):
                short = re.fullmatch(r"(.+?)만\s*+(?:남겨|골라)(?:\s*+(?:줘|주세요))?[.!?\s]*", text)
                if (pending[field].reason == 'predicate' and not short
                        and re.fullmatch(r'[\w가-힣-]+', text.strip())):
                    text = field + '가 ' + text.strip() + '인 행만 남겨줘'
                elif short and pending[field].reason != 'operator':
                    text = field + '가 ' + short[1] + '인 행만 남겨줘'
                elif re.fullmatch(r'(?:조건|필터)\s*+(?:취소|제거|해제)(?:해줘)?[.!?\s]*', text):
                    text = field + ' 조건 취소'
        numeric_turn = _compound_filter_in_turn(state, text)
        if numeric_turn and numeric_turn[0] == 'partial':
            numeric_fields.update(numeric_turn[1]['missing'])
        known_numeric = numeric_fields | {field for field, (_, rule) in active.items() if rule.get('value_type') == 'number'}
        known_numeric |= {field for field in fields if 'number' in (tables[0].get('profile', {}).get(field) or {}).get('types', [])}
        from agent_builder.conditions.filter_dialogue import confirmed_numeric_filter
        confirmed, _, _ = confirmed_numeric_filter(state)
        if confirmed:
            known_numeric.add(confirmed['field'])
        spans = _comparison_spans(text)
        for index, span in enumerate(spans):
            prefix = text[spans[index - 1].end() if index else 0:span.start()]
            known_numeric.update(field for field in fields if _comparison_subject(field, prefix))
        edits = filter_edit_events(text, fields, numeric_fields=known_numeric)
        conflicting_proposals = {}
        events = [(edit.start, {'clear': 'clear', 'keep': 'keep', 'uncertain': 'pending_cancel'}[edit.action],
                   field, f'{field} 조건을 취소할지 확인해주세요. 유지하거나 취소할 조건을 알려주세요.')
                  for edit in edits for field in edit.fields]
        for index, span in enumerate(spans):
            if _uncertain_comparison(text[span.end():]) or _excludes_named_range(text, span.end()):
                continue
            prefix = text[spans[index - 1].end() if index else 0:span.start()]
            events.extend((span.start(), 'numeric', field, None) for field in fields if _comparison_subject(field, prefix))
        for offset, clause in filter_clauses(text):
            matches = text_atoms(clause, fields)
            # Preserve the terminator so a short "정렬?" stays a question.
            ending = text[offset + len(clause):offset + len(clause) + 1]
            for index, match in enumerate(matches):
                field = match.field
                pending_kind = 'pending_operator' if match.operator == 'contains' else 'pending'
                tail = clause[match.end:matches[index + 1].start if index + 1 < len(matches) else len(clause)]
                action_tail = clause[matches[-1].end:] if match.joined else tail
                if selection_action_uncertain(action_tail):
                    events.append((offset + match.start, 'negative', field, match.operator))
                    continue
                if disjunctive_clause(clause):
                    events.append((offset + match.start, pending_kind, field, '텍스트 조건의 OR(또는) 결합은 현재 자동 확정할 수 없습니다. AND로 바꾸려면 모두 만족할 조건을 다시 적어주세요.'))
                    continue
                excluded = bool(_EXCLUDE_ACTION.match(action_tail))
                if excluded and len(matches) > 1:
                    events.append((offset + match.start, pending_kind, field, '여러 조건을 함께 만족하는 행을 제외할지, 각각 제외할지 확인해주세요.'))
                    continue
                if excluded and match.operator == 'contains':
                    events.append((offset + match.start, 'pending_operator', field,
                                   f'{field}에 특정 문구가 들어 있는 행을 제외하는 조건은 자동 확정할 수 없습니다. 적용할 조건을 다시 적어주세요.'))
                    continue
                bare = match.only and bool(re.match(r'\s*+(?:$|,|(?:이고|이며|그리고|및)\s|(?:CSV|JSON|파일|결과)(?:으로|로|\s))', tail, re.I))
                positive = bool(_POSITIVE_ACTION.match(action_tail + ending)) or bare
                if match.joined and matches[-1].only and re.match(
                        r'\s*+(?:$|(?:CSV|JSON|파일|결과)(?:으로|로|\s))', action_tail, re.I):
                    positive = True
                required = bool(re.match(r'\s*+이어야\s*+(?:해|합니다)', tail))
                if not (excluded or positive or required):
                    question = (f'{field}에 적용할 조건을 비교 방식과 함께 적어주세요.'
                                if match.operator == 'contains' else f'{field} 조건을 적용할지 확인해주세요.')
                    events.append((offset + match.start, pending_kind, field, question))
                    continue
                rule = dict(field=field, operator='ne' if excluded else match.operator, value=match.value,
                            compare_field=None, value_type='text')
                if (match.operator == 'eq'
                        and 'number' in (tables[0].get('profile', {}).get(field, {}) or {}).get('types', [])):
                    from agent_builder.conditions.condition_readiness import _amounts
                    from agent_builder.conditions.input_resolution import _PLAIN_AMOUNT, executable_number
                    amounts = _amounts(rule['value'], field) if _PLAIN_AMOUNT.fullmatch(rule['value'].strip()) else set()
                    if len(amounts) != 1 or not executable_number(next(iter(amounts))):
                        events.append((offset + match.start, 'pending', field, f'{field}의 숫자 기준값을 확인해주세요.'))
                        continue
                    rule.update(value=float(next(iter(amounts))), value_type='number')
                events.append((offset + match.start, 'predicate', field, rule))
            for field in uncovered_selection_fields(clause, fields, matches):
                events.append((offset + len(clause), 'pending', field, f'{field}의 선별 값을 확인해주세요.'))
            # Distinct comparisons of the same column in one AND clause cannot be
            # silently replayed as a replacement. Ask how they combine.
            for field in {atom.field for atom in matches}:
                if len({(atom.operator, atom.value) for atom in matches if atom.field == field}) > 1:
                    conflicting_proposals[field] = tuple(atom.value for atom in matches if atom.field == field)
                    events.append((offset + len(clause), 'pending_conflict', field, f'{field}의 여러 값을 어떻게 결합할지 확인해주세요.'))
            for field in sorted(fields):
                subject = re.search(r'(?<![\w])' + re.escape(field) + r'\s*+(?:선별\s*+)?(?:조건|필터|기준)', clause)
                if not subject or in_quoted_text(text, offset + subject.start()):
                    continue
                if denied_filter_change(clause[subject.end():]):
                    continue
                if not re.search(r'바꿔|바꾸|변경|수정|정할|미정|나중', clause[subject.end():]):
                    continue
                other_scope = (any(re.search(pattern, clause) for pattern in _POLICY_SUBJECTS.values())
                               or bool(_schedule_evidence(clause, follow_up=True))) and not re.search(r'필터|선별', clause)
                if not other_scope:
                    events.append((offset + subject.start(), 'pending', field, f'{field}의 새 선별 조건을 알려주세요.'))
        for position, kind, field, value in sorted(events, key=lambda event: event[0]):
            if kind == 'clear':
                active.pop(field, None)
                pending.pop(field, None)
                cleared_fields.add(field)
                numeric_fields.discard(field)
                replaced_numeric_fields.discard(field)
            elif kind == 'keep':
                if field in pending and pending[field].reason == 'cancel':
                    pending.pop(field)
            elif kind == 'numeric':
                active.pop(field, None)
                pending.pop(field, None)
                cleared_fields.discard(field)
                numeric_fields.add(field)
                replaced_numeric_fields.discard(field)
            elif kind == 'negative':
                if field not in cleared_fields:
                    reason = 'operator' if value == 'contains' else 'predicate'
                    ask(field, f'{field} 조건을 적용할지 확인해주세요. 적용할 조건을 적거나 {field} 조건 취소라고 알려주세요.', reason)
            elif kind in ('pending', 'pending_cancel', 'pending_conflict', 'pending_operator'):
                reason = {'pending_cancel': 'cancel', 'pending_conflict': 'conflict',
                          'pending_operator': 'operator'}.get(kind, 'predicate')
                ask(field, value, reason, conflicting_proposals.get(field, ()))
            elif kind == 'predicate':
                if field in conflicting_proposals:
                    continue  # A proposed replacement cannot mutate the confirmed value before resolution.
                intent = addition_intent(text, position=position)
                if intent == 'denied':
                    continue
                if intent == 'uncertain' or (intent == 'add' and (field in numeric_fields or field in active and active[field][1] != value)):
                    ask(field, f'{field}의 기존 값과 새 값을 함께 적용할지, 교체할지 확인해주세요.', 'addition')
                    continue
                active[field] = (source, value)
                if field in numeric_fields:
                    replaced_numeric_fields.add(field)
                    numeric_fields.discard(field)
                pending.pop(field, None)
                cleared_fields.discard(field)
    return {'predicates': list(active.values()), 'issues': [decision.message for decision in pending.values()], 'cleared_fields': cleared_fields,
            'replaced_numeric_fields': replaced_numeric_fields,
            'numeric_fields': numeric_fields,
            'pending_decisions': pending,
            'can_keep': bool(pending) and all(decision.can_keep for decision in pending.values())}


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


def literal_text_slots(state, catalog, primary):
    """Project the same replayed predicate into legacy slots without re-reading prose."""
    contract = text_filter_state(state)
    if contract['issues']:
        return {}
    predicates = contract['predicates']
    if primary in contract['cleared_fields']:
        primary = None  # A cancelled slot cannot own a retained text predicate.
    selected = next(((source, rule) for source, rule in predicates if rule['field'] == primary), None)
    if selected is None and primary is None and predicates:
        selected = predicates[0]
    if selected is None:
        return {}
    source, rule = selected
    turns = [index for index, message in enumerate(state.get('messages', [])) if message['role'] == 'user']
    evidence = [entry for entry in catalog if entry['source_id'] == 'user' and entry.get('turn') == turns[source]]
    if not evidence:
        return {}
    values = {'filter.field': rule['field'], 'filter.operator': rule['operator'], 'filter.value': rule['value']}
    return {key: {'key': key, 'status': 'specified', 'value': value,
                  'evidence_ids': [entry['id'] for entry in evidence], 'evidence': evidence,
                  'extraction': 'explicit_text_predicate'} for key, value in values.items()}
