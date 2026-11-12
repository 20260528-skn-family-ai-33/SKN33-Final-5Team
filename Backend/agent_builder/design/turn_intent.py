"""Separate side remarks from the work conversation before any checkpoint is resumed.

A pending clarification must only receive answers. A greeting or a one-off question
("오늘 날씨 어때?") is shown in the chat with a short notice, but it is never recorded as
an answer, task message, requirement or fingerprint input, and it never resumes the
interrupted graph. Mixed turns keep only the work part for the graph.

The decision is structural and conservative. A sentence is a side remark only when it is
a closed-class greeting, or an interrogative sentence that has no workflow cue and shares
no content word with the pending questions, the task so far or the attached files.
Everything else keeps the existing path (answer, change or new request), where the
assessment and validation still decide; the router never invents an answer.

A question *about* a pending question ("기준값이 뭐야?", "그게 무슨 뜻이야?") is not an answer
either (2026-11-12 beta feedback: it consumed the question and removed its choices). It is
recognised only when it is interrogative, refers to the pending question (shared content
word, or a closed set of "what does that mean" phrasings) and carries no answer content
(number, e-mail, the question's options, a column name, closed-set date/format words). It
keeps the question pending and gets an explanation built from the question record.
"""
import re

from agent_builder.design.file_timing import strip_quotes

_SENTENCE = re.compile(r'[^.!?？。\n]+[.!?？。]*')
# Phatic expressions only (a closed class, matched as a whole sentence).
_GREETINGS = {
    '안녕', '안녕하세요', '안녕하십니까', '반가워', '반가워요', '반갑습니다', '고마워', '고마워요', '감사해요',
    '감사합니다', '수고하세요', '수고하셨습니다', '좋은아침', '좋은아침이에요', 'hi', 'hello', 'hey', 'thanks', 'thankyou',
}
_INTERROGATIVE_END = re.compile(
    r'(?:[?？]|어때|어때요|뭐야|뭐예요|뭐에요|뭔가요|인가요|나요|니|냐|까요|ㄹ까|을까|죠|습니까|십니까|가요|대요|래요)\s*$')
# Workflow cues: requested operations, recurrence/schedules, files and outputs.
_WORK = re.compile(
    r'요약|정리|추출|분석|검토|집계|합계|계산|비교|대조|저장|변환|보내|발송|전송|만들|생성|작성|읽|찾|분류|필터|선별|'
    r'정렬|번역|알림|알려\s*줘|알려\s*주|예약|자동|워크플로우|에이전트|흐름|노드|도구|매일|매주|매월|매시간|마다|'
    r'파일|문서|자료|표|엑셀|csv|pdf|xlsx|메일|해\s*줘|해\s*주세요|해\s*줄래|부탁|하고\s*싶|원해', re.I)
_PARTICLES = ('으로', '에서', '부터', '까지', '처럼', '보다', '한테', '에게', '이랑', '하고',
              '은', '는', '이', '가', '을', '를', '로', '에', '의', '만', '도', '와', '과', '랑', '한', '인', '요')
_FUNCTION_WORDS = {'어떤', '어느', '무슨', '무엇', '뭐', '왜', '어떻게', '언제', '어디', '누구', '그럼', '그리고', '그런데',
                   '근데', '혹시', '그냥', '좀', '지금', '이거', '그거', '저거', '이', '그', '저', '것', '거', '수', '때'}


# "What does it mean?" phrasings that point at the question itself (closed set).
_ABOUT_QUESTION = re.compile(
    r'(?:무슨|어떤)\s*(?:뜻|의미|말|소리)|뭘\s*말하|뭐(?:를)?\s*말하는|무슨\s*얘기|예를\s*들(?:면|어)|예시|'
    r'(?:그게|그거|이게|이거|그건|이건|저게|저건)\s*(?:뭐|무슨|어떤|뭔)')
# The asking tail of a sentence ("…이 뭐야?", "…인가요?", "…아니야?"), removed before comparing content.
_TRAILING_ASK = re.compile(r'\s*(?:이|가|은|는)?\s*(?:뭐야|뭐예요|뭐에요|뭔데|뭔가요|뭐지|뭐임|무엇인가요|무엇이죠|맞아|맞나요|'
                           r'인가요|인지|일까|이야|야|예요|이에요|아니야|아닌가|거야|건가요|라는\s*거야)?\s*[?？]?\s*$')
