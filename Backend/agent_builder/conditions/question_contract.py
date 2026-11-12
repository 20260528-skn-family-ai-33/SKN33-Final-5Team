"""Check a proposed clarification question against what the user already settled.

Shared by the capability assessment and both planners (DeepAgent/Direct) and the final
validation, so every path drops the same redundant questions before they are shown or
registered as pending. The check reads the question's structure (which target, format
or comparison it asks to choose) and the user's own words; it never keys on domain words.

A question is settled only by user evidence:
- "어떤/어느/무슨 <대상>" is settled when the user named that target with a concrete
  specifier ("해지 조항", "모든 조항", "위약금이 있는 조항"). An evaluative adjective
  ("위험한 조항", "중요한 항목") is a vague criterion and keeps the question.
- A format question is settled by an explicit output format ("CSV로 저장").
- A comparison question (초과/이상/미만/이하) is settled by one explicit comparator.
- The same question already answered by the user is not asked again.
"""
import re

from agent_builder.design.file_timing import strip_quotes

_WHICH = re.compile(r'(?:어떤|어느|무슨)\s+([^?？.!,]+)')
_PARTICLES = ('으로', '에서', '부터', '까지', '처럼', '보다', '은', '는', '이', '가', '을', '를', '로', '에', '의', '만', '도', '와', '과')
_DEMONSTRATIVE = {'이', '그', '저', '해당', '어떤', '어느', '무슨', '이런', '그런', '저런', '같은', '다른', '위의', '아래'}
_QUANTIFIER = {'모든', '전체', '전부', '각', '모두'}
_ADNOMINAL = re.compile(r'(?:한|운|은|는|된|던|인|적인|할|될|일|ㄴ)$')
_ARGUMENT = re.compile(r'(?:이|가|을|를|에|과|와|로|으로|만|도)$')
_FORMAT_Q = re.compile(r'형식|포맷|확장자|파일\s*형태|어떤\s*파일로')
_FORMAT_A = re.compile(r'(?<![A-Za-z])(CSV|XLSX|JSON|PDF|TXT|DOCX|HWP|엑셀|마크다운|markdown)(?![A-Za-z])\s*'
                       r'(?:파일|문서|형식|포맷)?\s*(?:으로|로|형식)', re.I)
_OUTPUT_FORMAT_ACTION = re.compile(r'(?:저장|출력|내보내|정리|만들|작성|생성|제공|전달|보내|다운로드|변환)(?!된|되어|한|어진)')
_OUTPUT_FORMAT_OBJECT = re.compile(r'결과|출력|저장|보고서|산출물')
_INPUT_FORMAT_ACTION = re.compile(r'받은|받아|읽|불러|업로드|올린|첨부|가져|입력|원본|'
                                  r'(?:되어\s*있는|된|처리할)\s*(?:자료|파일|문서|데이터|표)')
_INPUT_FORMAT_QUESTION = re.compile(r'입력|원본|업로드|첨부|읽을|가져올|수신')
_FORMAT_BOILERPLATE = {'결과', '출력', '저장', '파일', '문서', '산출물', '어떤', '어느', '무슨', '무엇',
                       '최종', '생성할', '만들', '저장할', '중', '또는', '혹은',
                       'CSV', 'XLSX', 'JSON', 'PDF', 'TXT', 'DOCX', 'HWP', '엑셀', '마크다운'}
_COMPARATORS = ('초과', '이상', '미만', '이하')
_EXPLICIT_COMPARISON = re.compile(r'\d[\d,.]*\s*(?:천만|백만|십만|만|천|백|억)?\s*(?:원|점|개|명|건|kg|%|일|시간)?\s*'
                                  r'(?:을|를|이|가)?\s*(초과|이상|미만|이하)')
_COMPARISON_BOILERPLATE = {'비교', '방식', '기준', '조건', '선택', '어떤', '어느', '무슨', '무엇', '중', '여부', '포함'}


class SettledQuestionError(ValueError):
    """A planner response that only re-asks conditions the user already settled."""


def _stem(word):
    for particle in _PARTICLES:
        if word.endswith(particle) and len(word) > len(particle):
            return word[:-len(particle)]
    return word


def _noun_phrase(rest):
    """Words after 어떤 up to the first word carrying a particle: "파일 처리 규칙을" -> 파일 처리 규칙."""
    words = []
    for word in rest.split()[:3]:
        words.append(_stem(word))
        if _stem(word) != word:
            return ' '.join(words)
    return None  # no particle: the phrase boundary is unknown, keep the question


def _user_texts(state):
    return [strip_quotes(m.get('content', '')) for m in state.get('messages', []) if m.get('role') == 'user']


