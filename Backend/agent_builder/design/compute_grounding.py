"""Ground a requested new table column in the user's expression and column name."""
import ast
import re

from workflow_engine.table_formula import formula_columns


_COLUMN = r'(?:열|컬럼|칼럼|필드|column)'
_REQUEST = re.compile(
    r'(?:계산|파생|연산)\s*(?:된\s*)?' + _COLUMN
    + r'|(?:새(?:로운)?|결과)\s*' + _COLUMN + r'.{0,16}(?:만들|계산|추가|생성)'
    + r'|(?:곱한|나눈|더한|뺀)\s*값.{0,32}' + _COLUMN
    + r'|(?:곱할|나눌|더할|뺄)\s*(?:배율|비율|값|수).{0,60}' + _COLUMN + r'.{0,10}(?:만들|생성|추가)'
    + r'|(?:calculated|computed|derived)\s+column', re.I)
_CANCEL = re.compile(r'(?:계산|파생|연산)\s*' + _COLUMN
                     + r'\s*(?:추가|계산|생성)?\s*(?:은|는|을|를)?\s*(?:하지\s*말|취소|생략|만들지\s*말|추가하지\s*말)')
_CANCELLED = object()
_ARITHMETIC = re.compile(r'계산|산식|수식|곱|나눈|나눌|빼|뺀|더한|더할|배율|[+*/%×÷-]|(?:calculated|computed|derived)', re.I)
_IDENT = r'(?:[A-Za-z_][A-Za-z_0-9]*|[가-힣]+)'
_OPERAND = rf'(?:{_IDENT}|\d+(?:\.\d+)?)'
_FORMULA = re.compile(rf'(?<![A-Za-z_0-9])[(]*(?:{_OPERAND})'
                      rf'(?:[ )]*[-+*/%]\s*[(]*{_OPERAND})+[)]*')
_ASSIGN = re.compile(rf'(?<![\w])({_IDENT})\s*=(?!=)\s*')
_LABEL = re.compile(rf'(?<![\w])({_IDENT})(?:라는|란)\s*(?:새\s*)?{_COLUMN}'
                    rf'|(?<![\w])({_IDENT})\s+{_COLUMN}(?:로|에)\s*(?:계산|저장|넣|추가)'
                    rf'|{_COLUMN}\s*(?:이름|명)(?:은|는|을|으로)?\s*[:=]?\s*["“]?({_IDENT})', re.I)
QUESTIONS = {'compute.formula': '새 계산 열에는 어떤 수식을 적용할까요?',
             'compute.output_column': '새 계산 열의 결과 열 이름은 무엇인가요?'}


# A complete first-turn request with no formula, column name, second output
# or additional operation. A partial match cannot stand in for extraction.
_TABLE_COMPUTE_EXPORT = re.compile(
    r'\s*(?:(?:연결|첨부)(?:한|된)|이)\s*(?:표|테이블)(?:에|에서)\s*'
    r'(?:새(?:로운)?\s*)?(?:계산|파생|연산)\s*' + _COLUMN +
    r'(?:을|를)?\s*(?:만들고|추가하고|생성하고|만든\s*뒤|추가한\s*뒤)\s*'
    r'(?:결과(?:물)?(?:을|를)?\s*)?(csv|json)\s*(?:파일)?(?:으로|로)\s*'
    r'(?:저장|내보내)(?:해)?\s*(?:줘|주세요|세요)?[.!?\s]*', re.I)


def missing_compute_packet(state):
    """A complete source-backed partial ledger can ask before calling the model."""
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    assets = state.get('resources', [])
    if (len(users) != 1 or len(assets) != 1 or assets[0].get('kind') != 'table'
            or not assets[0].get('id') or state.get('base_agent_id') or state.get('required_node_refs')):
        return None
    request = _TABLE_COMPUTE_EXPORT.fullmatch(users[0])
    if not request:
        return None
    return {'tasks': [
        {'source_message': 0, 'description': '연결한 표 읽기',
         'spec': {'kind': 'read', 'file_ids': [assets[0]['id']]}},
        {'source_message': 0, 'description': '계산 결과 저장',
         'spec': {'kind': 'export', 'format': request[1].lower()}},
    ], 'unresolved': []}  # reconcile_compute emits the checked missing slots.


def _expression(text):
    try:
        formula_columns(text)
        return ast.dump(ast.parse(text, mode='eval').body)
    except (ValueError, TypeError, SyntaxError):
        return None


