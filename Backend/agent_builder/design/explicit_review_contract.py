"""Own an explicit analysis -> human review -> final memo instruction.

Only the written linear sequence over one document is recognized. Other tasks
remain model-extracted and independently validated; none are discarded here.
"""
import re
from copy import deepcopy

from .request_guards import review_request

_ORDER=re.compile(r'(?:계약|문서)\s*분석\s*(?:\(LLM\))?\s*(?:→|->)\s*'
                  r'사람\s*검토\s*(?:\(Human Review\))?\s*(?:→|->)\s*'
                  r'최종\s*(?:메모|보고서)\s*(?:\(LLM\))?',re.I)


def explicit_review_contract(state):
    assets=state.get('resources',[])
    if len(assets)!=1 or assets[0].get('kind')!='document' or review_request(state)!=(True,True):return None
    users=[m['content'] for m in state.get('messages',[]) if m.get('role')=='user']
    # A subsequent turn can revise ordering or introduce a different job.
    if len(users)!=1 or not _ORDER.search(users[0]):return None
    return {'source_message':0,'request':users[0],
            'owned_kinds':['document_analysis','human_review']}


def unrequested_review_kinds(state):
    contract=explicit_review_contract(state)
    if not contract:return set()
    text=contract['request']
    excluded=set()
    if not re.search(r'합계|합산|집계|건수|총액|평균|최댓값|최솟값|\b(?:sum|count|aggregate)\b',text,re.I):excluded.add('aggregate')
    if not re.search(r'예약|주기|매일|매주|매달|매월|시간마다|\b(?:hourly|daily|weekly|monthly|schedule)\b',text,re.I):excluded.add('schedule')
    return excluded


def project_explicit_review(candidates,state):
    contract=explicit_review_contract(state)
    if not contract:return candidates
    result=deepcopy(candidates)
    # The full original source is copied by extract_plan after projection.
    for kind,description in [('document_analysis','문서 분석 및 검토 후 최종 메모'),('human_review','사람 검토 의견 대기')]:
        if any(item['spec'].get('kind')==kind for item in result):continue
        item=dict(source_message=contract['source_message'],description=description,spec=dict(kind=kind))
        if kind=='document_analysis':
            index=next((i for i,x in enumerate(result) if x['spec'].get('kind')=='human_review'),len(result))
            result.insert(index,item)
        else:result.append(item)
    return result
