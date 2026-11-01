"""문장 전처리 (계획서 3장).

- normalize: 유니코드 정규화(NFKC), 공백 정리
- placeholders: 날짜, 숫자, 이메일을 자리표시 토큰으로 바꿈 (값만 다른 변형을 같은 요청으로 보게)
- negation: 부정 문구 처리. keep(그대로) / drop(부정 문장 삭제) / mark(부정 문장 단어에 NOT_)
"""
import re
import unicodedata

DATE = re.compile(r'\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}월\s*\d{1,2}일|\d{1,2}:\d{2}')
EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')
NUM = re.compile(r'\d[\d,]*(?:\.\d+)?')
# '빼고', '제외'는 필터 지시라 부정 신호로 보지 않는다.
NEG_CUE = re.compile(r'하지\s*마|하지\s*말|않|넣지\s*마|넣지\s*말|붙이지|지어내지|보내지는\s*말|보내지\s*마')
SENT_SPLIT = re.compile(r'(?<=[.!?])\s+|\n+')


def normalize(text):
    text = unicodedata.normalize('NFKC', text)
    return re.sub(r'\s+', ' ', text).strip()


def placeholders(text):
    text = EMAIL.sub(' __email__ ', text)
    text = DATE.sub(' __date__ ', text)
    text = NUM.sub(' __num__ ', text)
    return re.sub(r'\s+', ' ', text).strip()


def negation(text, mode='keep'):
    if mode == 'keep':
        return text
    out = []
    for sent in SENT_SPLIT.split(text):
        if not NEG_CUE.search(sent):
            out.append(sent)
        elif mode == 'mark':
            out.append(' '.join('NOT_' + w for w in sent.split()))
        elif mode != 'drop':
            raise ValueError(mode)
    return ' '.join(out)


def for_tfidf(text, neg='keep'):
    """TF-IDF용: 정규화 → 부정 처리 → 자리표시 → 소문자."""
    return placeholders(negation(normalize(text), neg)).lower()


def for_encoder(text):
    """임베딩, 미세조정 모델용: 정규화만 한다 (부정과 숫자는 모델이 읽게 둠)."""
    return normalize(text)