def _formula_literals(text, columns, *, complete_reply=False):
    text = text.replace('×', '*').replace('÷', '/')
    if complete_reply:
        text = re.sub(r'(?:해\s*(?:줘|주세요|요))?[.!?\s]*$', '', text.strip())

    def matches(pattern):
        if complete_reply:
            match = re.fullmatch(pattern, text)
            return [match] if match else []
        return re.finditer(pattern, text)

    found = []
    for match in matches(_FORMULA):
        value = match.group().strip()
        # Korean particles attach to the final operand, but never belong to a
        # known column. Latin identifiers and numeric operands have no suffix.
        for name in columns:
            value = re.sub(re.escape(name) + r'(?:을|를|으로|로)$', lambda _: name, value)
        if _expression(value):
            found.append(value)
    # In prose, adjacency and the operation verb bind BOTH operands; a number
    # in a separate sentence or a quantity-only reply is not a multiplier.
    words = {'곱': '*', '더': '+', '빼': '-', '뺀': '-', '나누': '/', '나눈': '/'}
    names = '|'.join(re.escape(name) for name in sorted(columns, key=len, reverse=True))
    if names:
        operand = rf'(?:{names}|[+-]?\d+(?:\.\d+)?)'
        phrasing = rf'(?<![\w])({operand})(?:에서|에|와|과|을|를)\s*({operand})(?:으로|로|을|를)?\s*(곱|더|빼|뺀|나누|나눈)'
        for match in matches(phrasing):
            found.append(f'{match[1]} {words[match[3]]} {match[2]}')
    return found


def _formula_answer_sources(state, formula):
    assets = state.get('resources', [])
    if len(assets) != 1 or assets[0].get('kind') != 'table':
        return set()
    answers = {(event.get('turn'), event.get('answer'))
               for channel in ('capability_answers', 'condition_answers', 'requirement_answers')
               for event in state.get(channel, []) if event.get('question_ids')}
    columns = assets[0].get('columns', [])
    users = [(turn, msg['content']) for turn, msg in enumerate(state['messages']) if msg['role'] == 'user']
    return {index for index, (turn, text) in enumerate(users) if (turn, text) in answers
            and any(_expression(f) == _expression(formula)
                    for f in _formula_literals(text, columns, complete_reply=True))}


def _named_column(text):
    text = text.replace('×', '*').replace('÷', '/')
    names = [m[1] for m in _ASSIGN.finditer(text) if _FORMULA.match(text, m.end())]
    for match in _LABEL.finditer(text):
        name = next(value for value in match.groups() if value)
        if not re.match(r'미정|아직|없|정하지|정해지지|무엇|어떤', name):
            names.append(name)
    return names


def _answer_keys(state, turn, text):
    # Older checkpoints wrote requirement responses in condition_answers.
    questions = {q.get('id'): q.get('key') for q in state.get('pending_questions', [])}
    for event in [*state.get('condition_answers', []), *state.get('requirement_answers', [])]:
        if event.get('turn') != turn or event.get('answer') != text:
            continue
        keys = set(event.get('candidate_keys', []))
        if any(q in questions and questions[q] not in keys for q in event.get('question_ids', [])):
            continue
        if len(keys) == 1:
            return keys
    return set()


def _inputs(state):
    from .explicit_table_criteria import explicit_criteria
    criteria = explicit_criteria(state)
    if criteria and 'calculations' in criteria:
        return None  # The explicit criteria validator already checks every calculation.
    columns = {column for resource in state.get('resources', []) if resource.get('kind') == 'table'
               for column in resource.get('columns', []) if isinstance(column, str) and column}
    source = None
    cancelled = False
    formulas, names = [], []
    user_index = -1
    for turn, message in enumerate(state.get('messages', [])):
        if message.get('role') != 'user':
            continue
        user_index += 1
        text = message['content']
        cancellation = _CANCEL.search(text)
        if cancellation:
            source, formulas, names = None, [], []
            cancelled = True
            text = text[cancellation.end():]
        new_request = bool(_REQUEST.search(text) and _ARITHMETIC.search(text))
        if new_request:
            source = user_index
            cancelled = False
        if source is None:
            continue
        values = _formula_literals(text, columns)
        labels = _named_column(text)
        keys = _answer_keys(state, turn, text)
        if keys == {'compute.formula'} and _expression(text.strip()):
            values = [text.strip()]
        if keys == {'compute.output_column'} and re.fullmatch(_IDENT, text.strip()):
            labels = [text.strip()]
        if (new_request and values and labels and formulas and names and set(labels).isdisjoint(names)
                and not re.search(r'바꾸|바꿔|변경|수정|대신|말고', text)):
            if len(values) != len(labels) or len(formulas) != len(names):
                raise ValueError('추가 계산 열과 기존 열의 수식·이름 대응을 확인하세요.')
            formulas.extend(values)
            names.extend(labels)
            continue
        if values:
            formulas = values
        if labels:
            names = labels
    if cancelled:
        return _CANCELLED
    return (source, formulas, names) if source is not None else None


