"""Place every frozen corpus case once into capacity-10 mixed upload sessions."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
folder = ROOT / 'data/document-benchmark/v1'
source = folder / 'manifest.json'
target = folder / 'manifest-corpus-mixed.json'
if target.exists():
    raise RuntimeError('전체 코퍼스 혼합 묶음 명세가 이미 고정됐습니다.')
original = json.loads(source.read_text())
cases = original['cases']
count = (len(cases) + 9) // 10
bins = [[] for _ in range(count)]
priority = {'csv': 0, 'pdf': 1, 'xlsx': 2, 'json': 3, 'txt': 4, 'md': 5}
for index, case in enumerate(sorted(cases, key=lambda c: (priority[c['format']], c['id']))):
    bins[index % count].append(dict(corpus_id=case['id']))
assert all(1 <= len(items) <= 10 for items in bins)
flattened = [item['corpus_id'] for items in bins for item in items]
assert len(flattened) == len(set(flattened)) == len(cases)
manifest = {**original,
    'parent_manifest_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'bundle_policy': 'all_frozen_cases_once_round_robin_by_format_capacity_10',
    'bundles': [dict(id=f'corpus-mixed-{index + 1:02d}', cases=items) for index, items in enumerate(bins)]}
target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(dict(manifest=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                      cases=len(cases), bundles=len(bins), uploads_both_arms=len(cases) * 2), ensure_ascii=False))
