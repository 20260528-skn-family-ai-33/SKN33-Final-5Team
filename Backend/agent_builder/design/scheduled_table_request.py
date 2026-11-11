"""Recognize a fully specified schedule for exporting one unchanged table."""
import re

from agent_builder.conditions.condition_readiness import schedule_literals
from .condition_requirements import owned_specs
from .requirement_task_identity import _schedule_answer


_EXPORT = re.compile(
    r'\s*(?P<time>.+?)\s+(?:(?:이|연결한|연결된|첨부한)\s*)?표[를을]\s*'
    r'(?P<format>csv|json)\s*(?:파일)?(?:로|으로)\s*(?:저장|내보내)'
    r'(?:해)?\s*(?:줘|주세요|세요)?[.!\s]*', re.I)
_EDIT = re.compile(
    r'\s*예약\s*(?P<slot>시각|시간대|시간|요일|일자)(?:을|를|은|는)\s*(?P<value>.+?)'
    r'(?:으로|로)\s*(?:바꿔|변경해|수정해)\s*(?:줘|주세요)?[.!\s]*')
_EDIT_KEYS = {'시각':'schedule.hour', '시간':'schedule.hour', '시간대':'schedule.timezone',
              '요일':'schedule.weekday', '일자':'schedule.monthday'}


def _temporal_clause(text, *, follow_up):
    """Account for the entire clause; dates inside unrelated work are insufficient."""
    literals = schedule_literals(text, follow_up=follow_up)
    rest = text
    for span in sorted({span for _, _, span in literals}, key=len, reverse=True):
        rest = rest.replace(span, '', 1)
    if not re.fullmatch(r'(?:\s|으로|로|에|정각|정시)*', rest):
        return {}
    return {key: value for key, value, _ in literals}


def _refinement(state, turn, text, known):
    events = [e for e in state.get('condition_answers', []) if e.get('turn') == turn]
    if events:
        event = events[0]
        if (len(events) != 1 or len(event.get('question_ids', [])) != 1
                or len(event.get('candidate_keys', [])) != 1 or not _schedule_answer(state, turn, text)):
            return {}
        target = event['candidate_keys'][0]
        values = {k: v for k, v, _ in schedule_literals(text, follow_up=True)}
    else:
        edit = _EDIT.fullmatch(text)
        if not edit:
            return {}
        target = _EDIT_KEYS[edit['slot']]
        values = _temporal_clause(edit['value'], follow_up=True)
    coupled = {'schedule.hour': {'schedule.minute'}, 'schedule.monthday': {'schedule.month_end'}}
    allowed = {target} | coupled.get(target, set())
    if (target not in values or 'conflict' in values.values()
            or any(k not in allowed and known.get(k) != v for k, v in values.items())):
        return {}
    return values


def scheduled_table_plan(state, confirmed):
    """Build legacy task specs only when every user turn and attachment is accounted for."""
    assets = state.get('resources', [])
    if (not confirmed or set(confirmed['operations']) != {'schedule'} or len(assets) != 1
            or assets[0].get('kind') != 'table' or not assets[0].get('id')
            or any(state.get(k) for k in ('base_agent_id', 'required_node_refs', 'redesign_context', 'tool_gap_choice'))):
        return None
    users = [(i, m['content']) for i, m in enumerate(state['messages']) if m['role'] == 'user']
    if not users or not (request := _EXPORT.fullmatch(users[0][1])):
        return None
    values = _temporal_clause(request['time'], follow_up=False)
    if not values or 'conflict' in values.values():
        return None
    for turn, text in users[1:]:
        refinement = _refinement(state, turn, text, values)
        if not refinement:
            return None
        values.update(refinement)
    values['output.format'] = request['format'].lower()
    if values != {key: item['value'] for key, item in confirmed['slots'].items()}:
        return None
    specs = owned_specs(confirmed, state)
    if set(specs) != {'schedule', 'export'}:
        return None
    read = {'source_message': 0, 'description': '연결한 표 읽기',
            'spec': {'kind': 'read', 'file_ids': [assets[0]['id']]}}
    # The schedule qualifies the run; the sole output consumes the connected table.
    tasks = [{**item, 'id': task_id, 'depends_on': dependencies, 'quote': users[item['source_message']][1]}
             for item, task_id, dependencies in ((read, 'r1', []), (specs['schedule'], 'r2', []),
                                                 (specs['export'], 'r3', ['r1', 'r2']))]
    return {'tasks': tasks, 'unresolved': []}
