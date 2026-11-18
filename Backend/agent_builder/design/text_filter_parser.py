"""Locate text comparison operands before interpreting a clause's action.

A conjunction is a boundary between atoms, not part of a value. The replay
layer owns edits and pending decisions; this lexer never mutates a condition.
"""
from dataclasses import dataclass
import re

from agent_builder.conditions.filter_edits import (
    DATA_ROW_NOUN, in_metadata_text, in_quoted_text, selection_action_uncertain,
)

_VALUE = r'''(?:"[^"\n]+"|'[^'\n]+'|“[^”\n]+”|‘[^’\n]+’|`[^`\n]+`|[^\s,'"`.!?]+?)'''
_ROW_UNIT = r'(?:(?:행|것|거래|항목|레코드|셀|주문(?:\s*+건)?|건)(?:들)?|' + DATA_ROW_NOUN + r')'
_ROWS = _ROW_UNIT + r'(?P<only>만|은|을|도)?'
_SAME_AS = r'(?:과|와)\s*+(?:같은|동일한|일치하는)'
_EQUALITY_LINK = r'(?:인|' + _SAME_AS + r')'
_END = (r'(?:' + _EQUALITY_LINK + r'\s+' + _ROWS
        + r'|(?P<join>이고|이며|이면서)(?=\s)|(?P<bare>만)(?=\s|[,;.!?]|$))')
_CONTAINMENT_END = (r'(?:(?:이|가)\s*+(?:들어\s*+(?:있는|간)|포함된)|(?:을|를)\s*+포함하는)\s+'
                    + _ROWS + r'(?![가-힣])')
_OR = re.compile(r'또는|혹은|이거나|이든|아니면')
_OUTPUT_SCOPE = re.compile(r'(?:결과물?|출력|산출물|보고서|파일|CSV|JSON|XLSX|엑셀)(?:(?:에|에서)(?:는|도)?|은|는)', re.I)
_SELECT = re.compile(r'\s*(?:남겨|골라|선별해|포함해)(?:\s*(?:줘|주세요))?(?![가-힣])')
_EXCLUDE = re.compile(r'\s*(?:제외해|삭제해|빼줘)(?:\s*(?:줘|주세요))?(?![가-힣])')
_SEQUENCE = re.compile(r'\s*(?:남긴|고른|선별한)\s+(?:뒤|후|다음)(?![가-힣])')
_LINK = re.compile(r'\s*(?:남기고|골라서)\s*(?:[,，]\s*)?')
_EXPORT = (r'(?:(?:CSV|JSON)(?:으로|로)|파일로)\s+저장'
           r'(?:해(?:\s*(?:줘|주세요))?|하세요)?')
_COMPLETE_EXPORT = re.compile(r'\s*' + _EXPORT + r'\s*[.!;。！]?\s*$', re.I)
_COMPLETE_SORT = re.compile(
    r'[^.!?;。！？\n]*?(?:(?:으로|로)\s+|(?:오름차순|내림차순)\s*(?:으로|로)?\s+)(?P<action>정렬)'
    r'(?:해(?:\s*(?:줘|주세요))?|하세요|(?:해서|하고)\s+' + _EXPORT + r')?\s*[.!;。！]?\s*$', re.I)


@dataclass(frozen=True)
class ColumnProjection:
    start: int
    end: int
    fields: tuple[str, ...]
    affirmed: bool


def column_projection_requests(text, fields):
    """Locate schema-owned output column lists without interpreting them as cell values."""
    names = '|'.join(re.escape(field) for field in sorted(fields, key=len, reverse=True))
    if not names:
        return []
    column = r'(?:' + names + ')'
    pattern = re.compile(r'\s*(?P<columns>' + column + r'(?:\s*(?:,|·|과|와|및)\s*' + column + r')*)'
                         r'\s*(?:열|컬럼)?\s*(?:만)?(?:을|를)?\s*(?:포함|표시|출력|저장|내보내|남겨|남기)', re.I)
    result = []
    for scope in _OUTPUT_SCOPE.finditer(text):
        match = pattern.match(text, scope.end())
        if match and not in_quoted_text(text, scope.start()) and not in_metadata_text(text, scope.start()):
            selected = [item.group() for item in re.finditer(column, match['columns'])]
            tail = re.split(r'[.!?;\n]|하고|그리고', text[match.end():], maxsplit=1)[0]
            denied = bool(re.match(r'\s*(?:하)?(?:지|진)\s*(?:는|도)?\s*(?:말|마|않|안)|\s*고\s*싶지\s*않', tail))
            result.append(ColumnProjection(scope.start(), match.end(), tuple(dict.fromkeys(selected)), not denied))
    return result


@dataclass(frozen=True)
class TextAtom:
    start: int
    end: int
    field: str
    value: str | None
    only: bool
    joined: bool = False
    operator: str = 'eq'
    compare_field: str | None = None


