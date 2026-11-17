"""User-facing node edit copy; persisted requests and proofs remain untouched."""
import json
import math
import re


# Keep these labels aligned with Frontend/app/node-setting-copy.ts.
FIELD_LABELS = {
    'max_results': '조회 한도', 'limit': '한도', 'count': '건수', 'threshold': '기준값',
    'query': '검색 조건', 'channel_id': '채널 ID', 'group_by': '묶을 열',
    'value_column': '계산할 열', 'sum_field': '합계 열', 'field': '열 이름',
    'compare_field': '비교할 열', 'format': '결과 형식', 'output_format': '결과 형식',
    'null_policy': '빈 값 처리', 'model': '모델', 'temperature': '모델 온도',
    'fields': '사용할 열', 'absolute_tolerance': '허용 오차', 'relative_tolerance': '비율 허용 오차',
    'precision': '소수 자릿수', 'decimal_places': '소수 자릿수', 'offset': '시작 위치',
    'top_k': '선택할 개수', 'timeout': '대기 시간', 'timeout_seconds': '대기 시간(초)',
    'min_length': '최소 길이', 'max_length': '최대 길이', 'batch_size': '한 번에 처리할 개수',
    'sheet_name': '시트 이름', 'range': '셀 범위', 'spreadsheet_id': '스프레드시트',
    'file_name': '파일 이름', 'filename': '파일 이름', 'subject': '메일 제목',
    'recipient': '받는 사람', 'text': '내용', 'message': '메시지', 'search': '검색어',
    'pattern': '찾을 내용', 'replacement': '바꿀 내용',
}
_ARG = re.compile(r'arguments_json\.([A-Za-z_][A-Za-z0-9_]*)\Z')
_FILTER = re.compile(r'execution_plan\.filters\[([0-4])\]\.(field|value|compare_field)\Z')
_SAFE_KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
_SENSITIVE = re.compile(r'secret|token|password|credential|api.?key|authorization', re.I)
_TEXT_ARGUMENTS = {'query', 'channel_id', 'value_column', 'sum_field', 'field',
                   'compare_field', 'format', 'output_format', 'null_policy', 'model'}
_BAD_REQUEST = '표시된 이전 값 또는 새 값이 실제 변경 요청과 다릅니다. 작업 설정을 다시 열어 요청해주세요.'
_DECODER = json.JSONDecoder()


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _same(left, right):
    return type(left) is type(right) and left == right or _number(left) and _number(right) and left == right


def _safe_key(key):
    return bool(_SAFE_KEY.fullmatch(key) and not _SENSITIVE.search(key)
                and key not in {'__proto__', 'prototype', 'constructor'})


def _label(path):
    arg = _ARG.fullmatch(path)
    if arg:
        return FIELD_LABELS.get(arg[1], '추가 설정')
    field = _FILTER.fullmatch(path)
    if field:
        return f"조건 {int(field[1]) + 1} · {('기준값' if field[2] == 'value' else FIELD_LABELS[field[2]])}"
    raise ValueError(_BAD_REQUEST)


def _numeric_filter(condition):
    if condition.get('value_type') != 'number' or condition.get('operator') in ('is_empty', 'not_empty'):
        return False
    value, column = condition.get('value'), condition.get('compare_field')
    numeric = _number(value)
    if type(value) is str and value.strip() and len(value) <= 32:
        try:
            numeric = math.isfinite(float(value))
        except ValueError:
            pass
    return numeric != (type(column) is str and bool(column.strip()) and len(column) <= 200)


def _editable(node):
    """Reproduce the popover's bounded field list and comparison ordering."""
    settings = node.get('settings') or {}
    try:
        arguments = json.loads(settings.get('arguments_json') or '{}')
    except (TypeError, ValueError):
        arguments = {}
    fields = {}
    if isinstance(arguments, dict):
        for key, value in arguments.items():
            if _safe_key(key) and (_number(value) or type(value) is str and key in _TEXT_ARGUMENTS and len(value) <= 200):
                fields['arguments_json.' + key] = value
    plan = settings.get('execution_plan') or {}
    filters = plan.get('filters', []) if isinstance(plan, dict) else []
    comparisons = {}
    for index, condition in enumerate(filters[:5] if isinstance(filters, list) else []):
        if not isinstance(condition, dict):
            continue
        if _numeric_filter(condition):
            comparisons[index] = condition
        for key in ('field', 'value', 'compare_field'):
            if index in comparisons and key != 'field':
                continue
            value = condition.get(key)
            if type(value) is str or _number(value):
                fields[f'execution_plan.filters[{index}].{key}'] = value
    return dict(list(fields.items())[:10]), comparisons


