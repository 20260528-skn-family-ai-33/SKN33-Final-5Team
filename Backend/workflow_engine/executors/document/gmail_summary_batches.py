"""Bound Gmail context without selecting or discarding any collected message."""
MAX_BATCH_CHARS = 12000
MAX_BATCH_DOCUMENTS = 10


def gmail_collection_notices(result):
    """Keep source boundaries and omissions visible even inside a reused graph."""
    notices = []
    period = result.get('date_range')
    if period:
        notices.append(f"조회 범위: {period['start'][:10]} 하루 ({period['timezone']}). {result['returned_count']}건을 읽었습니다.")
    if result.get('nextPageToken'):
        notices.append(f"검색 결과가 더 있어 이번 요약에는 최대 {result['requested_count']}건만 포함했습니다. 나머지 메일은 요약하지 않았습니다.")
    if result.get('truncated_message_ids'):
        notices.append(f"긴 메일 {len(result['truncated_message_ids'])}건은 본문 앞 {result.get('body_character_limit', 8000):,}자까지만 읽었습니다. 생략된 부분은 요약에 포함하지 않았습니다.")
    if result.get('body_unavailable_message_ids'):
        notices.append(f"메일 {len(result['body_unavailable_message_ids'])}건은 읽을 수 있는 텍스트 본문이 없어 제목·보낸 사람만 확인했습니다.")
    return notices


def append_collection_notices(text, results):
    for result in results:
        for notice in gmail_collection_notices(result):
            if notice not in text:
                text += '\n\n' + notice
    return text


def document_batches(documents, max_chars=MAX_BATCH_CHARS, max_documents=MAX_BATCH_DOCUMENTS):
    if type(max_chars) is not int or max_chars < 1 or type(max_documents) is not int or max_documents < 1:
        raise ValueError('메일 요약 묶음의 크기가 올바르지 않습니다.')
    batch, size = [], 0
    for document in documents:
        text = document['text']
        parts = max(1, (len(text) + max_chars - 1) // max_chars)
        for index in range(parts):
            start = index * max_chars
            part = text[start:start + max_chars]
            if batch and (len(batch) >= max_documents or size + len(part) > max_chars):
                yield batch
                batch, size = [], 0
            piece = {**document, 'text': part}
            if parts > 1:
                piece.update(offset=document.get('offset', 0) + start,
                             summary_part=index + 1, summary_parts=parts)
            batch.append(piece)
            size += len(part)
    if batch:
        yield batch


def summarize_all(documents, compose):
    """Compose every batch and join all outputs; a failed batch fails the summary."""
    batches = list(document_batches(documents))
    if not batches:
        raise ValueError('요약할 메일 본문이 없습니다.')
    if len(batches) == 1:
        return compose(batches[0], 1, 1)
    texts, evidence, seen = [], [], set()
    for index, batch in enumerate(batches, 1):
        result = compose(batch, index, len(batches))
        if not isinstance(result.get('text'), str) or not result['text'].strip():
            raise ValueError('메일 요약 묶음의 결과가 비어 있습니다. 일부 결과만 완료 처리하지 않습니다.')
        texts.append(f"### 메일 요약 {index}/{len(batches)}\n{result['text']}")
        for item in result.get('evidence', []):
            key = (item.get('source_id'), item.get('page'), item.get('quote'))
            if key not in seen:
                evidence.append(item)
                seen.add(key)
    count = len({document['source_id'] for document in documents if document.get('parser_engine') == 'gmail'})
    return {'text': f'조회된 메일 {count}건을 {len(batches)}개 묶음으로 나누어 요약했습니다.\n\n' + '\n\n'.join(texts),
            'evidence': evidence}
