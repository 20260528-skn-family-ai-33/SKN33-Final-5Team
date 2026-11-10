"""Every collected Gmail body reaches a model batch, including long single mails."""
from collections import defaultdict

import pytest

from workflow_engine.executors.document.gmail_summary_batches import (
    append_collection_notices, document_batches, gmail_collection_notices, summarize_all,
)


def documents(count=100, length=350):
    return [dict(source_id='gmail:' + str(index), parser_engine='gmail', offset=0,
                 text=(f'메일 {index}의 본문\n' + '가나다라마' * length)) for index in range(count)]


def test_all_500_message_bodies_are_covered_without_context_selection_or_mutation():
    docs = documents(500)
    original = [dict(document) for document in docs]
    batches = list(document_batches(docs))
    assert len(batches) > 1
    recovered = defaultdict(str)
    for batch in batches:
        assert len(batch) <= 10
        assert sum(len(document['text']) for document in batch) <= 12000
        for document in batch:
            recovered[document['source_id']] += document['text']
    assert dict(recovered) == {document['source_id']: document['text'] for document in docs}
    assert docs == original


def test_long_single_mail_is_split_at_every_character_without_a_hidden_tail_limit():
    doc = documents(1, 15000)[0]
    pieces = [piece for batch in document_batches([doc]) for piece in batch]
    assert len(pieces) > 1
    assert ''.join(piece['text'] for piece in pieces) == doc['text']
    assert [piece['offset'] for piece in pieces] == [index * 12000 for index in range(len(pieces))]
    assert all(piece['summary_parts'] == len(pieces) for piece in pieces)


def test_batch_outputs_are_all_preserved_and_no_final_model_call_drops_mail_summaries():
    docs = documents(100)
    calls = []
    def compose(batch, index, total):
        calls.append((batch, index, total))
        return {'text': '|'.join(document['source_id'] for document in batch),
                'evidence': [dict(source_id=document['source_id'], page=None, quote=document['text'][:10]) for document in batch]}
    result = summarize_all(docs, compose)
    assert len(calls) > 1
    assert {evidence['source_id'] for evidence in result['evidence']} == {document['source_id'] for document in docs}
    assert all(document['source_id'] in result['text'] for document in docs)
    assert '메일 100건' in result['text']
    assert [index for _, index, _ in calls] == list(range(1, len(calls) + 1))
    assert all(total == len(calls) for _, _, total in calls)


def test_a_failed_batch_does_not_return_a_partial_success():
    calls = []
    def compose(batch, index, total):
        calls.append(index)
        if index == 2:
            raise RuntimeError('model unavailable')
        return {'text': 'one batch', 'evidence': []}
    with pytest.raises(RuntimeError, match='model unavailable'):
        summarize_all(documents(100), compose)
    assert calls == [1, 2]


def test_small_collection_keeps_the_original_single_grounded_answer():
    docs = documents(2, 2)
    expected = {'text': 'two mail summaries', 'evidence': []}
    def compose(batch, index, total):
        assert index == total == 1 and batch == docs
        return expected
    assert summarize_all(docs, compose) is expected


def test_reused_summary_text_keeps_period_and_all_omission_notices_once():
    collection = dict(date_range=dict(start='2026-11-09T00:00:00+09:00', timezone='Asia/Seoul'),
                      returned_count=100, requested_count=100, nextPageToken='another-page',
                      truncated_message_ids=['long'], body_character_limit=8000,
                      body_unavailable_message_ids=['empty'])
    notices = gmail_collection_notices(collection)
    assert len(notices) == 4
    text = append_collection_notices('전날 메일 요약입니다.', [collection])
    assert all(notice in text for notice in notices)
    assert '2026-11-09' in text and '최대 100건' in text and '8,000자' in text
    assert append_collection_notices(text, [collection]) == text


def test_empty_collection_still_exposes_its_fixed_day_and_zero_count():
    collection = dict(date_range=dict(start='2026-11-09T00:00:00+09:00', timezone='Asia/Seoul'), returned_count=0)
    text = append_collection_notices('검색 조건에 맞는 메일이 없습니다.', [collection])
    assert '2026-11-09' in text and '0건' in text
    assert '나머지 메일' not in text
