"""Prepared offline regressions; run only after the live baseline is complete.

T02 development raw below comes from the preserved instrumentation-interrupted
public9b run: the extractor added unread and the model reviewer said faithful.
That raw response demonstrates a validation gap, not a valid full-run score.
No prompt-text assertions or claims about future live model behavior are made.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from agent_builder.design.gmail_summary import gmail_summary_reply
from agent_builder.design.request_meaning import (
    extract_meaning, gmail_selection, make_meaning_node, meaning_fingerprint,
    sealed_meaning, validate_meaning,
)
from agent_builder.design.temporal_context import ensure_temporal_context
from tests.conditions.test_condition_readiness import state
from tests.design.test_request_meaning import Extractor, meaning


T02_RAW = {
    'tasks': [{
        'source': 'gmail', 'actions': ['read', 'summarize'],
        'selection': {'order': 'newest_first', 'limit': None,
                      'limit_evidence': None, 'limit_source_message': None,
                      'period': 'today', 'period_source_message': 0,
                      'period_evidence': '오늘', 'filters': ['unread']},
        'schedule': None, 'summary_focus': [], 'other_requirements': [],
        'source_message': 0,
    }],
    'unresolved': [],
}


def request(text):
    current=state(text);current['resources']=[];current['user_timezone']='America/New_York'
    current.update(ensure_temporal_context(current,now='2026-11-10T01:00:00Z'))
    return current


def test_t02_raw_unread_is_rejected_even_when_the_model_claims_faithful():
    current=request('Gmail에서 오늘 받은 메일을 요약해줘.')
    transport=Extractor(deepcopy(T02_RAW),reviews=[{'faithful':True,'issues':[]}])
    update=make_meaning_node('synthetic-test',transport=transport)(current)
    assert update['request_meaning'] is None
    restored={**current,**update}
    assert gmail_selection(restored) is None
    assert gmail_summary_reply(restored) is None


def test_t02_ungrounded_filter_is_invalid_before_model_review():
    with pytest.raises(ValueError):
        validate_meaning(deepcopy(T02_RAW),request('Gmail에서 오늘 받은 메일을 요약해줘.'))


@pytest.mark.parametrize('description',['안 읽은','읽지 않은','미읽음','unread'])
def test_explicit_unread_condition_remains_compilable(description):
    current=request(f'Gmail에서 오늘 {description} 메일을 요약해줘.')
    current.update(extract_meaning(current,Extractor(deepcopy(T02_RAW))))
    assert 'is:unread' in gmail_selection(current)['query']


def test_followup_quantity_change_preserves_grounded_unread_and_original_day():
    current=request('Gmail에서 오늘 안 읽은 메일 5개를 요약해줘.')
    initial=meaning(count=5,evidence='5개',period='today',filters=['unread'])
    current.update(extract_meaning(current,Extractor(initial)))
    original_query=gmail_selection(current)['query']
    current['messages'].append({'role':'user','content':'10개로 바꿔줘.'})
    current.update(ensure_temporal_context(current,now='2026-11-11T01:00:00Z'))
    revised=meaning(count=10,evidence='10개',period='today',filters=['unread'])
    revised['tasks'][0]['selection']['limit_source_message']=1
    current.update(extract_meaning(current,Extractor(revised)))
    selected=gmail_selection(current)
    assert selected['count']==10 and selected['query']==original_query


def test_cancelled_unread_condition_is_not_restored_from_an_earlier_user_turn():
    current=request('Gmail에서 오늘 안 읽은 메일을 요약해줘.')
    current['messages'].append({'role':'user','content':'미읽음 필터는 빼고 읽음 여부와 관계없이 요약해줘.'})
    with pytest.raises(ValueError):validate_meaning(deepcopy(T02_RAW),current)
    revised=deepcopy(T02_RAW);revised['tasks'][0]['selection']['filters']=[]
    current.update(extract_meaning(current,Extractor(revised)))
    assert 'is:unread' not in gmail_selection(current)['query']


def test_negated_unread_selection_does_not_ground_an_unread_filter():
    current=request('Gmail에서 오늘 안 읽은 메일만 고르지는 말고 모든 메일을 요약해줘.')
    with pytest.raises(ValueError):validate_meaning(deepcopy(T02_RAW),current)


def stale_reviewed_state():
    """A persisted faithful review can contain a pre-fix semantic mistake."""
    current=request('Gmail에서 오늘 받은 메일을 요약해줘.')
    raw=deepcopy(T02_RAW)
    digest=hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    fingerprint=meaning_fingerprint(current)
    current.update(request_meaning=raw,request_meaning_digest=digest,
        request_meaning_fingerprint=fingerprint,
        request_meaning_review={'faithful':True,'issues':[],
            'meaning_digest':digest,'request_fingerprint':fingerprint})
    return current


def test_previously_reviewed_ungrounded_filter_cannot_compile_from_checkpoint():
    current=stale_reviewed_state()
    assert sealed_meaning(current) is None
    assert gmail_selection(current) is None
    assert gmail_summary_reply(current) is None


def test_invalid_cached_filter_is_reinterpreted_without_aborting_worker():
    current=stale_reviewed_state()
    corrected=deepcopy(T02_RAW);corrected['tasks'][0]['selection']['filters']=[]
    update=make_meaning_node('synthetic-test',transport=Extractor(corrected))(current)
    restored={**current,**update}
    assert restored['request_meaning']['tasks'][0]['selection']['filters']==[]
    assert 'is:unread' not in gmail_selection(restored)['query']


@pytest.mark.parametrize('text',[
    'Gmail에서 오늘 안 읽은 모든 메일을 요약해줘.',
    'Gmail에서 오늘 읽은 메일 말고 안 읽은 메일만 요약해줘.',
    'Gmail에서 오늘 안 읽은 메일을 요약해줘. 미읽음 필터는 빼지 말고 유지해.',
])
def test_positive_unread_selection_is_not_reversed_by_adjacent_words(text):
    current=request(text)
    current.update(extract_meaning(current,Extractor(deepcopy(T02_RAW))))
    assert 'is:unread' in gmail_selection(current)['query']


@pytest.mark.parametrize('update',[
    '읽은 메일만 대상으로 바꿔줘.',
    '읽음 여부와 관계없이 요약해줘.',
    '미읽음 필터를 해제해줘.',
    '미읽음 조건은 적용하지 않도록 해줘.',
    '미읽음 필터 없이 요약해줘.',
    '전체 메일을 요약해줘.',
])
def test_latest_read_state_change_invalidates_an_earlier_unread_selection(update):
    current=request('Gmail에서 오늘 안 읽은 메일을 요약해줘.')
    current['messages'].append({'role':'user','content':update})
    with pytest.raises(ValueError):validate_meaning(deepcopy(T02_RAW),current)


def test_literal_subject_value_is_not_unread_selection_evidence():
    current=request('Gmail에서 오늘 받은 메일을 요약하고 제목 unread인 메일을 보내줘.')
    with pytest.raises(ValueError):validate_meaning(deepcopy(T02_RAW),current)


@pytest.mark.parametrize('text',[
    'Summarize Gmail messages that are not unread.',
    'Exclude unread messages from the Gmail summary.',
    'Summarize Gmail messages with unread messages excluded.',
])
def test_negated_canonical_english_read_state_cannot_authorize_unread(text):
    candidate=meaning(count=None,evidence=None,filters=['unread'])
    with pytest.raises(ValueError):validate_meaning(candidate,request(text))


def test_unrelated_send_negation_does_not_cancel_the_read_state():
    current=request('Gmail에서 오늘 안 읽은 메일을 요약하고 발송하지 말고 파일로 저장해줘.')
    candidate=deepcopy(T02_RAW)
    candidate['tasks'][0]['actions']=['read','summarize','save']
    checked=validate_meaning(candidate,current)
    assert checked['tasks'][0]['selection']['filters']==['unread']
