"""Explicit, one-hop Ollama routing for persisted model references; never rewrite data."""
import json
import os
import re


SETTING = 'A4B_OLLAMA_MODEL_ALIASES'


def canonical_model(name):
    return name if ':' in name.rsplit('/', 1)[-1] else name + ':latest'


def model_aliases():
    raw = os.getenv(SETTING, '').strip()
    if not raw:
        return {}
    try:
        aliases = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise ValueError(f'{SETTING}는 이전 모델명: 대상 모델명의 JSON 객체여야 합니다.') from error
    if not isinstance(aliases, dict) or any(
        not isinstance(name, str) or not re.fullmatch(r'[-A-Za-z0-9_./:]{1,120}', name)
        for pair in aliases.items() for name in pair
    ):
        raise ValueError(f'{SETTING}의 모델 이름을 확인하세요.')
    sources = [canonical_model(name) for name in aliases]
    if len(set(sources)) != len(sources) or any(canonical_model(target) in sources for target in aliases.values()):
        raise ValueError(f'{SETTING}는 중복 별칭이나 연쇄·순환 연결을 허용하지 않습니다.')
    return {canonical_model(source): target for source, target in aliases.items()}


def resolve_ollama_model(model):
    return model_aliases().get(canonical_model(model), model)


def same_model_reference(first, second, credential_id):
    # Reuse is allowed only within the same connection (checked by the caller).
    if credential_id == 'local-model' and os.getenv('A4B_MAIN_PROVIDER') == 'ollama':
        return canonical_model(resolve_ollama_model(first)) == canonical_model(resolve_ollama_model(second))
    return first == second
