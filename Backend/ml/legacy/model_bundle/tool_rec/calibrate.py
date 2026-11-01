"""확률 보정 (계획서 8장). 도구마다 따로 맞춘다.

- platt: 점수 하나를 입력으로 하는 로지스틱 회귀. 데이터가 적어도 안정적이라 기본값.
- isotonic: 단조 증가 계단 함수. 양성이 적은 도구는 platt으로 대신한다.
- none: 점수를 그대로 둔다. 확률이 아닌 점수(svm, r0, embed_sim)는 brier, ece를 계산하지 않는다.

반드시 OOF 점수로 맞추고(fit), 평가 세트에는 transform만 한다.
"""
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

MIN_POS_ISOTONIC = 200


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


class Calibrator:
    def __init__(self, method='platt', prob_input=True):
        self.method, self.prob_input = method, prob_input

    def _feat(self, s):
        return _logit(s) if self.prob_input else s

    def fit(self, scores, Y):
        self.parts = []
        for j in range(Y.shape[1]):
            s, y = self._feat(scores[:, j]), Y[:, j]
            if self.method == 'none' or y.min() == y.max():
                self.parts.append(('const', y.mean()) if y.min() == y.max() and self.method != 'none' else ('none', None))
                continue
            if self.method == 'isotonic' and y.sum() >= MIN_POS_ISOTONIC:
                self.parts.append(('iso', IsotonicRegression(out_of_bounds='clip', y_min=0, y_max=1).fit(s, y)))
            else:
                self.parts.append(('platt', LogisticRegression(C=1e4).fit(s.reshape(-1, 1), y)))
        return self

    def transform(self, scores):
        out = np.zeros_like(scores, dtype=float)
        for j, (kind, obj) in enumerate(self.parts):
            s = self._feat(scores[:, j])
            if kind == 'none':
                out[:, j] = scores[:, j]
            elif kind == 'const':
                out[:, j] = obj
            elif kind == 'iso':
                out[:, j] = obj.predict(s)
            else:
                out[:, j] = obj.predict_proba(s.reshape(-1, 1))[:, 1]
        return out
