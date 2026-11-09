"""Optional deployment limits for native and OpenAI-compatible Ollama calls."""
import math
import os


def integer_setting(name, default, minimum, maximum):
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f'{name}는 {minimum}~{maximum} 정수여야 합니다.')
    return value


def temperature_setting(default):
    value = float(os.getenv('A4B_OLLAMA_TEMPERATURE', str(default)))
    if not math.isfinite(value) or not 0 <= value <= 2:
        raise ValueError('A4B_OLLAMA_TEMPERATURE는 0~2여야 합니다.')
    return value


def output_limit(default=4096):
    return integer_setting('A4B_OLLAMA_NUM_PREDICT', default, 1, 32768)
