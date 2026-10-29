"""Optional reference hints; retain every executable tool and native contract."""
import os
from pathlib import Path
import sys


def recommend_for_state(state):
    mode=os.getenv('A4B_TOOL_ADVICE_MODE','off')
    if mode=='off':return None
    if mode!='hint':raise ValueError('A4B_TOOL_ADVICE_MODE는 off/hint이어야 합니다.')
    root=Path(__file__).parents[1]/'integrations/tool_recommender_reference'
    bundle=str(root/'model_bundle')
    if bundle not in sys.path:sys.path.insert(0,bundle)
    from .registry import TOOLS
    from integrations.tool_recommender_reference.policy.tool_advice import build_advice
    users=[m['content'] for m in state['messages'] if m['role']=='user']
    kinds={r['kind'] for r in state.get('resources',[])}
    kind='both' if {'table','document'}<=kinds else 'table' if 'table' in kinds else 'document' if 'document' in kinds else 'none'
    columns=list(dict.fromkeys(c for r in state.get('resources',[]) if r['kind']=='table' for c in r['columns']))
    try:
        from tool_rec.recommend import recommend_tools
        prediction=recommend_tools('\n'.join(users),kind=kind,columns=columns,has_saved_workflows=bool(state.get('saved_workflows')))
    except Exception as error:
        # The recommendation is optional: keep the registered tool catalogue
        # available and distinguish infrastructure failure from unsupported work.
        return {'ml_candidates': [], 'ml_usage': '도구 추천을 사용할 수 없어 등록된 전체 도구를 기준으로 검토합니다.',
                'ml_status':'failed','ml_error_type':type(error).__name__}
    advice,_=build_advice('C_ml_hint',TOOLS,prediction)
    return advice