def _pair_values(value, column):
    if column is None and (_number(value) or type(value) is str and value.strip() and len(value) <= 32):
        try:
            if math.isfinite(float(value)):
                return '숫자', value
        except (TypeError, ValueError, OverflowError):
            pass
    if value is None and type(column) is str and column.strip() and len(column) <= 200:
        return '열', column
    raise ValueError(_BAD_REQUEST)


def _read_value(text):
    value, end = _DECODER.raw_decode(text)
    if type(value) not in (int, float, str, type(None)) or type(value) in (int, float) and not _number(value):
        raise ValueError(_BAD_REQUEST)
    return value, text[end:]


def _check_line(line, prefix, old, new, old_kind='', new_kind=''):
    if not line.startswith(prefix + old_kind):
        raise ValueError(_BAD_REQUEST)
    shown_old, tail = _read_value(line[len(prefix + old_kind):])
    if not tail.startswith(' → ' + new_kind):
        raise ValueError(_BAD_REQUEST)
    shown_new, tail = _read_value(tail[len(' → ' + new_kind):])
    if tail or not _same(shown_old, old) or not _same(shown_new, new):
        raise ValueError(_BAD_REQUEST)


def validate_visible_changes(edit, baseline_node):
    """Bind the friendly request, typed payload, and unchanged baseline together."""
    try:
        if (not isinstance(edit, dict) or not isinstance(baseline_node, dict)
                or edit.get('node_id') != baseline_node.get('id') or edit.get('label') != baseline_node.get('label')
                or type(edit.get('node_id')) is not str or type(edit.get('label')) is not str or type(edit.get('request')) is not str
                or len(edit['request']) > 4000 or '\r' in edit['request']
                or not isinstance(edit.get('changes'), list) or not 1 <= len(edit['changes']) <= 20):
            raise ValueError(_BAD_REQUEST)
        lines = edit['request'].split('\n')
        shown_label, suffix = _read_value(lines[0])
        if (shown_label != edit['label'] or suffix != ' 작업의 설정을 다음과 같이 바꿔주세요.'
                or lines[-1] != '다른 작업의 설정은 유지해주세요.'):
            raise ValueError(_BAD_REQUEST)
        fields, comparisons = _editable(baseline_node)
        order = {path: index for index, path in enumerate(fields)}
        for index in comparisons:
            prefix = f'execution_plan.filters[{index}]'
            order[prefix + '.value'] = len(fields) + index * 2
            order[prefix + '.compare_field'] = len(fields) + index * 2 + 1
        changes = edit['changes']
        seen, previous, position, row = set(), -1, 0, 1
        for change in changes:
            if not isinstance(change, dict) or set(change) != {'path', 'old_value', 'new_value'}:
                raise ValueError(_BAD_REQUEST)
            path = change['path']
            if type(path) is not str or path not in order or path in seen or order[path] <= previous:
                raise ValueError(_BAD_REQUEST)
            if type(change['new_value']) not in (str, int, float, type(None)) or type(change['new_value']) is str and len(change['new_value']) > 200:
                raise ValueError(_BAD_REQUEST)
            seen.add(path)
            previous = order[path]
        while position < len(changes):
            change = changes[position]
            path, old, new = change['path'], change['old_value'], change['new_value']
            field = _FILTER.fullmatch(path)
            if field and int(field[1]) in comparisons and field[2] == 'value':
                index = int(field[1])
                condition = comparisons[index]
                other = changes[position + 1]
                if other['path'] != path.rsplit('.', 1)[0] + '.compare_field':
                    raise ValueError(_BAD_REQUEST)
                if not _same(old, condition.get('value')) or not _same(other['old_value'], condition.get('compare_field')):
                    raise ValueError(_BAD_REQUEST)
                old_kind, old_value = _pair_values(old, other['old_value'])
                new_kind, new_value = _pair_values(new, other['new_value'])
                if new_kind == '숫자' and not _number(new):
                    raise ValueError(_BAD_REQUEST)
                label = f"조건 {index + 1}" + (f" ({condition['field']})" if condition.get('field') else '')
                _check_line(lines[row], f'- {label} 비교 기준: ', old_value, new_value, old_kind + ' ', new_kind + ' ')
                position += 2
            else:
                if path not in fields or not _same(fields[path], old):
                    raise ValueError(_BAD_REQUEST)
                if not (type(old) is str and type(new) is str or _number(old) and _number(new)):
                    raise ValueError(_BAD_REQUEST)
                # Identical visible labels and old values cannot prove which hidden setting was selected.
                if any(other != path and _label(other) == _label(path) and _same(value, old) for other, value in fields.items()):
                    raise ValueError(_BAD_REQUEST)
                _check_line(lines[row], '- ' + _label(path) + ': ', old, new)
                position += 1
            row += 1
        if row != len(lines) - 1:
            raise ValueError(_BAD_REQUEST)
    except (KeyError, TypeError, IndexError, ValueError, OverflowError):
        raise ValueError(_BAD_REQUEST) from None


