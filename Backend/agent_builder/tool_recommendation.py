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
    from tool_rec.recommend import recommend_tools
    from .registry import TOOLS
    from integrations.tool_recommender_reference.policy.tool_advice import build_advice
    users=[m['content'] for m in state['messages'] if m['role']=='user']
    kinds={r['kind'] for r in state.get('resources',[])}
    kind='both' if {'table','document'}<=kinds else 'table' if 'table' in kinds else 'document' if 'document' in kinds else 'none'
    columns=list(dict.fromkeys(c for r in state.get('resources',[]) if r['kind']=='table' for c in r['columns']))
    prediction=recommend_tools('\n'.join(users),kind=kind,columns=columns,has_saved_workflows=bool(state.get('saved_workflows')))
    advice,_=build_advice('C_ml_hint',TOOLS,prediction)
    return advice
