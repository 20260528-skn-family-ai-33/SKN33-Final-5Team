"""Fill one missing instruction field without regenerating a valid graph."""
from copy import deepcopy
import json


def correct_instructions(raw, state, llm):
    if isinstance(raw.get('instructions'), str) and raw['instructions'].strip():
        return raw
    schema = {'title': 'ExecutionInstructions', 'type': 'object', 'additionalProperties': False,
              'properties': {'instructions': {'type': 'string', 'minLength': 1, 'maxLength': 4000}},
              'required': ['instructions']}
    response = llm.with_structured_output(schema, method='json_schema', strict=True).invoke([
        {'role': 'system', 'content': '사용자가 요청한 업무의 실행 지시사항 한 항목만 작성합니다. '
         '확정된 조건을 보존하고 요청하지 않은 기준·숫자·동작을 만들지 마세요. '
         'JSON 초안 자체를 문자열로 복사하거나 안내 문구를 반복하지 마세요. '
         '지시사항은 짧은 한국어 문장으로 쓰고, 미정 조건은 확정하지 마세요.'},
        {'role': 'user', 'content': json.dumps({
            'user_messages': [m['content'] for m in state['messages'] if m['role'] == 'user'],
            'purpose': raw.get('purpose'), 'workflow': raw.get('workflow'),
            'questions': raw.get('questions', []), 'confirmed_conditions': state.get('condition_ledger')}, ensure_ascii=False)}])
    if hasattr(response, 'model_dump'):
        response = response.model_dump()
    if not isinstance(response, dict) or set(response) != {'instructions'}:
        raise ValueError('누락된 지시사항만 보완할 수 있습니다.')
    value = response['instructions']
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError('실행 지시사항은 1~4,000자로 작성해야 합니다.')
    result = deepcopy(raw)
    result['instructions'] = value.strip()
    return result