def _target_settled(target, texts):
    words = target.split()
    stems = [_stem(w) for w in words]
    head = stems[-1]
    if len(head) < 1 or head in ('것', '거', '방식', '방법', '내용', '기준') and len(stems) == 1:
        # "어떤 기준/방식" asks for a criterion itself, which only a concrete answer settles.
        return None
    pattern = re.compile(r'(\S+)\s+' + r'\s*'.join(map(re.escape, stems)) + r'(?:' + '|'.join(_PARTICLES) + r')?(?=\s|[.,!?]|$)')
    for text in texts:
        tokens = text.split()
        for match in pattern.finditer(text):
            modifier = match.group(1)
            if modifier in _DEMONSTRATIVE:
                continue
            if modifier in _QUANTIFIER:
                return match.group(0)
            if _ADNOMINAL.search(modifier):
                # A relative clause with its own argument ("위약금이 있는") is concrete;
                # a bare evaluative adjective ("위험한") is a vague criterion.
                before = text[:match.start()].split()
                if before and _ARGUMENT.search(before[-1]) and not _ADNOMINAL.search(before[-1]):
                    return match.group(0)
                continue
            if _stem(modifier) != modifier:
                continue  # "영수증에서 항목", "계약서를 조항": a particle marks another argument, not a specifier
            return match.group(0)
        del tokens
    return None


def _named_fields_present(fields, context):
    return all(re.search(r'(?<![가-힣A-Za-z])' + re.escape(field) +
                         r'(?:은|는|이|가|을|를|에|의|도|만)?(?=\s|$)', context) for field in fields)


def _format_targets(question):
    head = question[:_FORMAT_Q.search(question).start()]
    words = [_stem(word) for word in re.findall(r'[가-힣A-Za-z_]+', head)]
    return [word for word in words if word not in _FORMAT_BOILERPLATE and len(word) > 1]


def _output_format_quote(user, targets):
    """Find a format used for the result, not the format of material being read."""
    for found in _FORMAT_A.finditer(user):
        before = _comparison_context(user, found.start())[-32:]
        after = re.split(r'[,.;!?\n]', user[found.end():], maxsplit=1)[0][:40]
        action = _OUTPUT_FORMAT_ACTION.search(after)
        context = before + ' ' + (after[:action.start()] if action else after)
        if targets and not _named_fields_present(targets, context):
            continue
        if action and not _INPUT_FORMAT_ACTION.search(after[:action.start()]) and not _FORMAT_A.search(after[:action.start()]):
            return found.group(0)
        if not action and _OUTPUT_FORMAT_OBJECT.search(before) and not _INPUT_FORMAT_ACTION.search(after):
            return found.group(0)
    return None


def _comparison_fields(question):
    """Named fields before a comparator; generic comparison wording is ignored."""
    head = re.split(r'초과|이상|미만|이하|비교\s*방식|포함할까', question, maxsplit=1)[0]
    head = re.sub(r'\d[\d,.]*\s*(?:천만|백만|십만|만|천|백|억)?', ' ', head)
    words = [_stem(word) for word in re.findall(r'[가-힣A-Za-z_]+', head)]
    return [word for word in words if word not in _COMPARISON_BOILERPLATE and len(word) > 1]


def _comparison_context(user, position):
    prefix = user[:position]
    return re.split(r'[,.;!?\n]|그리고|하지만|반면|또한|하고|이며|이고', prefix)[-1]


def settled_by(question, state):
    """Return the user quote that already settles this question, or None."""
    text = (question or '').strip()
    if not text:
        return None
    texts = _user_texts(state)
    if not texts:
        return None
    if _FORMAT_Q.search(text) and not (_INPUT_FORMAT_QUESTION.search(text) and
                                       not _OUTPUT_FORMAT_OBJECT.search(text)):
        targets = _format_targets(text)
        for user in texts:
            quote = _output_format_quote(user, targets)
            if quote:
                return quote
    asked = [c for c in _COMPARATORS if c in text]
    if len(asked) >= 2 or '비교 방식' in text or '포함할까' in text:
        fields = _comparison_fields(text)
        found = [m for user in texts for m in _EXPLICIT_COMPARISON.finditer(user)
                 if _named_fields_present(fields, _comparison_context(user, m.start()))]
        if len({m.group(1) for m in found}) == 1:
            return found[0].group(0)
    for match in _WHICH.finditer(text):
        target = _noun_phrase(match.group(1))
        quote = target and _target_settled(target, texts)
        if quote:
            return quote
    return None


# Relation predicates (a closed class): "위약금 조항이 있는 부분" names its items by a fact anyone
# can check. Evaluative predicates ("재고가 부족한 품목", "위험한 조항") need a threshold or rule.
_RELATION_PREDICATE = re.compile(
    r'^(?:있는|없는|있던|없던|포함된|포함한|포함되는|들어간|들어있는|관련된|관련한|관련되는|해당하는|해당되는|'
    r'명시된|적힌|적혀\s*있는|언급된|기재된|표시된|붙은|달린|'
    # the same relations as a condition: "위약금이 있으면 금액을 뽑아서" checks a fact, it does not judge
    r'있으면|없으면|있을\s*때|없을\s*때|포함되면|포함되어\s*있으면|들어가\s*있으면|해당하면|해당되면|명시되어\s*있으면)$')


