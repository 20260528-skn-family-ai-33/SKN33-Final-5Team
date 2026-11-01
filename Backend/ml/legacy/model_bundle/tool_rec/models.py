"""모델 (계획서 5장). 모두 fit(df, Y) / scores(df) → (요청 수, 도구 14개) 점수.

지금 바로 돌릴 수 있는 것: r0, lr, svm
다운로드가 필요한 것: embed_lr, embed_sim (sentence-transformers 모델을 받아야 함)
"""
import hashlib
import os
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

from . import features
from .data import KINDS
from .labels import TOOLS
from .preprocess import for_encoder
from .rules import r0_scores

# registry.py(4f71484) 도구 설명. embed_sim이 요청과 비교하는 문장.
DESCRIPTIONS = {
    'read_table': '연결한 CSV, Excel, JSON 표 파일의 실제 행을 읽습니다.',
    'filter_sort': '여러 조건의 값과 열 비교, 숫자와 문자 정렬, 중복 및 결측 정책을 적용합니다.',
    'write_result': '표 처리 결과를 CSV 또는 JSON 파일로 만듭니다.',
    'aggregate_table': '그룹별로 건수나 합계를 집계합니다.',
    'read_document': '연결 문서를 페이지와 출처와 함께 읽습니다.',
    'search_document': '질문과 일치하는 문서 구간을 검색합니다.',
    'extract_information': '문서에서 설정한 항목을 추출하고 원문 인용을 검증합니다.',
    'compose_answer': '검색된 원문만 바탕으로 답변 또는 요약을 작성합니다.',
    'export_result': '추출 표를 CSV, JSON으로, 답변을 Markdown 파일로 만듭니다.',
    'reply_review': '고객 원문, 정책, 응대 초안을 보고 판정, 문제 구절, 수정안을 보고합니다.',
    'review_summary': '검수 결과를 보고서 표로 정리합니다.',
    'agent_node': '지시에 따라 글을 쓰거나 바꾸거나 정리해 텍스트를 반환합니다.',
    'condition': '누락 항목이나 빈 결과 여부에 따라 예, 아니오 경로를 고릅니다.',
    'ask_user': '실행을 멈추고 사람의 확인 답변을 받아 이어 갑니다.',
}


class R0:
    """규칙 기준선. 학습할 것은 동점 정렬용 도구 빈도뿐."""
    prob_output = False

    def fit(self, df, Y):
        self.prior = Y.mean(axis=0)
        return self

    def scores(self, df):
        return r0_scores(df, self.prior)


class Linear:
    """TF-IDF 계열 특성 + 도구마다 선형 분류기 하나 (One-vs-Rest)."""

    def __init__(self, feat_cfg, kind='lr', C=1.0, class_weight=None):
        self.feat_cfg, self.kind, self.C, self.class_weight = feat_cfg, kind, C, class_weight
        self.prob_output = kind == 'lr'

    def fit(self, df, Y):
        if self.kind == 'lr':
            base = LogisticRegression(C=self.C, class_weight=self.class_weight, max_iter=2000, solver='liblinear')
        else:
            base = LinearSVC(C=self.C, class_weight=self.class_weight)
        self.pipe = Pipeline([('feat', features.build(self.feat_cfg)), ('clf', OneVsRestClassifier(base))])
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', (ConvergenceWarning, UserWarning))
            self.pipe.fit(df, Y)
        return self

    def scores(self, df):
        if self.kind == 'lr':
            return self.pipe.predict_proba(df)
        return self.pipe.decision_function(df)


def _encoder_input(df, with_resource):
    out = []
    for q, k, c in zip(df['request'], df['kind'], df['columns_text']):
        s = '요청: ' + for_encoder(q)
        if with_resource:
            s += f' / 자료: {k}' + (f' (열: {c})' if c else '')
        out.append(s)
    return out