_LEGACY_HEADER = re.compile(r'`([^\n]*)` 작업 \(노드 ID: ([^\n()]+)\)의 설정을 다음과 같이 바꿔줘\.\Z')
_LEGACY_SIMPLE = re.compile(r'- ([^\n()]+) \((arguments_json\.[A-Za-z_][A-Za-z0-9_]*|execution_plan\.filters\[[0-4]\]\.(?:field|value|compare_field))\): (.*?) → (.*)\Z')
_LEGACY_PAIR = re.compile(
    r'- 조건 ([1-5])(?: \(([^\n()]*)\))?: (?:숫자 직접 입력|비교 열)로 설정\. '
    r'(execution_plan\.filters\[[0-4]\]\.value): ([^;→]*) → ([^;→]*); '
    r'(execution_plan\.filters\[[0-4]\]\.compare_field): ([^;→]*) → ([^;→]*); value_type은 number로 유지\.\Z')


def _legacy_value(text, *, new=False):
    if new and text == '(비움)':
        return ''
    try:
        value = json.loads(text)
        if _number(value):
            return value
    except (TypeError, ValueError):
        pass
    return text


def public_request(text):
    """Project only a complete recognized legacy request, never arbitrary prose."""
    if not isinstance(text, str) or '\r' in text:
        return text
    lines = text.split('\n')
    header = _LEGACY_HEADER.fullmatch(lines[0]) if lines else None
    if not header or len(lines) < 3 or lines[-1] != '다른 작업의 설정은 유지해줘.':
        return text
    projected = []
    seen = set()
    try:
        for line in lines[1:-1]:
            simple = _LEGACY_SIMPLE.fullmatch(line)
            if simple:
                label, path, old, new = simple.groups()
                key = path.rsplit('.', 1)[-1]
                if path in seen or not _safe_key(key) or label not in {_label(path), key, '조건 ' + str(int(_FILTER.fullmatch(path)[1]) + 1) + ' · ' + key if _FILTER.fullmatch(path) else key}:
                    return text
                seen.add(path)
                projected.append(f'- {_label(path)}: {_json(_legacy_value(old))} → {_json(_legacy_value(new, new=True))}')
                continue
            pair = _LEGACY_PAIR.fullmatch(line)
            if not pair:
                return text
            index, field, value_path, old_value, new_value, column_path, old_column, new_column = pair.groups()
            if value_path.rsplit('.', 1)[0] != column_path.rsplit('.', 1)[0] or int(index) != int(_FILTER.fullmatch(value_path)[1]) + 1 or value_path in seen or column_path in seen:
                return text
            seen.update((value_path, column_path))
            old_kind, old = _pair_values(json.loads(old_value), json.loads(old_column))
            new_kind, new = _pair_values(json.loads(new_value), json.loads(new_column))
            name = f'조건 {index}' + (f' ({field})' if field else '')
            projected.append(f'- {name} 비교 기준: {old_kind} {_json(old)} → {new_kind} {_json(new)}')
    except (TypeError, ValueError, KeyError):
        return text
    return '\n'.join([_json(header[1]) + ' 작업의 설정을 다음과 같이 바꿔주세요.', *projected, '다른 작업의 설정은 유지해주세요.'])


def change_summary(edit):
    """Summarize validated typed changes without revealing storage metadata."""
    summaries = []
    changes = edit.get('changes') or []
    position = 0
    while position < len(changes):
        change = changes[position]
        field = _FILTER.fullmatch(change['path'])
        if (field and field[2] == 'value' and position + 1 < len(changes)
                and changes[position + 1]['path'] == change['path'].rsplit('.', 1)[0] + '.compare_field'):
            other = changes[position + 1]
            old_kind, old = _pair_values(change['old_value'], other['old_value'])
            new_kind, new = _pair_values(change['new_value'], other['new_value'])
            summaries.append(f"조건 {int(field[1]) + 1} 비교 기준: {old_kind} {_json(old)} → {new_kind} {_json(new)}")
            position += 2
            continue
        if not _same(change['old_value'], change['new_value']):
            summaries.append(f"{_label(change['path'])}: {_json(change['old_value'])} → {_json(change['new_value'])}")
        position += 1
    return ', '.join(summaries)[:1200]


