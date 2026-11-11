"""Render the measured arms and matched controls; requires no inference or connectors."""
import argparse
from pathlib import Path
import runpy
from statistics import median


DIR = Path(__file__).resolve().parent
C = runpy.run_path(str(DIR / 'compare.py'))
ARMS = ('BASE', 'PLANNER', 'CAPABILITY', 'TABLE', 'COMBINED')


def num(seconds, signed=False):
    if seconds is None:
        return '미기록'
    return format(seconds, '+.2f' if signed else '.2f')


def table(headers, rows):
    return ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers),
            *['| ' + ' | '.join(str(v) for v in row) + ' |' for row in rows]]


def report(directory):
    gold = C['load'](C['FROZEN'])
    corpus = {case['id']: case for case in gold['cases']}
    sha = C['digest']({k: v for k, v in gold.items() if k != 'gold_sha256'})
    C['require'](sha == gold['gold_sha256'], 'Frozen corpus digest mismatch')
    raw, scores, result, pending = {}, {}, {}, []
    for arm in ARMS:
        path = directory / (arm + '.json')
        if not path.exists():
            pending.append(arm)
            continue
        run = C['load'](path)
        if run['status'] != 'completed':
            pending.append(f"{arm} ({run['status']})")
            continue
        raw[arm] = run
        scores[arm] = C['validate'](run, corpus, sha)
        C['require'](list(scores[arm]) == ['H01', 'H21', 'H32'], 'Expected H01, H21, H32 in order')
        result[arm] = C['compare'](raw['BASE'], run, scores['BASE'], scores[arm])['candidate']
    C['require']('BASE' in result and all(x == 'pass' for x in scores['BASE'].values()), 'Incomplete baseline')
    lines = ['## 독립 후보 및 조합 실측', '',
             'BASE는 안정본, PLANNER는 모든 요청에 적용한 Planner 후보, TABLE은 표 요청으로 범위를 좁힌 Planner 후보이다. '
             'CAPABILITY는 기존 계약으로 전체 작업을 확인할 수 있을 때의 지원 판단 후보, COMBINED는 TABLE과 CAPABILITY를 함께 적용한 후보이다.','']
    if pending:
        lines.extend(['집계 대기 중인 실행은 ' + ', '.join(pending) + '이다.', ''])
    cohort, details, turns = [], [], []
    for arm, cases in result.items():
        vals = cases.values()
        cohort.append([arm, f"{sum(c['oracle'] == 'pass' for c in vals)}/3",
                       num(median(c['scenario_wall_s'] for c in vals)),
                       *[f"{sum(c[k] for c in vals):,}" for k in ('calls', 'input_tokens', 'output_tokens')]])
        for cid, case in cases.items():
            details.append([arm, cid, case['oracle'], num(case['scenario_wall_s']), case['calls'],
                            f"{case['input_tokens']:,}", f"{case['output_tokens']:,}",
                            f"{num(case['measured_load_s'])} ({case['load_samples']})"])
        for t in cases['H21']['user_turns']:
            turns.append([arm, f"{t['turn']+1} · {t['route']}", num(t['wall_s']), num(t['gap_to_30_s'], True),
                          t['calls'], f"{t['input_tokens']:,}", f"{t['output_tokens']:,}",
                          f"{num(t['measured_load_s'])} ({t['load_samples']})"])
    lines += table(['실험','동결 의미 판정','선별 3건 전체 시간 p50(초)','호출','입력 토큰','출력 토큰'], cohort)
    lines += ['', *table(['실험','사례','의미 판정','전체 초','호출','입력 토큰','출력 토큰','관측 로드 초 (표본 수)'], details)]
    lines += ['', '### H21 사용자 발화별 지연', '',
              '첫 턴은 정책 질문이 반환될 때까지, 둘째 턴은 사용자의 정책 선택 후 검증된 워크플로우가 반환될 때까지 재었다. '
              '30초 차이의 양수는 목표 초과, 음수는 목표 이내를 뜻한다. 사용자의 답변 대기는 제외했다.', '',
              *table(['실험','턴 · 분기','응답 초','30초 차이','호출','입력 토큰','출력 토큰','관측 로드 초 (표본 수)'], turns)]
    lines.extend(['','### 요인별 대조','',
                  '아래 차이는 우측 후보에서 좌측 후보의 값을 뺀 결과다. '
                  '대상 단계의 토큰 및 호출 수가 같고 다른 단계의 추론량도 같다면, 한 번의 시간 차이만으로 최적화 효과를 산정할 수 없다.', ''])
    rows = []
    pairs = [('BASE', 'TABLE', 'generation'), ('BASE', 'CAPABILITY', 'capability_check'),
             ('CAPABILITY', 'COMBINED', 'generation'), ('TABLE', 'COMBINED', 'capability_check')]
    for first, second, stage in pairs:
        if first not in result or second not in result:
            continue
        C['compare'](raw[first], raw[second], scores[first], scores[second])
        for cid in result[first]:
            left, right = result[first][cid], result[second][cid]
            a, b = left['stages'].get(stage, {}), right['stages'].get(stage, {})
            ld, rd = left['measured_load_s'], right['measured_load_s']
            rows.append([f'{first} → {second}',cid,stage,
                         num(right['scenario_wall_s'] - left['scenario_wall_s'], True),
                         num(b.get('wall_s', 0) - a.get('wall_s', 0), True),
                         *[format(b.get(key, 0) - a.get(key, 0), '+,d') for key in ('calls','input_tokens','output_tokens')],
                         num(rd - ld if ld is not None and rd is not None else None, True)])
    lines += table(['대조','사례','대상 단계','전체 초 차이','단계 초 차이','단계 호출 차이',
                    '단계 입력 차이','단계 출력 차이','사례 관측 로드 초 차이'], rows)
    lines.extend(['', 'CAPABILITY → COMBINED는 지원 판단을 고정하고 표 Planner의 추가 효과를 본다. H32는 이 표 경로를 사용하지 않는다. '
                  'TABLE → COMBINED는 표 Planner를 고정하고 지원 판단의 추가 효과를 본다. H01과 H21은 이 문서 전달 경로를 사용하지 않는다. '
                  '해당 대조의 변경 범위 밖 사례도 함께 제시했다.', '',
                  '모델 로드는 네이티브 응답에서 반환한 값만 합산했다. Planner SDK 응답의 서버 내부 로드는 미기록이다. '
                  '로드와 모델 호출 시간은 사용자 지연에 포함된다. 단순 차감 값을 사용자 체감 시간으로 해석할 수 없다. '
                  'p50은 세 사례 전체 시간의 중앙값이며 턴 중앙값이나 전체 서비스 중앙값으로 확대하지 않는다.', '',
                  '아래 명령은 이미 저장된 원본을 읽어 표를 다시 만든다. 각 후보와 기준본의 단계별 wall, 토큰, '
                  '모델 로드 관측 횟수, 앱 파일 해시 변화, 턴별 시간을 보려면 같은 폴더의 compare.py를 쓴다.', '',
                  '```powershell', "$experiment = 'runtime/planner-capability-9b-experiment'",
                  '& $worker "$experiment/render-results.py"',
                  '& $worker "$experiment/compare.py" "$experiment/CAPABILITY.json" "$experiment/COMBINED.json"',
                  '& $worker "$experiment/compare.py" "$experiment/TABLE.json" "$experiment/COMBINED.json"', '```'])
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DIR)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    markdown = report(args.directory)
    if args.output:
        args.output.write_text(markdown, encoding='utf-8')
    else:
        print(markdown)
