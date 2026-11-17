"""Screen high-confidence model-written instructions in clarification text.

The model may echo instructions to itself as ``missing_conditions``.  Keep real
questions in a multi-sentence item, but never turn an instruction to check or
perform the whole task into a required answer field.
"""
import re


_SENTENCE = re.compile(r'(?<=[.!?？。])\s+|\n+')
_META_CHECK = re.compile(
    r'^(?:(?:자료|원문|기존|이전|현재|전체|모든|요청한|변경한)\s+)*'
    r'(?:처리(?:\s*(?:단계|과정))?|업무|작업|요청|설정|구성|설계)'
    r'(?:\s*(?:단계|과정|와|과|및|조건|전송|내용|흐름))*'
    r'\s*(?:을|를)?\s*(?:확인|검토)\s*(?:해|하여|해\s*주|해\s*보아)?\s*'
    r'(?:주세요|주십시오|하세요|바랍니다)$'
)
_META_EXECUTE = re.compile(
    r'^(?:원문|기존|이전|전체|모든|요청한)\s*(?:업무|작업|요청)'
    r'.*(?:변경|수정|추가)(?:한|된)?\s*(?:조건|내용|요청)'
    r'.*(?:모두|전부|그대로)?.*(?:수행|반영|유지|보존|적용|진행)\s*'
    r'(?:해|하여)\s*(?:주세요|주십시오|하세요|바랍니다)$'
)


def _meta_instruction(sentence):
    text = sentence.strip().rstrip('.!?？。').strip()
    return bool(_META_CHECK.fullmatch(text) or _META_EXECUTE.fullmatch(text))


def strip_known_meta_instructions(text):
    """Remove known task instructions, or return ``None`` if nothing remains.

    This is deliberately narrow.  Remaining text still goes through the existing
    settled-question and answer controls; this function alone does not prove that
    it is an answerable question.
    """
    if not isinstance(text, str):
        return None
    kept = []
    seen = set()
    for part in _SENTENCE.split(text.strip()):
        part = part.strip()
        if not part or _meta_instruction(part):
            continue
        key = re.sub(r'[\s.!?？。]+', '', part).casefold()
        if key not in seen:
            seen.add(key)
            kept.append(part)
    return ' '.join(kept) if kept else None