# Adnominal (관형형) endings inside a selected noun phrase. "한" needs a 2-syllable root
# (위험한, 부족한) so short nouns such as 권한·기한 are not read as modifiers.
_ADNOMINAL_FORM = re.compile(r'(?:..한|운|된|던|은|는|큰|센|긴|낮은|적인)$')


def _adnominal(word):
    # Inside an extracted selection phrase a word ending in 은/는 is an adnominal form
    # (보이는, 많은); topic-marked nouns do not occur between a modifier and its head.
    # A conditional that is not a relation ("위험하면", "크면") is an evaluation as well.
    return bool(_ADNOMINAL_FORM.search(word)) or bool(re.search(r'(?:으면|면)$', word))


def concrete_specifier(phrase):
    """Whether a selected phrase already names its items concretely.

    True for a noun specifier ("개인정보 관련 조항", "해지 조항", "모든 조항") or a relation
    clause with its own argument ("위약금 조항이 있는 부분"). False when any modifier is an
    evaluative form ("위험한 조항", "재고가 부족한 품목", "위험해 보이는 계약 조항", "금액이 큰 행").
    """
    words = [w for w in re.split(r'\s+', (phrase or '').strip()) if w]
    if len(words) < 2:
        return False
    modifiers = words[:-1]
    if any(m in _DEMONSTRATIVE for m in modifiers):
        return False
    relation = False
    for index, modifier in enumerate(modifiers):
        if _RELATION_PREDICATE.match(modifier):
            relation = relation or (index > 0 and bool(_ARGUMENT.search(modifiers[index - 1])))
        elif _adnominal(modifier):
            return False  # an evaluative modifier needs a threshold or rule
    if relation or any(m in _QUANTIFIER for m in modifiers):
        return True
    nearest = modifiers[-1]
    return _stem(nearest) == nearest and not _RELATION_PREDICATE.match(nearest)


def _normalized(text):
    return re.sub(r'[\s.?？!]+', '', text or '')


def answered_question(question, state):
    """The same question was already answered with a real answer in this conversation."""
    asked = {q['id']: q.get('text', '') for q in state.get('capability_questions', []) or []}
    asked.update({q['id']: q.get('text', '') for q in state.get('pending_questions', []) or [] if 'id' in q})
    asked.update({q['id']: q.get('text', '') for event in state.get('clarification_answers', []) or []
                  for q in event.get('questions', []) if 'id' in q})
    answered = {i for e in state.get('capability_answers', []) or [] if e.get('kind') == 'answer'
                for i in e.get('question_ids', [])}
    answered |= {i for e in state.get('condition_answers', []) or [] for i in e.get('question_ids', [])}
    answered |= {i for e in state.get('clarification_answers', []) or [] if e.get('kind') == 'answer' and e.get('explicit')
                 for i in e.get('question_ids', [])}
    target = _normalized(question)
    if any(_normalized(asked[i]) == target for i in answered if i in asked):
        return True
    # Older checkpoints recorded selected requirement controls only in requirement_answers.
    # Free-text events also contain question snapshots, but have no structured value.
    from agent_builder.conditions.input_resolution import answer_kind
    for event in state.get('requirement_answers', []) or []:
        if event.get('skipped') or 'value' not in event or event['value'] is None:
            continue
        value = event['value']
        text = ', '.join(map(str, value)) if isinstance(value, list) else str(value)
        if answer_kind(text) == 'answer' and any(_normalized(q.get('text', '')) == target
                                                 for q in event.get('questions', [])):
            return True
    return False


def settled_questions(questions, state):
    """[(question, reason)] for proposed questions the user already settled."""
    result = []
    # Design-time file requests are screened only at the capability stage (clarification_core.screen),
    # whose contract says files are not required now. A planner may still ask to connect a file.
    for question in questions or []:
        quote = settled_by(question, state)
        if quote:
            result.append((question, f'사용자가 이미 지정함: "{quote}"'))
        elif answered_question(question, state):
            result.append((question, '사용자가 이미 답변한 질문'))
    return result


def settled_question_issue(questions, state):
    found = settled_questions(questions, state)
    if not found:
        return None
    detail = ' / '.join(f'{q} ({reason})' for q, reason in found)
    return ('이미 사용자가 정한 조건을 다시 묻는 질문입니다. 이 질문을 빼고 사용자 원문 조건으로 설계하거나, '
            '정말 미정인 다른 조건만 질문하세요: ' + detail)


def drop_settled(draft, state):
    """Remove settled questions and their aligned controls. Returns (draft, removed)."""
    found = {q for q, _ in settled_questions(draft.get('questions', []), state)}
    if not found:
        return draft, []
    questions = list(draft.get('questions', []))
    options = list(draft.get('question_options', []) or [])
    responses = list(draft.get('question_responses', []) or [])
    keep = [index for index, question in enumerate(questions) if question not in found]
    result = {**draft, 'questions': [questions[index] for index in keep],
              'question_options': [options[index] for index in keep if index < len(options)]}
    if 'question_responses' in draft:
        result['question_responses'] = [responses[index] for index in keep if index < len(responses)]
    return result, sorted(found)

