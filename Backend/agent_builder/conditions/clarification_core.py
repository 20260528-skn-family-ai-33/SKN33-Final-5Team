"""Clarification core: one place for question records, answers and system failures.

Every gate (attachment, capability, conditions, requirements, planners) may *propose*
questions. Only this module turns proposals into pending question records, so the same
checks apply everywhere:

- a question the user already settled is not registered (question_contract);
- a design-time request for a file is not a capability/requirement question, because
  files may be connected at execution time (the upload precheck owns "attach now");
- a stage that needs a user decision registers questions and waits; it never ends the
  run as a failure. A model or contract failure is recorded separately as a retryable
  ``system_failure`` and never becomes a question.

Records keep the existing ``pending_questions`` shape ({id, key, source, text, options})
so the UI and older checkpoints work unchanged; extra fields are additive.
"""
import hashlib
import re

from agent_builder.conditions.question_contract import settled_questions

# Sources whose records await_input must wait for (condition records keep their own flag).
CORE_SOURCES = ('requirement', 'requirements', 'orchestration')  # main uses 'requirements'

_FILE_NOUN = r'(?:파일|문서|자료|원문|PDF|CSV|XLSX|엑셀|이미지|사진|텍스트\s*데이터|데이터\s*파일|문서\s*ID|파일\s*ID|ID)'
_FILE_DELIVERY = r'(?:연결|제공|업로드|첨부|올려|올리|입력|전달|공유|필요)'
_DESIGN_FILE_REQUEST = re.compile(_FILE_NOUN + r'[^.?!]{0,12}?' + _FILE_DELIVERY, re.I)
# Questions about what to do *with* the file stay valid: "CSV에서 집계할 열", "문서에서 추출할 항목".
_FILE_CONTENT_QUESTION = re.compile(r'(?:열|컬럼|항목|필드|기준|조건|조항|범위|형식|이름)\s*(?:을|를|은|는|이|가)?', re.I)


# What a question asks for is its head noun once the asking phrase is removed:
# "계약서 파일 ID 또는 경로에 대해 알려주세요" asks for 경로 (a file locator).
_ASK_TAIL = re.compile(r'(?:\s*(?:에\s*대해\s*알려\s*주세요|(?:을|를)\s*알려\s*주세요|(?:이|가)?\s*필요(?:합니다)?|[?？.!]))+\s*$')
# The input file itself or its app-assigned locator. File IDs are assigned by A4B at upload,
# so a design-time question asking the user for one is never a business condition.
_INPUT_HEAD = re.compile(r'(?:파일|자료|문서|원문|PDF|CSV|XLSX|엑셀|이미지|사진|ID|아이디|경로|path)$', re.I)
_OUTPUT_SIDE = re.compile(r'결과|출력|저장할|만들|생성할|내보낼')


def design_time_file_request(text):
    """A capability/requirement/planner question that only asks for the input file or its locator."""
    text = text or ''
    if _DESIGN_FILE_REQUEST.search(text) and not _FILE_CONTENT_QUESTION.search(text):
        return True
    phrase = _ASK_TAIL.sub('', text).strip()
    words = phrase.split()
    if not words:
        return False
    head = re.sub(r'(?:을|를|은|는|이|가|의|에)$', '', words[-1])
    return bool(_INPUT_HEAD.search(head)) and not _FILE_CONTENT_QUESTION.search(phrase) and not _OUTPUT_SIDE.search(phrase)


# A sentence that already asks the user ("…알려주세요.", "…올려 주세요", "…인가요") is kept as is.
_ALREADY_ASKS = re.compile(r'(?:주세요|주십시오|바랍니다|인가요|일까요|할까요|될까요|나요|까요)\s*[.!。]?$')