def text_atoms(text, fields):
    names = '|'.join(re.escape(field) for field in sorted(fields, key=len, reverse=True))
    if not names:
        return []
    subject = r'(?<![\w])(?P<field>' + names + r')\s*+(?:열\s*+)?'
    operand = r'\s*+(?P<value>' + _VALUE + ')'
    containment = re.compile(subject + r'(?:이|가|은|는|에(?:는)?)' + operand + _CONTAINMENT_END)
    forward_subject = r'(?<![\w])(?P<field>' + names + r')\s*+(?:열[의은는이가]?\s*+)?(?:이|가|은|는)'
    column_comparison = re.compile(forward_subject + r'\s*(?P<compare_field>' + names + r')\s*'
                                   r'(?:(?:열|컬럼)\s*)?' + _SAME_AS + r'\s+' + _ROWS)
    forward = re.compile(forward_subject + operand + _END)
    reverse = re.compile(
        r'(?<![\w])(?P<value>' + _VALUE + r')\s+(?P<field>' + names + r')'
        r'(?:(?P<bare>만)|인\s+' + _ROW_UNIT + r'(?P<only>만))(?=\s|[,;.!?]|$)')
    projections = column_projection_requests(text, fields)
    atoms = []
    for pattern in (containment, column_comparison, forward, reverse):
        for match in pattern.finditer(text):
            if (any(item.start <= match.start('field') < item.end for item in projections)
                    or in_quoted_text(text, match.start('field')) or in_metadata_text(text, match.start('field'))
                    or any(atom.start < match.end() and match.start() < atom.end for atom in atoms)):
                continue
            groups = match.groupdict()
            value = groups.get('value')
            # In "결과에는 상태만", the first token introduces the output scope.
            if pattern is reverse and groups.get('bare') and _OUTPUT_SCOPE.fullmatch(value):
                continue
            if value and value[0] in '\"\'“‘`':
                value = value[1:-1]
            atoms.append(TextAtom(match.start(), match.end(), match['field'], value,
                                  bool(groups.get('only') == '만' or groups.get('bare')),
                                  bool(groups.get('join')),
                                  operator='contains' if pattern is containment else 'eq',
                                  compare_field=groups.get('compare_field')))
    return sorted(atoms, key=lambda atom: atom.start)


def selection_action(clause, atoms, index, terminator=''):
    """Classify what the clause tells us to do with this already parsed operand."""
    atom = atoms[index]
    tail = clause[atom.end:atoms[index + 1].start if index + 1 < len(atoms) else len(clause)]
    action = clause[atoms[-1].end:] if atom.joined else tail
    if selection_action_uncertain(action):
        return 'negative'
    if terminator in ('?', '？'):
        return 'unclear'
    if _EXCLUDE.match(action):
        return 'exclude'
    bare = atom.only and re.match(r'\s*(?:$|,|(?:이고|이며|그리고|및)\s|(?:CSV|JSON|파일|결과)(?:으로|로|\s))', tail, re.I)
    joined = atom.joined and atoms[-1].only and re.match(r'\s*(?:$|(?:CSV|JSON|파일|결과)(?:으로|로|\s))', action, re.I)
    if _SELECT.match(action) or _SEQUENCE.match(action) or bare or joined or re.match(r'\s*이어야\s*(?:해|합니다)', tail):
        return 'include'
    if _COMPLETE_EXPORT.fullmatch(action + terminator):
        return 'include'
    link = _LINK.match(action)
    if link:
        continuation = action[link.end():] + terminator
        sorting = _COMPLETE_SORT.fullmatch(continuation)
        if _COMPLETE_EXPORT.fullmatch(continuation) or (
            sorting and not in_metadata_text(continuation, sorting.start('action'))
            and not in_quoted_text(continuation, sorting.start('action'))
        ):
            return 'include'
    return 'unclear'


def disjunctive_clause(text):
    # Quoted cell contents such as '서울 또는 부산' are one literal value.
    from agent_builder.conditions.filter_edits import _QUOTED
    return bool(_OR.search(_QUOTED.sub(' ', text)))


def uncovered_selection_fields(text, fields, atoms):
    """An unparsed row-selection operand is a question, never permission to drop it."""
    if not re.search(r'만|남겨|골라|선별', text):
        return set()
    if re.search(r'(?:열|컬럼)(?:만|을|를)', text) and not atoms:
        return set()
    projections = column_projection_requests(text, fields)
    missing = set()
    for field in fields:
        for match in re.finditer(r'(?<![\w])' + re.escape(field) + r'(?![A-Za-z0-9_])', text):
            if (any(item.start <= match.start() < item.end for item in projections)
                    or in_quoted_text(text, match.start()) or in_metadata_text(text, match.start())
                    or any(atom.start <= match.start() < atom.end for atom in atoms)):
                continue
            tail = text[match.end():]
            prefix = text[:match.start()]
            if re.match(r'\s*(?:이|가|은|는|을|를)?\s*(?:마스킹|익명화|가려|숨겨)', tail):
                continue
            if re.search(r'중복|빈\s*$|결측', prefix):
                continue
            if re.match(r'\s*(?:이|가|은|는)?\s*(?:큰|작은|높은|낮은|많은|적은|부족한|같은|같거나|다른|겹치|비어|빈|없는|누락)', tail):
                continue
            # Numeric operands, policy subjects and output mentions have other owners.
            if re.search(r'\d|이상|초과|미만|이하|중복|정렬|포함|출력|기준|조건|필터', tail):
                continue
            if re.match(r'\s*(?:이|가|은|는)\s*', tail):
                missing.add(field)
    return missing
