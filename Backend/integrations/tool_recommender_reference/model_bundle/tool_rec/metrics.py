"""지표 (계획서 7장).

순위 지표 (점수로 상위 k개를 볼 때)
- comp_at_k: 필수 도구가 전부 상위 k개 안에 든 요청의 비율 (COLT의 COMP@K)
- recall_at_k, precision_at_k, ndcg_at_k
- k='N'이면 요청마다 필수 도구 수 |N|을 k로 쓴다 (COLT의 COMP@|N|)

집합 지표 (기준값으로 고른 후보 목록을 볼 때)
- comp_set, recall_set, precision_set, avg_size

보정 지표: brier, ece
Y는 필수 도구, A는 허용 도구. 정밀도는 필수 또는 허용이면 맞은 것으로 센다.
"""
import numpy as np


def _topk(scores, k_row):
    return [list(np.argsort(-s, kind='stable')[:k]) for s, k in zip(scores, k_row)]


def _k_row(Y, k):
    return Y.sum(axis=1).astype(int) if k == 'N' else np.full(len(Y), int(k))


def comp_at_k(scores, Y, k):
    return comp_set(_topk(scores, _k_row(Y, k)), Y)


def recall_at_k(scores, Y, k):
    return recall_set(_topk(scores, _k_row(Y, k)), Y)


def precision_at_k(scores, Y, A, k):
    return precision_set(_topk(scores, _k_row(Y, k)), Y, A)


def ndcg_at_k(scores, Y, k):
    vals = []
    for s, y in zip(scores, Y):
        top = np.argsort(-s, kind='stable')[:k]
        dcg = sum(y[j] / np.log2(r + 2) for r, j in enumerate(top))
        ideal = sum(1 / np.log2(r + 2) for r in range(min(int(y.sum()), k)))
        vals.append(dcg / ideal if ideal else 0.0)
    return float(np.mean(vals))


def comp_set(cands, Y):
    return float(np.mean([all(j in c for j in np.flatnonzero(y)) for c, y in zip(cands, Y)]))


def comp_flags(cands, Y):
    """요청마다 성공(1)/실패(0). 부트스트랩 비교에 쓴다."""
    return np.array([int(all(j in c for j in np.flatnonzero(y))) for c, y in zip(cands, Y)])


def recall_set(cands, Y):
    hit = sum(sum(1 for j in np.flatnonzero(y) if j in c) for c, y in zip(cands, Y))
    return float(hit / max(Y.sum(), 1))


def precision_set(cands, Y, A):
    ok = np.maximum(Y, A)
    vals = [np.mean([ok[i, j] for j in c]) if c else 0.0 for i, c in enumerate(cands)]
    return float(np.mean(vals))


def avg_size(cands):
    return float(np.mean([len(c) for c in cands]))


def fbeta(pred, y, beta=2.0):
    tp = float(np.sum(pred & (y == 1)))
    fp = float(np.sum(pred & (y == 0)))
    fn = float(np.sum(~pred & (y == 1)))
    if tp == 0:
        return 0.0
    p, r = tp / (tp + fp), tp / (tp + fn)
    return (1 + beta ** 2) * p * r / (beta ** 2 * p + r)


def per_label(cands, Y):
    """도구별 정밀도, 재현율, F1, 양성 수."""
    n = Y.shape[1]
    pred = np.zeros_like(Y)
    for i, c in enumerate(cands):
        pred[i, c] = 1
    rows = []
    for j in range(n):
        tp = int(((pred[:, j] == 1) & (Y[:, j] == 1)).sum())
        fp = int(((pred[:, j] == 1) & (Y[:, j] == 0)).sum())
        fn = int(((pred[:, j] == 0) & (Y[:, j] == 1)).sum())
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        rows.append({'label': j, 'precision': p, 'recall': r, 'f1': f, 'support': int(Y[:, j].sum())})
    return rows


def brier(probs, Y):
    return float(np.mean((probs - Y) ** 2))


def ece(probs, Y, bins=10):
    """도구-요청 쌍 전체를 10구간으로 나눠 '말한 확률'과 '실제 비율'의 차이를 평균한다."""
    p, y = probs.ravel(), Y.ravel()
    idx = np.minimum((p * bins).astype(int), bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(total)
