"""A reversible off/shadow/enforce adapter; off makes no additional LLM calls."""
import json
import os
import re
from copy import deepcopy

from agent_builder.conditions.condition_readiness import judge_conditions, plan_issues
from contracts.models import AgentDraft

# A comparison/summary word alone does not make a request multi-step analysis. When the
# conversation also states a single-table selection (a numeric threshold, "행만 남겨", or an
# empty/duplicate policy), the grounded checker must stay on: live 2026-11-03 runs sent
# "반품 건 정리를 위해 비용이 100만원 초과인 행만 남겨줘. 빈 비용은 제외해…" to the full planner,
# which failed 9/9 such requests and confirmed 3 of them with the wrong policies.
# Real analysis work is still delegated below when the capability check selects extended tools.
_THRESHOLD = (r'(?:\d[\d,]*(?:\.\d+)?\s*(?:천만|백만|십만|만|천|백)?|(?<![가-힣])[일이삼사오육칠팔구십]*[십백천만억]+)'
              r'\s*(?:원|점|개|명|건|kg|%)?\s*(?:을|를|이|가|보다)?\s*'
              r'(?:초과|이상|미만|이하|넘|밑돌|보다\s*(?:크|작|많|적))')
_SINGLE_TABLE_SIGNAL = re.compile(
    _THRESHOLD + r'|행(?:만|은|을|들)?\s*(?:남겨|남기|골라|고르|뽑|추려|걸러|빼)'
    r'|중복\s*[가-힣A-Za-z0-9_]*\s*(?:은|는|이|가)?\s*(?:첫|마지막|모두|전부|유지|제거|오류)'
    r'|빈\s*[가-힣A-Za-z0-9_]{0,8}\s*(?:은|는|이|가|이면|이\s*있으면)?\s*(?:제외|유지|그대로|오류|빼)')


def condition_mode(mode=None):
    setting = mode or os.getenv('A4B_CONDITION_MODE', 'off')
    if setting not in ('off', 'shadow', 'enforce'):
        raise ValueError('A4B_CONDITION_MODE는 off/shadow/enforce이어야 합니다.')
    return setting


def _transport(api_key, model):
    if os.getenv('A4B_MAIN_PROVIDER') == 'ollama':
        from agent_builder.design.nodes.direct_conversation import OllamaPlanner
        return OllamaPlanner(model)
    from infrastructure.llm.model_factory import create_chat_model
    from agent_builder.conditions.condition_readiness import SingleWorkProposal
    class Structured:
        def invoke(self, messages):
            # The provider is selected by the same application model factory.
            llm = create_chat_model(api_key, model, timeout=70, max_retries=0)
            parsed = llm.with_structured_output(SingleWorkProposal, method='json_schema').invoke(messages)
            return {'content': parsed.model_dump_json() if hasattr(parsed, 'model_dump_json') else json.dumps(parsed)}
    return Structured()


def _pending_draft(state, ledger):
    from contracts.models import ExecutionPlan
    evidence = next((e for slot in ledger.get('slots', {}).values() for e in slot.get('evidence', []) if e['source_id'] == 'user'), None)
    requirements = [{'description': '사용자 요청의 확인 조건', 'source_id': 'user', 'quote': evidence['quote']}] if evidence else []
    return AgentDraft(name='업무 조건 확인', purpose='사용자 조건 확인 후 업무 설계', instructions='확정 조건과 사용자 답변을 보존합니다.',
        tool_ids=[], document_ids=[r['id'] for r in state.get('resources', [])], requirements=requirements,
        questions=[q['text'] for q in ledger.get('questions', [])], question_options=[q.get('options', []) for q in ledger.get('questions', [])],
        unsupported=ledger.get('unsupported_actions', []) if ledger['decision'] == 'unsupported' else [],
        explanation='필수 업무 조건을 확인합니다.' if ledger['decision'] == 'clarify' else '지원 범위 또는 입력 상태를 확인하세요.',
        execution_plan=ExecutionPlan(filters=[], sort=[], match='all', missing_policy='keep', duplicate_policy='keep', duplicate_field=None, output_format='json'),
        workflow=None, schedule=None, report_destination=None).model_dump()


