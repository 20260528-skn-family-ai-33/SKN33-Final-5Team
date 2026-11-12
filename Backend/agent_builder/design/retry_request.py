"""Recognize only an explicit retry of a failed design request."""
import re


def is_generation_retry(content):
    text = re.sub(r'[\s.!?]+', '', content).casefold()
    return text in {
        '다시시도', '재시도', '재시도해줘', '다시시도해줘', '다시시도해주세요',
        '조건그대로다시시도', '앞서전달한조건을유지하고업무흐름생성을다시시도해주세요',
    }
