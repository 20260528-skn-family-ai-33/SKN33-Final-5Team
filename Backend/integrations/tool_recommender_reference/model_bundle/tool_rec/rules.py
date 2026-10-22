"""R0 규칙 기준선과 핵심 단어 사전 (계획서 4장 F-4, 5장 R0).

사전은 registry.py 도구 설명의 단어와 그 흔한 한국어 바꿔 말하기로 만들었다.
평가 세트 문장에 맞춰 고치면 안 된다. 고칠 때는 학습 데이터(D1)만 보고 고친다.
"""
import re

import numpy as np

from .labels import IDX, TOOLS
from .preprocess import negation, normalize

KEYWORDS = {
    'filter_sort': r'조건|비교|골라|고르|만\s*남|필터|제외|빼고|정렬|순으로|순서|중복|빈\s*값|비어|결측|이상|이하|초과|미만|넘는|포함|들어간',
    'aggregate_table': r'집계|합계|총합|건수|몇\s*건|별로|별\s|세어|세서|sum|count',
    'write_result': r'저장|파일|csv|엑셀|xlsx|json',
    'export_result': r'저장|파일|json|markdown|마크다운|\bmd\b',
    'extract_information': r'추출|뽑아|항목|필드|값을\s*찾',
    'search_document': r'검색|관련\s*내용|관련된\s*부분|어디에|찾아',
    'compose_answer': r'답변|답해|근거|요약|설명해',
    'reply_review': r'검수|검토|규정에\s*맞|정책에\s*맞|초안.*점검|문제\s*구절',
    'review_summary': r'검수\s*보고서|검토\s*보고서|보고서로\s*정리',
    'agent_node': r'작성|써\s*줘|써줘|바꿔|변환|다듬|만들어|초안|정리해',
    'condition': r'경우|이면|없으면|있으면|분기|따라\s*다르',
    'ask_user': r'물어|확인받|확인\s*후|승인|되묻',
}
_RX = {t: re.compile(p, re.I) for t, p in KEYWORDS.items()}


def keyword_flags(text):
    """사전 단어가 있으면 1. 부정 문장은 지우고 본다."""
    t = negation(normalize(text), 'drop')
    return [1 if t_ in _RX and _RX[t_].search(t) else 0 for t_ in TOOLS]


def r0_predict(request, kind):
    """규칙 기준선: 자료 종류 + 핵심 단어. 결과는 0/1 벡터."""
    flags = keyword_flags(request)
    v = np.zeros(len(TOOLS))
    has_table = kind in ('table', 'both')
    has_doc = kind in ('document', 'both')
    if has_table:
        v[IDX['read_table']] = 1
        for t in ('filter_sort', 'aggregate_table', 'write_result'):
            v[IDX[t]] = flags[IDX[t]]
    if has_doc:
        v[IDX['read_document']] = 1
        for t in ('extract_information', 'search_document', 'compose_answer', 'export_result'):
            v[IDX[t]] = flags[IDX[t]]
    if not has_table and not has_doc:
        v[IDX['agent_node']] = 1
        v[IDX['export_result']] = flags[IDX['export_result']]
    for t in ('reply_review', 'review_summary', 'condition', 'ask_user'):
        v[IDX[t]] = max(v[IDX[t]], flags[IDX[t]])
    if has_table and has_doc and v[IDX['write_result']] and not v[IDX['filter_sort']] and not v[IDX['aggregate_table']]:
        v[IDX['write_result']] = 0  # 표 결과가 아니면 결과 저장 쪽
    return v


def r0_scores(df, prior):
    """순위 지표용 점수: 규칙에 걸린 도구가 먼저, 동점은 학습 데이터의 도구 빈도(prior) 순."""
    hits = np.array([r0_predict(q, k) for q, k in zip(df['request'], df['kind'])])
    return hits + 0.01 * np.asarray(prior)[None, :]