def statement_question(text):
    """Turn a missing-item statement ("선별 기준 금액이 미정입니다") into a question."""
    text = (text or '').strip()
    if not text:
        return text
    if text.endswith(('?', '？')) or _ALREADY_ASKS.search(text):
        return text
    stem = re.sub(r'\s*(?:이|가|은|는)?\s*(?:아직\s*)?(?:미정|미확정|정해지지\s*않았|확인되지\s*않았|불명확|필요)'
                  r'(?:입니다|이에요|예요|합니다|함|임|습니다)?\.?$', '', text).strip()
    if not stem or stem.rstrip('.') == text.rstrip('.'):
        return text.rstrip('.') + '. 이 부분을 알려주세요.'
    last = stem[-1]
    particle = '을' if (0xAC00 <= ord(last) <= 0xD7A3 and (ord(last) - 0xAC00) % 28) else '를'
    return f'{stem}{particle} 알려주세요.'


def question_record(source, key, text, *, options=None, target=None, required=True, scope=''):
    """Stable ID: the same question in the same scope keeps its ID across turns and reloads."""
    digest = hashlib.sha256(f'{source}:{scope}:{key}:{text}'.encode()).hexdigest()[:24]
    return {'id': digest, 'key': key, 'source': source, 'text': text, 'options': list(options or []),
            'target': target, 'required': required, 'status': 'open'}


def screen(proposals, state, *, file_questions_allowed=False):
    """Split proposed question texts into (kept, dropped[(text, reason)])."""
    settled = dict(settled_questions(proposals, state))
    kept, dropped = [], []
    for text in proposals:
        if text in settled:
            dropped.append((text, settled[text]))
        elif not file_questions_allowed and design_time_file_request(text):
            dropped.append((text, '설계 단계에는 파일이 필요하지 않습니다(실행할 때 연결 가능)'))
        else:
            kept.append(text)
    return kept, dropped


def register(state, proposals, *, source, scope='', file_questions_allowed=False):
    """Screen proposals and return (records, dropped). Records are deduplicated by text.

    file_questions_allowed keeps design-time file requests; only the capability stage drops them.
    """
    kept, dropped = screen(list(dict.fromkeys(p for p in proposals if p and p.strip())), state,
                           file_questions_allowed=file_questions_allowed)
    records = [question_record(source, f'{source}.{_slug(text)}', text, scope=scope) for text in kept]
    return records, dropped


def _slug(text):
    return hashlib.sha256(re.sub(r'\s+', '', text).encode()).hexdigest()[:10]


def awaited(state):
    """Pending records await_input waits for."""
    return [q for q in state.get('pending_questions', []) or []
            if state.get('condition_enforced') or q.get('source') in ('receipt',) + CORE_SOURCES]


def record_answer(state, answer, question_id=None, question_answers=None):
    """One answer event for core records; condition/capability events keep their own lists."""
    from agent_builder.conditions.input_resolution import answer_kind
    records = [q for q in state.get('pending_questions', []) or [] if q.get('source') in CORE_SOURCES]
    if not records:
        return None
    if question_answers is not None:
        from agent_builder.design.question_answers import answer_text
        ids = [item['question_id'] for item in question_answers
               if not item.get('skip') and answer_kind(answer_text(item['value'])) == 'answer']
    else:
        ids = [question_id] if question_id else [q['id'] for q in records]
    selected = [q for q in records if q['id'] in ids]
    if question_answers is not None and not selected:
        return None
    # explicit: the user chose this question (question_id). A free message may be a new request
    # rather than an answer, so only explicit answers may later close a planner's repeated question.
    return {'turn': len(state.get('messages', [])), 'answer': answer,
            'kind': 'answer' if question_answers is not None else answer_kind(answer),
            'questions': selected, 'question_ids': [q['id'] for q in selected],
            'explicit': bool(question_id) or question_answers is not None}


def open_required(state):
    """Required questions still waiting for an answer; confirm/save must refuse while any exist."""
    return [q for q in state.get('pending_questions', []) or [] if q.get('required', True)]


def system_failure(stage, error, message):
    """A retryable processing failure. It is not a user decision and never a question."""
    return {'stage': stage, 'error_type': type(error).__name__ if isinstance(error, BaseException) else str(error),
            'message': message, 'retryable': True}


RETRY_NOTICE = ' 일시적인 처리 오류라 질문으로 바꾸지 않았습니다. 같은 메시지를 다시 보내면 이 단계부터 다시 시도합니다.'