def _missing(spec):
    return [dict(key=key, text=text) for key, text in QUESTIONS.items() if not spec[key.split('.')[1]]]


def _compute_question(q):
    if isinstance(q, dict):
        return q.get('key') in QUESTIONS
    return isinstance(q, str) and re.search(
        r'(?:수식|계산식|결과\s*(?:열|컬럼)\s*(?:의\s*)?(?:이름|명)|새\s*열\s*(?:의\s*)?이름)', q)


def reconcile_compute(raw, state):
    """Resolve a single new-column request before assigning dependency IDs."""
    if not isinstance(raw.get('unresolved'), list):
        raise ValueError('누락 조건 목록의 응답 형식이 잘못됐습니다.')
    evidence = _inputs(state)
    if not evidence:
        return raw
    if evidence is _CANCELLED:
        return {**raw, 'tasks':[t for t in raw['tasks'] if t['spec']['kind'] != 'compute'],
                'unresolved':[q for q in raw['unresolved'] if not _compute_question(q)]}
    source, formulas, names = evidence
    pending = list(raw['unresolved'])
    candidates = [item for item in raw['tasks'] if item['spec']['kind'] == 'compute']
    if len(formulas) > 1 or len(names) > 1:
        # Only verify distinct explicit pairs here. Never merge or guess their
        # relationships when there are multiple output columns.
        if (len(formulas) != len(names) or not candidates or
                sorted((_expression(item['spec'].get('formula')) or '', item['spec'].get('output_column') or '')
                       for item in candidates) != sorted((_expression(f), n) for f, n in zip(formulas, names))):
            raise ValueError('여러 계산 열의 수식과 결과 이름을 원문과 개별 대조해야 합니다.')
        return raw
    spec = dict(kind='compute', formula=next(iter(formulas), None), output_column=next(iter(names), None))
    questions = _missing(spec)
    # Replace equivalent model questions with the checked keys. Keep questions
    # about independent work, even when the compute candidate is discarded.
    pending = [q for q in pending if not _compute_question(q)]
    answers = set()
    if not questions and len(candidates) > 1:
        if any(_expression(item['spec'].get('formula')) != _expression(spec['formula'])
               or item['spec'].get('output_column') != spec['output_column'] for item in candidates):
            raise ValueError('여러 계산 업무 중 원문과 다른 수식 또는 결과 열이 있습니다.')
        answers = _formula_answer_sources(state, spec['formula'])
    tasks = []
    inserted = False
    for item in raw['tasks']:
        if item['spec']['kind'] == 'compute':
            if not questions:
                # An adjacent formula-only reply refines this one known column;
                # a different producer, output, table or additional instruction does not.
                if (item['source_message'] in answers and item['source_message'] > source and tasks
                        and tasks[-1]['source_message'] == source and tasks[-1]['spec'] == spec):
                    continue
                tasks.append({**item, 'spec':spec})
                inserted = True
        else:
            if not questions and not candidates and not inserted and item['spec']['kind'] == 'export':
                tasks.append({'source_message':source, 'description':'계산 열 추가', 'spec':spec})
                inserted = True
            tasks.append(item)
    if not questions and not inserted:
        tasks.append({'source_message':source, 'description':'계산 열 추가', 'spec':spec})
    return {**raw, 'tasks':tasks, 'unresolved':[*questions, *pending]}


def validate_compute_grounding(plan, state):
    """Recheck sealed plans including checkpoints without trusting a model digest."""
    evidence = _inputs(state)
    if not evidence:
        return
    specs = [t['spec'] for t in plan['tasks'] if t['spec']['kind'] == 'compute']
    if evidence is _CANCELLED:
        if specs or any(_compute_question(q) for q in plan['unresolved']):
            raise ValueError('취소한 계산 열이 업무나 질문에 남아 있습니다.')
        return
    _, formulas, names = evidence
    if formulas and names:
        expected = {(_expression(f), n) for f, n in zip(formulas, names)}
        if len(formulas) != len(names) or {(_expression(s['formula']), s['output_column']) for s in specs} != expected:
            raise ValueError('계산 열의 수식 또는 이름이 사용자 원문과 다릅니다.')
    else:
        missing = {q['key'] for q in _missing({'formula': formulas, 'output_column': names})}
        keys = {q['key'] for q in plan['unresolved'] if isinstance(q, dict)}
        if specs or not missing <= keys:
            raise ValueError('계산 열에 필요한 수식·결과 이름을 확인하세요.')