# Closed-set values that answer common questions even without a number.
_ANSWER_WORDS = re.compile(
    r'[월화수목금토일]요일|오전|오후|정각|말일|csv|json|xlsx|엑셀|첫\s*행|마지막\s*행|'
    r'[\w.+-]+@[\w-]+\.[\w.]+|\d', re.I)


def _question_items(pending):
    """Pending questions as dicts: text, options and the declared answer control (if any)."""
    items = []
    for q in pending or ():
        if isinstance(q, dict):
            items.append({'text': q.get('text', ''), 'options': list(q.get('options') or []),
                          'response': q.get('response') or {}})
        elif str(q).strip():
            items.append({'text': str(q), 'options': [], 'response': {}})
    return items


def _answers_something(text, items, state):
    """The sentence carries content that could be an answer, so it is never treated as a mere question."""
    if _ANSWER_WORDS.search(text):
        return True
    folded = text.casefold()
    options = [o for item in items for o in item['options']] + \
              [o for item in items for o in (item['response'].get('options') or []) if isinstance(o, str)]
    if any(str(o).strip() and str(o).casefold() in folded for o in options):
        return True
    columns = [c for r in state.get('resources', []) or [] for c in (r.get('columns') or []) if isinstance(c, str)]
    words = set(re.findall(r'[가-힣A-Za-z0-9_]+', text))
    return any(c in words or (len(c) >= 2 and c in text) for c in columns)


def about_pending_question(sentence, state, pending):
    """True for a question about a pending question that carries no answer content."""
    items = _question_items(pending)
    text = strip_quotes(sentence).strip()
    if not items or not text or not _INTERROGATIVE_END.search(text.rstrip('.!~ ')):
        return False
    if _answers_something(text, items, state):
        return False
    question_stems = _stems(' '.join(item['text'] for item in items))
    # Words that only form the question ("뭐야", "무슨 뜻이야") are not content.
    content = _stems(_TRAILING_ASK.sub('', _ABOUT_QUESTION.sub(' ', text)))
    # Any new content word makes it an answer phrased as a question ("위약금 조항이 위험한 거 아니야?").
    if any(not _related({word}, question_stems) for word in content):
        return False
    return bool(_ABOUT_QUESTION.search(text)) or bool(content)


def _compact(text):
    return re.sub(r'[\s.!?？~。,]+', '', text).casefold()


def _stems(text):
    result = set()
    for word in re.findall(r'[가-힣A-Za-z0-9_]+', text):
        stem = word
        for particle in _PARTICLES:
            if stem.endswith(particle) and len(stem) > len(particle) + 1:
                stem = stem[:-len(particle)]
                break
        if len(stem) >= 2 and stem not in _FUNCTION_WORDS:
            result.add(stem.casefold())
    return result


def _context_stems(state, pending):
    parts = [m.get('content', '') for m in state.get('messages', []) if m.get('role') == 'user']
    parts += [q.get('text', '') if isinstance(q, dict) else str(q) for q in pending]
    parts += [o for q in pending if isinstance(q, dict) for o in q.get('options', [])]
    draft = state.get('draft') or {}
    parts += list(draft.get('questions', []) or []) + [draft.get('purpose', '') or '']
    for resource in state.get('resources', []) or []:
        parts += [resource.get('name', '')] + list(resource.get('columns', []) or [])
    return _stems(' '.join(parts))


def _related(sentence_stems, context):
    # Prefix match tolerates inflection ("위험으로"/"위험한" -> 위험).
    return any(s == c or (len(s) >= 2 and len(c) >= 2 and (s.startswith(c) or c.startswith(s)))
               for s in sentence_stems for c in context)


def classify_sentence(sentence, context):
    text = strip_quotes(sentence).strip()
    if not text:
        return 'empty'
    if _compact(text) in _GREETINGS:
        return 'greeting'
    if not _INTERROGATIVE_END.search(text.rstrip('.!~ ')):
        return 'work'
    if _WORK.search(text):
        return 'work'
    stems = _stems(text)
    if stems and _related(stems, context):
        return 'work'
    return 'side_question'


