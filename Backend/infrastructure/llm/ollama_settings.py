"""Optional deployment limits for native and OpenAI-compatible Ollama calls."""
import math
import os

RESIDENT_MODELS = frozenset({'a4b-qwen35-9b-previous-pilot32k',
                             'a4b-qwen35-9b-final664-pilot32k'})


def is_resident_model(model):
    return isinstance(model, str) and model.removesuffix(':latest') in RESIDENT_MODELS


def context_limit(model):
    return integer_setting('A4B_OLLAMA_NUM_CTX', 32768 if is_resident_model(model) else 16384,
                           4096, 131072)


def resident_options(model):
    # Sampling/output settings remain caller-controlled. Other models retain
    # their existing residency policy instead of occupying a 9B serving slot.
    return {'shift': False, 'truncate': False, 'keep_alive': -1} if is_resident_model(model) else {}


def integer_setting(name, default, minimum, maximum):
    value = int(os.getenv(name, '').strip() or str(default))
    if not minimum <= value <= maximum:
        raise ValueError(f'{name}는 {minimum}~{maximum} 정수여야 합니다.')
    return value


def temperature_setting(default):
    value = float(os.getenv('A4B_OLLAMA_TEMPERATURE', '').strip() or str(default))
    if not math.isfinite(value) or not 0 <= value <= 2:
        raise ValueError('A4B_OLLAMA_TEMPERATURE는 0~2여야 합니다.')
    return value


def output_limit(default=4096):
    return integer_setting('A4B_OLLAMA_NUM_PREDICT', default, 1, 32768)


def bounded_output_limit(requested):
    """Apply the optional deployment ceiling to both requests and retry budgets."""
    if os.getenv('A4B_OLLAMA_NUM_PREDICT', '').strip():
        return min(requested, output_limit())
    return requested
