"""Optional reference hints; retain every executable tool and native contract."""
import os
from pathlib import Path
import sys


def recommend_for_state(state):
    mode=os.getenv('A4B_TOOL_ADVICE_MODE','off')
    if mode=='off':return None
    if mode!='hint':raise ValueError('A4B_TOOL_ADVICE_MODE는 off/hint이어야 합니다.')
    backend=os.getenv('A4B_TOOL_ADVICE_BACKEND','legacy')
    if backend=='svm56':
        try:
            from workflow_engine.catalog.registry import TOOLS
            from ml.svm56.svm56_service_hook import recommend_svm_for_state
            return recommend_svm_for_state(state, TOOLS)
        except Exception as error:
            return {'ml_candidates': [], 'ml_status': 'failed', 'ml_error_type': type(error).__name__,
                    'advisory_only': True,
                    'ml_usage': '추천을 사용할 수 없습니다. 전체 등록 도구와 요청을 기준으로 검토하세요.'}
    if backend!='legacy':
        return {'ml_candidates': [], 'ml_status': 'invalid_backend', 'advisory_only': True,
                'ml_usage': '추천 설정을 확인하고 전체 등록 도구와 요청을 기준으로 검토하세요.'}
    from ml.advice.request_context import request_context, abstain
    try:
        query, context, reason = request_context(state)
    except (ValueError, TypeError):
        return abstain('invalid_request', {})
    if reason:
        return abstain(reason, context)
    root=Path(__file__).parents[1]/'legacy'
    bundle=str(root/'model_bundle')
    if bundle not in sys.path:sys.path.insert(0,bundle)
    from workflow_engine.catalog.registry import TOOLS
    from ml.legacy.policy.tool_advice import build_advice
    kinds=set(context['resource_kinds'])
    kind='both' if {'table','document'}<=kinds else 'table' if 'table' in kinds else 'document' if 'document' in kinds else 'none'
    columns=context['columns']
    try:
        from tool_rec.recommend import recommend_tools
        prediction=recommend_tools(query,kind=kind,columns=columns,has_saved_workflows=bool(state.get('saved_workflows')))
    except Exception as error:
        # The recommendation is optional: keep the registered tool catalogue
        # available and distinguish infrastructure failure from unsupported work.
        return {'ml_candidates': [], 'ml_usage': '도구 추천을 사용할 수 없어 등록된 전체 도구를 기준으로 검토합니다.',
                'ml_status':'failed','ml_error_type':type(error).__name__}
    advice,_=build_advice('C_ml_hint',TOOLS,prediction)
    return advice
