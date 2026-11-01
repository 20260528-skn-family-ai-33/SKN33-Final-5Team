"""데이터 읽기. 한 줄에 요청 하나인 jsonl.

{"id": "d1-0001", "source": "d1", "group": "P03",
 "request": "유형별 건수 세서 CSV로 저장해 줘",
 "resource": {"kind": "table", "formats": ["csv"], "columns": ["유형", "처리상태"]},
 "labels_required": ["read_table", "aggregate_table", "write_result"],
 "labels_allowed": [], "category": "table", "exclude": false}

resource.kind: table / document / both / none
"""
import json

import numpy as np
import pandas as pd

from .labels import TOOLS, to_vec

KINDS = ['table', 'document', 'both', 'none']


def load_jsonl(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            for key in ('id', 'request', 'resource', 'labels_required'):
                if key not in r:
                    raise ValueError(f'{path}:{n} {key} 없음')
            unknown = set(r['labels_required']) | set(r.get('labels_allowed', []))
            unknown -= set(TOOLS) | {'finish', 'saved_workflow'}
            if unknown:
                raise ValueError(f'{path}:{n} 모르는 도구 {sorted(unknown)}')
            if r['resource'].get('kind') not in KINDS:
                raise ValueError(f'{path}:{n} resource.kind는 {KINDS} 중 하나')
            rows.append(r)
    return [r for r in rows if not r.get('exclude')]


def to_frame(rows):
    """모델 입력용 표와 정답 행렬(필수 Y, 허용 A)."""
    df = pd.DataFrame({
        'id': [r['id'] for r in rows],
        'request': [r['request'] for r in rows],
        'kind': [r['resource']['kind'] for r in rows],
        'columns_text': [' '.join(r['resource'].get('columns', [])) for r in rows],
        'group': [r.get('group') or r['id'] for r in rows],
    })
    Y = np.array([to_vec(r['labels_required']) for r in rows])
    A = np.array([to_vec(r.get('labels_allowed', [])) for r in rows])
    return df, Y, A


def combo_strings(Y, min_count):
    """층화용 조합 문자열. 드문 조합은 rare로 합친다."""
    combos = [''.join(map(str, row)) for row in Y]
    counts = pd.Series(combos).value_counts()
    return np.array([c if counts[c] >= min_count else 'rare' for c in combos])
