"""특성 만들기 (계획서 4장). 설정 하나가 sklearn 변환기 하나가 된다.

cfg 예: {"unit": "char", "resource": "kind+columns", "neg": "mark", "keywords": True}
- unit: word(띄어쓰기 1~2개 묶음) / char(글자 2~4개 묶음) / kiwi(형태소, kiwipiepy 필요)
- resource: none / kind / kind+columns
- neg: keep / drop / mark
- keywords: 핵심 단어 사전 0/1 특성 추가 여부
"""
import functools

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder

from .data import KINDS
from .preprocess import for_tfidf
from .rules import keyword_flags

_KIWI = None


@functools.lru_cache(maxsize=200000)
def kiwi_tokens(text):
    global _KIWI
    if _KIWI is None:
        try:
            from kiwipiepy import Kiwi
        except ImportError as e:
            raise ImportError('unit=kiwi는 pip install kiwipiepy 가 필요합니다') from e
        _KIWI = Kiwi()
    keep = ('NN', 'VV', 'VA', 'XR', 'SL', 'MAG')
    return tuple(t.form for t in _KIWI.tokenize(text) if t.tag.startswith(keep) or t.form.startswith('__'))


def add_text(df, neg='keep'):
    df = df.copy()
    df['text'] = [for_tfidf(q, neg) for q in df['request']]
    return df


def keyword_matrix(df):
    return np.array([keyword_flags(q) for q in df['request']])


def text_vectorizer(unit):
    if unit == 'word':
        return TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True, token_pattern=r'(?u)\b\w+\b')
    if unit == 'char':
        return TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 4), min_df=1, sublinear_tf=True)
    if unit == 'kiwi':
        return TfidfVectorizer(tokenizer=kiwi_tokens, token_pattern=None, lowercase=False, ngram_range=(1, 2), sublinear_tf=True)
    raise ValueError(unit)


def build(cfg):
    parts = [('txt', text_vectorizer(cfg['unit']), 'text')]
    if cfg['resource'] in ('kind', 'kind+columns'):
        parts.append(('kind', OneHotEncoder(categories=[KINDS], handle_unknown='ignore'), ['kind']))
    if cfg['resource'] == 'kind+columns':
        parts.append(('cols', TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 3)), 'columns_text'))
    if cfg.get('keywords'):
        parts.append(('kw', FunctionTransformer(keyword_matrix), ['request']))
    return Pipeline([
        ('prep', FunctionTransformer(add_text, kw_args={'neg': cfg['neg']})),
        ('cols', ColumnTransformer(parts, sparse_threshold=1.0)),
    ])


def name(cfg):
    return f"{cfg['unit']}|{cfg['resource']}|neg={cfg['neg']}|kw={int(bool(cfg.get('keywords')))}"