_ERROR_COPY = (
    (('노드의 이전 값이 변경되었습니다.',), '작업의 이전 값이 변경되어 적용하지 않았습니다. 작업 설정을 다시 열고 값을 확인한 뒤 요청해주세요.'),
    (('노드의 비교 조건이 변경되었습니다.',), '작업의 비교 조건이 변경되어 적용하지 않았습니다. 작업 설정을 다시 열고 값을 확인한 뒤 요청해주세요.'),
    (('변경할 노드를 찾을 수 없습니다.', '변경 대상 노드가 다릅니다.'), '변경할 작업을 찾을 수 없어 적용하지 않았습니다. 최신 워크플로우에서 작업 설정을 다시 열어주세요.'),
    (('이 인자는 노드 설정창에서 직접 변경할 수 없습니다', '설정창에서 지원하지 않는 변경 경로입니다'), '이 설정은 변경창에서 직접 변경할 수 없습니다. 대화에서 변경할 내용을 설명해주세요.'),
    (('현재 노드의 인자 JSON을 읽을 수 없습니다.',), '작업 설정을 읽을 수 없어 변경하지 않았습니다. 작업 설정을 다시 열고 요청해주세요. 문제가 반복되면 관리자에게 알려주세요.'),
    (('다른 단계나 설명에 이전 설정값이 남아',), '다른 단계나 설명에 이전 설정값이 남아 있어 적용하지 않았습니다. 대화에서 관련 조건을 함께 수정해주세요.'),
    (('다른 단계나 설명에 이전 수량이 남아',), '다른 단계나 설명에 이전 수량이 남아 있어 적용하지 않았습니다. 대화에서 관련 조건을 함께 수정해주세요.'),
    (('다른 단계나 설명에 이전 숫자가 남아',), '다른 단계나 설명에 이전 숫자가 남아 있어 적용하지 않았습니다. 대화에서 관련 조건을 함께 수정해주세요.'),
    (('검색 조건은 작업 범위와 다른 단계의 설명에 연결돼',), '검색 조건은 작업 범위와 다른 단계의 설명에 연결돼 있어 바로 적용하지 않았습니다. 대화에서 전체 검색 조건을 수정해주세요.'),
    (('여러 메일 조회 작업이 같은 요약에 연결되어 있습니다.',), '여러 메일 조회 작업이 같은 요약에 연결되어 있습니다. 어느 조회 한도를 적용할지 대화에서 확인해 주세요.'),
    (('변경한 설정을 실행할 수 없습니다',), '변경한 값으로 작업을 실행할 수 없어 적용하지 않았습니다. 값의 형식과 연결된 자료를 확인한 뒤 다시 요청해주세요.'),
    (('변경된 설정 값이 없습니다.',), '현재 설정과 같은 값이어서 변경하지 않았습니다. 바꾸려는 값을 입력한 뒤 다시 요청해주세요.'),
    (('진행 중인 확인 질문을 해결한 뒤',), '아직 확인할 업무 조건이 있어 변경하지 않았습니다. 대화의 확인 질문에 답한 뒤 다시 요청해주세요.'),
    (('먼저 워크플로우 초안을 검증한 뒤',), '작업 초안이 아직 확인되지 않아 변경하지 않았습니다. 대화에서 초안 확인을 마친 뒤 다시 요청해주세요.'),
    (('노드 변경 후', '노드 변경 검증 기록이 없습니다.'), '요청 후 작업 설정이나 확인 기록이 바뀌었습니다. 최신 작업 설정을 다시 열어 변경 요청을 보내주세요.'),
    (('조건 열 이름을 입력해 주세요.', '비교할 열 이름을 확인해 주세요.', '비교 기준값을 확인해 주세요.', '노드 변경 값은 문자열 또는 숫자여야 합니다.', '노드 변경 값의 길이 또는 숫자 범위를 확인해 주세요.'), '입력한 값의 형식이나 범위를 확인할 수 없어 적용하지 않았습니다. 숫자와 열 이름을 확인한 뒤 다시 요청해주세요.'),
    (('노드 변경 요청', '노드 변경 항목', '표시된 변경', '표시된 이전 값', '표시된 노드 변경', '같은 설정을 두 번 변경할 수 없습니다'), _BAD_REQUEST),
)


def public_error(error):
    """Return bounded guidance without raw validation/provider bodies or paths."""
    text = str(error)
    if text.startswith('변경한 설정을 실행할 수 없습니다'):
        for match in re.finditer(r'\bmax_results 실행 인자의 허용 규격: (\{[^{}\n]*\})(?=$| / )', text):
            try:
                rule = json.loads(match[1])
            except ValueError:
                continue
            if rule == {'type': 'positive_integer', 'maximum': 500}:
                return '메일 조회 한도는 1~500건의 정수로 입력해야 합니다. 작업 설정에서 조회 한도를 확인한 뒤 다시 요청해주세요.'
    for prefixes, copy in _ERROR_COPY:
        if text.startswith(prefixes):
            return copy
    return '설정 변경을 확인하는 중 문제가 생겨 적용하지 않았습니다. 작업 설정을 다시 열어 변경 요청을 보내주세요.'


def is_node_edit_error(text):
    """Identify old node-edit failures before projecting stored assistant messages."""
    return isinstance(text, str) and any(text.startswith(prefixes) for prefixes, _ in _ERROR_COPY)