def make_condition_check(api_key='', model='gpt-4.1-mini', transport=None, mode=None):
    configured = condition_mode(mode)
    def check(state):
        setting='enforce' if state.get('condition_required') or state.get('condition_enforced') else configured
        if setting == 'off':
            return {'condition_enforced': False, 'condition_route': 'conversation', 'condition_ledger': None, 'condition_template': False}
        user_requests=' '.join(m['content'] for m in state.get('messages',[]) if m['role']=='user')
        import re
        outside_table_scope=(re.search(r'비교|증감|증가율|감소율|변화율|반품|환불|정정|그 전주',user_requests) or
            ('요약' in user_requests and not re.search(r'선별|중복|필터',user_requests)))
        if setting=='enforce' and outside_table_scope and not _SINGLE_TABLE_SIGNAL.search(user_requests):
            return {'condition_ledger':None,'condition_enforced':False,'condition_required':False,
                    'condition_route':'conversation','condition_template':False,'pending_questions':[],
                    'trace':state.get('trace',[])+[{'node':'condition_check','label':'업무 조건 확인','status':'completed',
                        'detail':'outside_scope: 비교·요약 업무를 전체 설계기로 위임','mode':setting,'model_calls':0,'metrics':{}}]}
        assessment=state.get('capability_assessment') or {}
        table_contract_tools={'read_table','filter_sort','write_result','finish'}
        selected=set(assessment.get('tool_ids',[]))
        if setting=='enforce' and assessment.get('decision')=='existing_tools' and selected-table_contract_tools:
            # This checker only owns single-table filtering/deduplication/scheduling.
            # A supported join, scalar, document or connector operation belongs to the
            # full planner; it still goes through native validation and user approval.
            return {'condition_ledger':None,'condition_enforced':False,'condition_required':False,
                    'condition_route':'conversation','condition_template':False,'pending_questions':[],
                    'trace':state.get('trace',[])+[{'node':'condition_check','label':'업무 조건 확인','status':'completed',
                        'detail':'outside_scope: 등록된 확장 도구 요청을 전체 설계기로 위임','mode':setting,'model_calls':0,'metrics':{}}]}
        from agent_builder.design.document_rows_template import document_rows_applies
        if setting == 'enforce' and document_rows_applies(state):
            # Document-to-table work is outside this checker's table scope; the fixed flow decides it in code.
            return {'condition_ledger': None, 'condition_enforced': False, 'condition_required': False,
                    'condition_route': 'conversation', 'condition_template': False, 'pending_questions': [],
                    'trace': state.get('trace', []) + [{'node': 'condition_check', 'label': '업무 조건 확인', 'status': 'completed',
                        'detail': 'outside_scope: 문서 표 정리 고정 흐름으로 위임', 'mode': setting, 'model_calls': 0, 'metrics': {}}]}
        try:
            # Build a transport lazily: a failed input never calls the model.
            from agent_builder.conditions.condition_readiness import input_issues
            from agent_builder.conditions.input_resolution import latest_resolution, option_answer, numeric_answer, pending_nonanswer
            if input_issues(state) or latest_resolution(state) or option_answer(state) or numeric_answer(state) or pending_nonanswer(state):
                result = judge_conditions(state, None)
            else:
                result = judge_conditions(state, transport or _transport(api_key, model), max_contract_repairs=1)
            ledger = result['ledger']
            detail = ledger['decision']
        except Exception as error:
            # A model/contract error is not a business clarification or unsupported request.
            ledger = {**deepcopy(state.get('condition_ledger') or {}),
                      'decision': 'system_error', 'issues': ['조건 판정을 완료하지 못했습니다. 모델 연결과 출력 계약을 확인하세요.'],
                      'questions': [], 'slots': {}, 'operations': [], 'error_type': type(error).__name__}
            ledger['slots'] = deepcopy((state.get('condition_ledger') or {}).get('slots', {}))
            ledger['operations'] = list((state.get('condition_ledger') or {}).get('operations', []))
            result = {'model_calls': getattr(error, 'model_calls', None), 'metrics': {}}
            detail = '판정 실패: ' + type(error).__name__
        update = {'condition_ledger': ledger, 'condition_enforced': setting == 'enforce',
                  'condition_required': setting == 'enforce',
                  'condition_route': 'conversation', 'condition_template':False,
                  'trace': state.get('trace', []) + [{'node': 'condition_check', 'label': '업무 조건 확인',
                     'status': 'completed' if ledger['decision'] == 'ready' else 'needs_input',
                     'detail': detail, 'mode': setting, 'model_calls': result['model_calls'], 'metrics': result['metrics']}]}
        if setting == 'enforce' and ledger.get('outside_scope'):
            # This checker covers table filtering/deduplication/scheduling only.
            # Other work still goes through capability checks, normal design,
            # validation and approval; it is not a READY condition verdict.
            update.update(condition_ledger=None, condition_enforced=False,
                          condition_required=False, pending_questions=[])
            update['trace'][-1].update(status='completed', detail='outside_scope: 기존 설계 경로로 위임')
            return update
        if setting == 'shadow' or ledger['decision'] == 'ready':
            if setting == 'enforce':
                update['pending_questions'] = []
                # auto: confirmed filter/dedup/schedule conditions become the draft in code; anything the
                # template cannot express falls back to the planner. llm restores planner-only generation.
                plan_mode=os.getenv('A4B_CONDITION_PLAN_MODE','auto')
                if plan_mode not in ('llm','template','auto'):raise ValueError('A4B_CONDITION_PLAN_MODE는 llm/template/auto이어야 합니다.')
                update['condition_template']=plan_mode in ('template','auto') and bool(ledger.get('operations'))
                if update['condition_template']:update['condition_route']='template_plan'
            return update
        update.update(condition_route='supervisor', confirmed=False, saved_agent_id=None,
                      draft=_pending_draft(state, ledger), pending_questions=ledger.get('questions', []),
                      issues=[], semantic_issues=[], verification_enforced=False)
        # Existing UI renders free-text questions from assistant history.
        if ledger['decision'] in ('clarify', 'deferred'):
            reply='\n'.join(q['text'] for q in ledger['questions'])
        elif ledger['decision']=='unsupported':
            reply='요청한 동작이 현재 도구로 가능한지 추가 확인이 필요합니다. 수행하려는 작업을 구체적으로 알려주세요.'
        else:
            conflicts=[q for q in ledger.get('unresolved',[]) if ledger.get('slots',{}).get(q,{}).get('status')=='conflict']
            from agent_builder.conditions.condition_readiness import QUESTIONS
            detail=['서로 다른 조건이 함께 요청됐습니다. '+QUESTIONS[key] for key in conflicts if key in QUESTIONS]
            reply=' '.join((ledger.get('issues') or ['조건을 자동 확정하지 않았습니다. 대표 업무의 범위와 변경 내용을 확인해주세요.'])+detail)
        update['messages']=state.get('messages',[])+[{'role':'assistant','content':reply}]
        if ledger['decision'] in ('clarify', 'deferred'):
            update.update(status='needs_clarification')
        elif ledger['decision'] == 'unsupported':
            update.update(status='unsupported')
        else:
            update.update(condition_route='stop', status='validation_failed',
                          issues=(ledger.get('issues') or ['대표 업무의 지원 범위를 벗어났거나 작업 변경·취소 확인이 필요합니다.'])+detail)
        return update
    return check


