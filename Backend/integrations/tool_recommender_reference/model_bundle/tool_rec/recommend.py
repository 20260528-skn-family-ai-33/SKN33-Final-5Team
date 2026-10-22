"""A4B에 붙일 추천 함수. 설계 대화의 list_tools가 전체 16개 대신 이 후보를 먼저 돌려주면 된다.

  from tool_rec.recommend import recommend_tools
  recommend_tools('유형별로 몇 건인지 세서 저장해 줘', kind='table', columns=['유형', '처리상태'])
  → {'candidates': ['read_table', 'aggregate_table', 'write_result', ..., 'finish'], 'probs': {...}, 'full_list': False}

- 완료 안내(finish)는 항상 넣고, 저장된 워크플로우가 있으면 saved_workflow도 넣는다.
- 가장 높은 확률이 0.2 미만이면 full_list=True: 후보를 줄이지 말고 16개 전체를 보여 준다.
"""
import os

import joblib
import pandas as pd

from . import select
from .labels import TOOLS

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models', 'tool_recommender.joblib')
_B = None


def recommend_tools(request, kind='none', columns=(), has_saved_workflows=False, k=5, tau_low=0.2):
    global _B
    if _B is None:
        _B = joblib.load(_PATH)
    df = pd.DataFrame({'id': ['q'], 'request': [request], 'kind': [kind], 'columns_text': [' '.join(columns)], 'group': ['q']})
    p = _B['calibrator'].transform(_B['model'].scores(df))[0]
    cands = select.candidates(p[None, :], 'topk', k=k)[0]
    out = [TOOLS[j] for j in cands]
    full = bool(p.max() < tau_low)
    out += ['finish'] + (['saved_workflow'] if has_saved_workflows else [])
    return {'candidates': out, 'probs': {t: round(float(v), 3) for t, v in zip(TOOLS, p)}, 'full_list': full}