class _Encoder:
    """sentence-transformers 인코더. 같은 문장은 cache 폴더에 저장해 두고 다시 쓴다."""

    _loaded = {}   # 같은 모델을 여러 번 불러오지 않게

    def __init__(self, model_name, query_prefix='', cache_dir='cache', max_len=512):
        self.model_name, self.query_prefix, self.cache_dir, self.max_len = model_name, query_prefix, cache_dir, max_len

    def encode(self, texts):
        """문장마다 따로 캐시한다. fold마다 다른 조합을 불러도 한 번 계산한 문장은 다시 계산하지 않는다."""
        os.makedirs(self.cache_dir, exist_ok=True)
        tag = hashlib.sha1(f'{self.model_name}|{self.query_prefix}|{self.max_len}'.encode()).hexdigest()[:10]
        path = os.path.join(self.cache_dir, f'emb_{tag}.npz')
        store = dict(np.load(path)) if os.path.exists(path) else {}
        keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
        todo = sorted({(k, t) for k, t in zip(keys, texts) if k not in store})
        if todo:
            if self.model_name not in _Encoder._loaded:
                from sentence_transformers import SentenceTransformer
                m = SentenceTransformer(self.model_name, device='cpu')
                m.max_seq_length = self.max_len
                _Encoder._loaded = {self.model_name: m}   # 메모리 16GB라 한 번에 하나만 둔다
            emb = _Encoder._loaded[self.model_name].encode([self.query_prefix + t for _, t in todo],
                                                           normalize_embeddings=True, batch_size=16)
            store.update({k: e for (k, _), e in zip(todo, emb)})
            np.savez(path, **store)
        return np.stack([store[k] for k in keys])


class EmbedLR:
    """임베딩 + 로지스틱 회귀 (M4). 예: model_name='nlpai-lab/KURE-v1'"""
    prob_output = True

    def __init__(self, model_name, query_prefix='', with_resource=True, C=1.0, class_weight=None):
        self.enc = _Encoder(model_name, query_prefix)
        self.with_resource, self.C, self.class_weight = with_resource, C, class_weight

    def _x(self, df):
        emb = self.enc.encode(_encoder_input(df, self.with_resource))
        onehot = np.array([[k == x for x in KINDS] for k in df['kind']], dtype=float)
        return np.hstack([emb, onehot])

    def fit(self, df, Y):
        base = LogisticRegression(C=self.C, class_weight=self.class_weight, max_iter=2000)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            self.clf = OneVsRestClassifier(base).fit(self._x(df), Y)
        return self

    def scores(self, df):
        return self.clf.predict_proba(self._x(df))


class EmbedSim:
    """요청과 도구 설명의 코사인 유사도 (M3). 학습이 필요 없다. 점수는 확률이 아니라 보정이 필요하다."""
    prob_output = False

    def __init__(self, model_name, query_prefix='', doc_prefix='', descriptions=None):
        self.enc = _Encoder(model_name, query_prefix)
        self.doc_enc = _Encoder(model_name, doc_prefix)
        self.descriptions = descriptions or [DESCRIPTIONS[t] for t in TOOLS]   # 라벨 순서와 같은 순서

    def fit(self, df, Y):
        assert len(self.descriptions) == Y.shape[1]
        self.tool_emb = self.doc_enc.encode(self.descriptions)
        return self

    def scores(self, df):
        return self.enc.encode(_encoder_input(df, False)) @ self.tool_emb.T


def make(spec):
    """실험 설정(dict) → 모델."""
    m = spec['model']
    if m == 'r0':
        return R0()
    if m in ('lr', 'svm'):
        return Linear(spec['feat'], kind=m, C=spec.get('C', 1.0), class_weight=spec.get('class_weight'))
    if m == 'embed_lr':
        return EmbedLR(spec['encoder'], spec.get('query_prefix', ''), spec.get('with_resource', True), spec.get('C', 1.0), spec.get('class_weight'))
    if m == 'embed_sim':
        return EmbedSim(spec['encoder'], spec.get('query_prefix', ''), spec.get('doc_prefix', ''), spec.get('descriptions'))
    raise ValueError(m)