def split_turn(content, state, pending=()):
    """Return {'work': text for the graph ('' if none), 'side': [side sentences], 'kinds': [...]}."""
    context = _context_stems(state, pending)
    sentences = [s.strip() for s in _SENTENCE.findall(content) if s.strip()] or [content.strip()]
    work, side, kinds = [], [], []
    for sentence in sentences:
        items = _question_items(pending)
        if items and _answers_something(strip_quotes(sentence), items, state):
            kind = 'work'  # "20 이상이 맞아?": answer content while a question waits is an answer
        elif about_pending_question(sentence, state, pending):
            kind = 'about_question'
        else:
            kind = classify_sentence(sentence, context)
        kinds.append(kind)
        if kind in ('greeting', 'side_question', 'about_question'):
            side.append(sentence)
        elif kind == 'work':
            work.append(sentence)
    if 'about_question' in kinds and work:
        # "기준값이 뭐야? 100 이상으로 해줘": the answer goes on; the question about it needs no reply.
        return {'work': ' '.join(work).strip(), 'side': side, 'kinds': kinds}
    if not side or (work and 'side_question' not in kinds):
        # A greeting next to a real request is harmless context; keep the turn intact.
        return {'work': content.strip(), 'side': [], 'kinds': kinds}
    return {'work': ' '.join(work).strip(), 'side': side, 'kinds': kinds}


def _explain(item):
    """What a pending question asks for, from its own record only (no example value is invented)."""
    response = item['response'] or {}
    kind = response.get('type')
    options = [str(o) for o in (item['options'] or response.get('options') or []) if str(o).strip()]
    if kind == 'time':
        how = '09:00처럼 시각으로 답해 주세요.'
    elif kind == 'number':
        unit = response.get('unit')
        bounds = [f"{response['minimum']} 이상" if response.get('minimum') is not None else '',
                  f"{response['maximum']} 이하" if response.get('maximum') is not None else '']
        bounds = ', '.join(b for b in bounds if b)
        how = '숫자로 답해 주세요' + (f'(단위: {unit})' if unit else '') + (f'. 입력 범위는 {bounds}예요.' if bounds else '.')
    elif options:
        how = ('여러 개를 골라도 돼요: ' if kind == 'multi_choice' else '다음 중에서 골라 주세요: ') + ', '.join(options) + '.'
    else:
        how = '정해 주시는 내용을 그대로 반영할게요. 아직 모르시면 "나중에 정할게"라고 답해 주세요.'
    return f'「{item["text"]}」는 이 업무를 만들려면 정해야 하는데 요청에 아직 없는 내용을 묻는 질문이에요. {how}'


def side_reply(turn, pending_texts, pending=None, failure=False):
    """Fixed notice for side remarks; no model call and no stored answer."""
    lines = []
    if 'about_question' in turn['kinds']:
        if turn['work']:
            return ''
        items = _question_items(pending if pending is not None else pending_texts)
        side = ' '.join(turn['side'])
        stems = _stems(side)
        chosen = [i for i in items if _related(stems, _stems(i['text']))] or items
        lines += [_explain(item) for item in chosen]
        lines.append('진행 중인 질문은 그대로 남아 있어요.')
        return '\n'.join(lines)
    if failure and 'side_question' in turn['kinds'] and not turn['work']:
        return ('앞의 요청은 업무 흐름을 만드는 중 처리 오류로 멈춘 상태예요. 입력하신 조건은 보존돼 있어요. '
                '"조건 그대로 다시 시도"를 누르거나, 요청을 작업 단위로 나눠 다시 보내 주세요.')
    items = _question_items(pending if pending is not None else pending_texts)
    if items and 'side_question' in turn['kinds'] and not turn['work']:
        # While a question waits, an unrelated-looking question still gets the question explained
        # rather than a notice about real-time information ("이상이 뭐야?").
        return '\n'.join(['이 질문은 업무 설정이나 답변으로 기록하지 않았어요.'] + [_explain(i) for i in items]
                         + ['진행 중인 질문은 그대로 남아 있어요.'])
    if 'side_question' in turn['kinds']:
        lines.append('이 대화는 업무 워크플로우를 설계합니다. 실시간 정보 같은 일회성 질문에는 답하지 않으며, '
                     '이 질문은 진행 중인 설정이나 답변으로 기록하지 않았습니다. 반복 실행할 업무라면 "매일 ..."처럼 '
                     '자동화할 내용을 알려주세요.')
    elif not turn['work']:
        lines.append('안녕하세요. 자동화할 업무를 알려주세요.')
    if pending_texts and not turn['work']:
        lines.append('진행 중인 확인 질문에는 아직 답변하지 않았습니다:\n' + '\n'.join('• ' + t for t in pending_texts))
    return '\n'.join(lines)