def _confirmed_schedule(state, ledger):
    """The draft with its schedule taken from the confirmed slots, or None when nothing changes.

    The schedule is fully decided before generation (every slot specified and grounded), so the
    planner has nothing to choose. Live 2026-11-03 (RunPod, weekly request with every value written):
    the planner left draft.schedule empty in 2 of 3 runs and the turn ended validation_failed.
    """
    if not state.get('condition_enforced') or 'schedule' not in (ledger.get('operations') or []):
        return None
    slots = ledger.get('slots') or {}
    spec = {key: (slots.get('schedule.' + key) or {}).get('value') for key in ('frequency', 'timezone', 'hour', 'minute', 'weekday')}
    if spec['frequency'] == 'daily':
        spec['weekday'] = None
    if any(spec[key] is None for key in ('frequency', 'timezone', 'hour', 'minute')) or (spec['frequency'] == 'weekly' and spec['weekday'] is None):
        return None  # not fully confirmed; plan_issues keeps reporting the mismatch
    from contracts.models import ScheduleSpec
    spec = ScheduleSpec(**spec).model_dump()
    draft = state.get('draft') or {}
    if draft.get('schedule') == spec:
        return None
    return {**deepcopy(draft), 'schedule': spec}


def audit_conditions(state):
    from agent_builder.conditions.condition_readiness import canonical_written_operators
    corrected, changes = canonical_written_operators(state, state.get('draft') or {})
    if corrected is not None:
        state = {**state, 'draft': corrected,
                 'trace': state.get('trace', []) + [{'node': 'condition_audit', 'label': '원문 비교 방식 반영', 'status': 'completed',
                                                     'detail': '요청에 적힌 비교 방식으로 바로잡음: ' + ', '.join(changes), 'model_calls': 0}]}
    ledger = state.get('condition_ledger')
    if not ledger or ledger.get('decision') != 'ready':
        return {'draft': state['draft'], 'trace': state['trace']} if corrected is not None else {}
    from agent_builder.conditions.condition_readiness import literal_condition_issues
    from agent_builder.design.planner_constraints import table_tool_issues
    filled = _confirmed_schedule(state, ledger)
    if filled is not None:
        state = {**state, 'draft': filled}
    issues = literal_condition_issues(state) + plan_issues(ledger, state['draft']) + table_tool_issues(state)
    update = {'trace': state.get('trace', []) + [{'node': 'condition_audit', 'label': '확정 조건 반영 검사',
               'status': 'completed' if not issues else 'needs_input', 'detail': ' / '.join(issues) or '확정 조건이 실행 설정에 반영됐습니다.',
               'issues': issues, 'enforced': state.get('condition_enforced', False)}]}
    if corrected is not None:
        update['draft'] = state['draft']
    if filled is not None:
        update['draft'] = filled
        update['trace'][-1]['schedule_filled'] = filled['schedule']
    if state.get('condition_enforced') and issues:
        update['semantic_issues'] = list(dict.fromkeys(state.get('semantic_issues', []) + issues))
        if state['draft'].get('questions') or state['draft'].get('unsupported'):
            draft = deepcopy(state['draft'])
            draft['questions'] = []; draft['question_options'] = []; draft['unsupported'] = []
            update['draft'] = draft
    return update
