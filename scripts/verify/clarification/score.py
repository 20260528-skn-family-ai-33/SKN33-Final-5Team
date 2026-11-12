"""Score clarification eval outputs. usage: python score.py a.json [b.json ...]

Metrics are counts "hits/total" where lower is better. Gold labels are drafts (question target
keywords and whether a question is required); a model's wording is not graded.
"""
import json, sys
from collections import defaultdict


def matches(question, keys):
    return any(k in question for k in keys)


def score(path):
    data = json.load(open(path))
    m = defaultdict(lambda: [0, 0])  # metric -> [hits, total]
    by_cat = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    calls = secs = 0
    notes = []
    for case in data['results']:
        pending_required = None
        prev_questions = None
        prev_status = None
        for turn, row in zip(case['turns'], case['rows']):
            g = turn.get('gold', {}); cat = case['cat']
            calls += row.get('model_calls', 0); secs += row.get('seconds', 0)
            qs = row.get('questions') or []
            def add(name, hit):
                m[name][0] += hit; m[name][1] += 1
                by_cat[cat][name][0] += hit; by_cat[cat][name][1] += 1
            if 'error' in row and turn['action'] == 'message':
                add('처리 오류', 1); notes.append(f"{case['id']} 오류: {row['error'][:70]}")
            elif turn['action'] == 'message':
                add('처리 오류', 0)
            if 'ask' in g:
                targets = g['ask']
                unnecessary = [q for q in qs if not any(matches(q, t) for t in targets)]
                add('불필요 질문', int(bool(unnecessary)))
                if unnecessary: notes.append(f"{case['id']} 불필요: {unnecessary}")
                if targets:
                    missing = [t for t in targets if not any(matches(q, t) for q in qs)]
                    add('필요 질문 누락', int(bool(missing)))
                    if missing: notes.append(f"{case['id']} 누락 (상태 {row.get('status') or row.get('error','')[:30]})")
                    pending_required = targets if not missing else None
            if g.get('side'):
                # A side remark must leave the conversation exactly as it was: same status and the
                # same pending questions (same IDs when a question was pending).
                kept = (row.get('status') == prev_status and qs == prev_questions
                        and (not prev_questions or row.get('same_question_ids')))
                add('별도 질문 오분류', int(not kept))
                if not kept: notes.append(f"{case['id']} 별도질문 '{turn['text']}' -> {row.get('status')} {qs}")
            if g.get('must_not_save'):
                add('미응답 저장', int(row.get('saved', 0) > 0))
            if pending_required is not None and not g.get('answer_of') and not g.get('ask'):
                wrong = row.get('status') in ('ready', 'saved') or row.get('saved', 0) > 0
                add('미응답 잘못된 ready·저장', int(wrong))
            if g.get('answer_of'):
                again = [q for q in qs if matches(q, g['answer_of'])]
                add('답변 후 재질문', int(bool(again)))
                if again: notes.append(f"{case['id']} 답변 후 재질문: {again}")
                pending_required = None
            if g.get('no_draft'):
                add('인사·일회성에 초안/모델호출', int(row.get('has_draft', False) or row.get('model_calls', 0) > 0))
            if g.get('must_reach_model'):
                add('업무 요청 차단(오탐)', int(row.get('model_calls', 0) == 0))
            if g.get('expect_decision'):
                add('지원 판정 오답', int(row.get('decision') != g['expect_decision']))
            if 'questions' in row:
                prev_questions = qs
                prev_status = row.get('status')
    groups = defaultdict(set)
    for case in data['results']:
        if case.get('group') and case['rows']:
            row = case['rows'][0]
            groups[case['group']].add((row.get('status'), bool(row.get('questions'))))
    for name, outcomes in groups.items():
        m['같은 뜻 다른 결과'][0] += int(len(outcomes) > 1); m['같은 뜻 다른 결과'][1] += 1
        if len(outcomes) > 1: notes.append(f'표현 묶음 {name}: 결과가 갈림 {sorted(outcomes, key=str)}')
    return data, m, by_cat, calls, secs, notes


if __name__ == '__main__':
    reports = [score(p) for p in sys.argv[1:]]
    names = sorted({k for _, m, _, _, _, _ in reports for k in m})
    print('| 지표 (낮을수록 좋음) | ' + ' | '.join(r[0]['label'] for r in reports) + ' |')
    print('|---|' + '---|' * len(reports))
    for n in names:
        print(f'| {n} | ' + ' | '.join(f"{r[1][n][0]}/{r[1][n][1]}" if r[1][n][1] else '-' for r in reports) + ' |')
    print('| 모델 호출 수 | ' + ' | '.join(str(r[3]) for r in reports) + ' |')
    print('| 총 소요(초, 캐시 재사용 포함) | ' + ' | '.join(f"{r[4]:.0f}" for r in reports) + ' |')
    print('| 실제 실행 시간(초) | ' + ' | '.join(f"{r[0]['stats']['seconds']:.0f}" for r in reports) + ' |')
    for r in reports:
        print('\n#', r[0]['label'], r[0]['model'])
        for cat, mm in sorted(r[2].items()):
            print(' ', cat, {k: f'{v[0]}/{v[1]}' for k, v in mm.items()})
        for note in r[5]: print('  -', note)
