"""A reversible off/shadow/enforce adapter; off makes no additional LLM calls."""
import json
import os
from copy import deepcopy

from ..condition_readiness import judge_conditions, plan_issues
from ..models import AgentDraft


def condition_mode(mode=None):
    setting = mode or os.getenv('A4B_CONDITION_MODE', 'off')
    if setting not in ('off', 'shadow', 'enforce'):
        raise ValueError('A4B_CONDITION_MODE는 off/shadow/enforce이어야 합니다.')
    return setting


def _transport(api_key, model):
    if os.getenv('A4B_MAIN_PROVIDER') == 'ollama':
        from .direct_conversation import OllamaPlanner
        return OllamaPlanner(model)
    from ..model_factory import create_chat_model
    from ..condition_readiness import SingleWorkProposal
    class Structured:
        def invoke(self, messages):
            # The provider is selected by the same application model factory.
            llm = create_chat_model(api_key, model, timeout=70, max_retries=0)
            parsed = llm.with_structured_output(SingleWorkProposal, method='json_schema').invoke(messages)
            return {'content': parsed.model_dump_json() if hasattr(parsed, 'model_dump_json') else json.dumps(parsed)}
    return Structured()


def _pending_draft(state, ledger):
    from ..models import ExecutionPlan
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
        try:
            # Build a transport lazily: a failed input never calls the model.
            from ..condition_readiness import input_issues
            from ..input_resolution import latest_resolution
            if input_issues(state) or latest_resolution(state):
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
        if setting == 'shadow' or ledger['decision'] == 'ready':
            if setting == 'enforce':
                update['pending_questions'] = []
                plan_mode=os.getenv('A4B_CONDITION_PLAN_MODE','llm')
                if plan_mode not in ('llm','template'):raise ValueError('A4B_CONDITION_PLAN_MODE는 llm/template이어야 합니다.')
                update['condition_template']=plan_mode=='template'
                if plan_mode=='template':update['condition_route']='template_plan'
            return update
        update.update(condition_route='supervisor', confirmed=False, saved_agent_id=None,
                      draft=_pending_draft(state, ledger), pending_questions=ledger.get('questions', []),
                      issues=[], semantic_issues=[], verification_enforced=False)
        # Existing UI renders free-text questions from assistant history.
        if ledger['decision'] in ('clarify', 'deferred'):
            reply='\n'.join(q['text'] for q in ledger['questions'])
        elif ledger['decision']=='unsupported':
            reply='지원 범위: '+' / '.join(ledger.get('unsupported_actions',[]))
        else:
            reply=' '.join(ledger.get('issues') or ['조건을 자동 확정하지 않았습니다. 대표 업무의 범위와 변경 내용을 확인해주세요.'])
        update['messages']=state.get('messages',[])+[{'role':'assistant','content':reply}]
        if ledger['decision'] in ('clarify', 'deferred'):
            update.update(status='needs_clarification')
        elif ledger['decision'] == 'unsupported':
            update.update(status='unsupported')
        else:
            update.update(condition_route='stop', status='validation_failed',
                          issues=ledger.get('issues') or ['대표 업무의 지원 범위를 벗어났거나 작업 변경·취소 확인이 필요합니다.'])
        return update
    return check


def audit_conditions(state):
    ledger = state.get('condition_ledger')
    if not ledger or ledger.get('decision') != 'ready':
        return {}
    issues = plan_issues(ledger, state['draft'])
    update = {'trace': state.get('trace', []) + [{'node': 'condition_audit', 'label': '확정 조건 반영 검사',
               'status': 'completed' if not issues else 'needs_input', 'detail': ' / '.join(issues) or '확정 조건이 실행 설정에 반영됐습니다.',
               'issues': issues, 'enforced': state.get('condition_enforced', False)}]}
    if state.get('condition_enforced') and issues:
        update['semantic_issues'] = list(dict.fromkeys(state.get('semantic_issues', []) + issues))
        if state['draft'].get('questions') or state['draft'].get('unsupported'):
            draft = deepcopy(state['draft'])
            draft['questions'] = []; draft['question_options'] = []; draft['unsupported'] = []
            update['draft'] = draft
    return update
