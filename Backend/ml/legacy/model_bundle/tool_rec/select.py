"""후보 고르기 (계획서 9장).

방식
- topk: 점수 상위 k개 (Yang 2001의 RCut)
- global: 확률 ≥ t (공통 기준값)
- per_label: 도구별 기준값 t_j (SCut). 양성이 적은 도구는 공통 t를 쓴다.
- hybrid: 확률 ≥ t + 최소 min_k개 보장, 최대 max_k개

기준값은 OOF로만 고른다(choose_*). 평가 세트에는 고른 값을 그대로 쓴다.
후처리: registry PORTS 입력 규칙으로 짝 도구를 넣는다. 완료 안내는 항상 넣는다(지표에는 안 셈).
"""
import numpy as np

from .labels import BASE_PRODUCER, IDX, REQUIRES, TOOLS
from . import metrics

GRID = np.round(np.arange(0.05, 0.96, 0.05), 2)


def candidates(probs, method, k=5, t=0.5, t_per_label=None, min_k=3, max_k=8):
    out = []
    for p in probs:
        order = list(np.argsort(-p, kind='stable'))
        if method == 'topk':
            c = order[:k]
        elif method == 'global':
            c = [j for j in order if p[j] >= t]
        elif method == 'per_label':
            c = [j for j in order if p[j] >= t_per_label[j]]
        elif method == 'hybrid':
            c = [j for j in order if p[j] >= t][:max_k]
            c += [j for j in order if j not in c][:max(0, min_k - len(c))]
        else:
            raise ValueError(method)
        out.append([int(j) for j in c])
    return out


def postprocess(cands, kinds):
    """입력이 필요한 도구가 후보에 있는데 그 입력이 처음 생기는 도구가 없으면 넣는다."""
    out = []
    for c, kind in zip(cands, kinds):
        c = list(c)
        names = {TOOLS[j] for j in c}
        for tool in list(names):
            need = REQUIRES.get(tool)
            if not need:
                continue
            base, needs_kind = BASE_PRODUCER[need]
            ok = needs_kind is None or kind in (needs_kind, 'both')
            if ok and base not in names:
                c.append(IDX[base])
                names.add(base)
        out.append(c)
    return out


def fallback(cands, probs, tau_low):
    """가장 높은 확률조차 tau_low 미만이면 전체 도구를 준다."""
    used = np.array([p.max() < tau_low for p in probs])
    full = list(range(len(TOOLS)))
    return [full if u else c for c, u in zip(cands, used)], used.mean()


def choose_global_t(probs, Y, max_avg=None):
    """OOF에서 공통 기준값 고르기: 평균 후보 수 제한 안에서 COMP 최대, 동점이면 후보가 적은 쪽.
    제한이 없으면 기준값을 0에 가깝게 내려 '전부 넣기'가 이기므로 max_avg를 꼭 준다."""
    best = None
    for t in GRID:
        c = candidates(probs, 'global', t=t)
        comp, size = metrics.comp_set(c, Y), metrics.avg_size(c)
        if max_avg is not None and size > max_avg:
            continue
        key = (comp, -size)
        if best is None or key > best[0]:
            best = (key, float(t))
    return best[1] if best else float(GRID[-1])


def choose_per_label_t(probs, Y, global_t, min_pos=30, beta=2.0):
    """도구마다 F2가 가장 높은 기준값. 양성이 min_pos 미만이면 공통 기준값."""
    ts = np.full(Y.shape[1], global_t)
    for j in range(Y.shape[1]):
        if Y[:, j].sum() < min_pos:
            continue
        best = max(GRID, key=lambda t: (metrics.fbeta(probs[:, j] >= t, Y[:, j], beta), -t))
        ts[j] = best
    return ts
