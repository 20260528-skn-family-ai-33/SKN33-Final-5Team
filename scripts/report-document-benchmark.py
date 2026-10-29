"""Write the Korean report from completed remote benchmark evidence."""
import argparse
import collections
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
from zoneinfo import ZoneInfo


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def number(value):
    return f'{value:.4f}' if isinstance(value, (int, float)) and math.isfinite(value) else '해당 없음'


def mean(values):
    return statistics.mean(values) if values else None


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(map(str, headers)) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(map(str, row)) + ' |' for row in rows])


def preservation_rate(score):
    detail = score.get('preservation', {})
    if 'expected_cells' in detail:
        return detail['exact_cells'] / detail['expected_cells']
    if 'expected_tokens' in detail:
        return detail['exact_tokens'] / detail['expected_tokens']
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--primary', type=Path, required=True)
    parser.add_argument('--mixed', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = read(args.manifest)
    corpus = args.manifest.resolve().parent
    cases = {c['id']: c for c in manifest['cases']}
    runs = [args.primary.resolve(), args.mixed.resolve()]
    assert all(read(run / 'summary.json')['complete'] for run in runs), '모든 실행을 마친 뒤 보고서를 생성하세요.'
    records = [r for run in runs for r in read(run / 'progress.json')]
    assert len(records) == 930, '예정된 930회 업로드 기록이 모두 있어야 합니다.'
    qualities = [s for run in runs for s in read(run / 'scores/file-quality.json')]
    primary_quality = [s for s in qualities if s['bundle_id'].startswith('single-')]
    reference = {(s['arm'], s['corpus_id']): s for s in primary_quality}
    primary_records = {(r['arm'], r['corpus_id']): r for r in records if r['bundle_id'].startswith('single-')}
    bundles = [b for run in runs for b in read(run / 'bundles.json')]
    primary_summary, mixed_summary = [read(run / 'scores/summary.json') for run in runs]
    start = min(read(run / 'summary.json')['started_at'] for run in runs)
    end = max(read(run / 'summary.json')['completed_at'] for run in runs)
    time_label = lambda value: datetime.fromtimestamp(value, ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S KST')
    rows = []
    for arm in ['A', 'B']:
        selected = [r for r in records if r['arm'] == arm]
        positive = [r for r in selected if not r['expected_failure']]
        rows.append([arm, len(selected), sum(r['status'] == 'succeeded' for r in positive), len(positive),
                     sum(r['expected_rejection_observed'] for r in selected), sum(r.get('parser_status') == 'partial' for r in selected),
                     number(sum(r['upload_elapsed_seconds'] for r in selected))])
    sections = ['# RunPod 문서 분석·라우팅 A/B 검증 보고서',
        f'실행: {time_label(start)} ~ {time_label(end)}. 새 전용 L40S Pod에서 단일 파일과 묶음 입력 총 930회를 실제 HTTP 업로드로 실행했다.',
        '## 구성과 완료 범위',
        '- A: 프로젝트의 자동 구조 조사·파서 라우팅. 사람의 후보 채택은 제외했다.\n'
        '- B: Docling과 PaddleOCR를 항상 시도하는 고정 구성. Docling 본문·표를 기본으로 하고 중복되지 않는 OCR 문자열을 추가했다.\n'
        '- 동일한 원본·정답, 같은 Pod, 파일 동시 처리 1, 분석 프로세스 300초·HTTP 클라이언트 330초·파일 8MiB 제한을 양쪽에 적용했다.\n'
        '- 공개 PDF 18페이지 + 공개 CSV 100개 + 독립 정답 보존 시험 60개 + 손상 PDF 1개.\n'
        '- 단일 179개, 기본 묶음 16종, 전체 코퍼스 혼합 묶음 18종을 양쪽에서 실행했다.\n'
        '- 업로드·저장 성공, 파서 경고·일부 실패, 내용 정확도를 분리해 기록했다.',
        table(['구성', '업로드 시도', '정상 입력 업로드 성공', '정상 입력 분모', '손상 입력 거절', 'partial 표시', '업로드 시간 합계(초)'], rows),
        '시간 합계는 파일별 변환·파싱·OCR·병합·저장·업로드 응답 시간을 포함한다. 환경 설치와 모델 전송, 원본 다운로드 대조는 제외한다. '
        '`partial`은 오류뿐 아니라 수식 계산값 부재 같은 검토 사유도 포함하므로 정확도 점수로 해석하지 않는다.',
        '## PDF: OmniDocBench 공식 지표',
        'v1_0 데이터의 981페이지 모집단에서 문서 유형 9종별 2페이지를 seed 20261028로 추출했다. 공식 제공 페이지별 PDF를 그대로 양쪽에 넣었다. '
        '실패도 빈 예측으로 남겼으며 공식 매칭·정규화 코드를 사용했다. Edit Distance는 낮을수록, TEDS는 높을수록 좋다. CDM 수식 점수는 측정하지 않았다.']
    pdf_rows = []
    for cohort, summary in [('단일', primary_summary), ('전체 혼합', mixed_summary)]:
        for arm in ['A', 'B']:
            values = summary['omnidocbench'][arm]
            edit = lambda category: number(values.get(category, {}).get('Edit_dist', {}).get('ALL_page_avg'))
            pdf_rows.append([cohort, arm, edit('text_block'), number(values.get('table', {}).get('TEDS', {}).get('all')),
                             edit('table'), edit('reading_order'), values.get('table', {}).get('sample_count', 0)])
    sections.append(table(['입력', '구성', '본문 Edit Distance', 'TEDS', '표 Edit Distance', '읽기 순서 Edit Distance', '표 표본 수'], pdf_rows))
    language = collections.Counter(g['page_info']['page_attribute']['language'] for g in read(corpus / 'gold/omni-selected.json'))
    sections.append('선택 PDF의 언어 구성: ' + ', '.join(f'{key} {value}페이지' for key, value in sorted(language.items())) + '. '
                    '18페이지 표본 결과이며 논문 전체 데이터의 결과나 한국어 실무 문서 전반의 성능으로 일반화하지 않는다.')
    sections += ['## CSV: Pollock 100개',
        '공식 `header_record_cell_measures_csv`를 그대로 호출했다. HTTP preview가 아닌 실제 SQLite의 전체 행을 CSV로 저장해 정답과 대조했다. '
        '실패는 0점으로 포함했다. A/B가 같은 서비스 CSV 로더를 공유하므로 아래 지표는 공통 로더와 전체 경로의 검증이며 OCR 교체 효과를 뜻하지 않는다.']
    csv_rows = []
    for cohort, scores in [('단일', primary_quality), ('전체 혼합', [s for s in qualities if s['bundle_id'].startswith('corpus-mixed-')])]:
        for arm in ['A', 'B']:
            selected = [s for s in scores if s['arm'] == arm and s['suite'] == 'pollock']
            success = [s for s in selected if s['upload_status'] == 'succeeded']
            csv_rows.append([cohort, arm, f'{len(success)}/{len(selected)}'] +
                            [number(mean([s['metrics'][key] for s in selected])) for key in ['header_f1', 'record_f1', 'cell_f1']] +
                            [number(mean([s['metrics']['cell_f1'] for s in success]))])
    sections.append(table(['입력', '구성', '업로드 성공', '헤더 F1(전체)', '행 F1(전체)', '셀 F1(전체)', '셀 F1(성공만)'], csv_rows))
    sections.append('CSV 1개(POLLOCK-070)는 8MiB 제한 초과로 양쪽 모두 거절하는 대상이며, 전체 100개 분모에서 제외하지 않았다. '
                    '행 F1은 Pollock의 공식 정규화·행 결합 정의를 따른다. 원래 문자열·선행 0 보존은 아래 별도 정답 시험으로도 확인했다.')
    different_csv = [identifier for identifier, case in cases.items() if case['suite'] == 'pollock'
                     and primary_records[('A', identifier)]['status'] != primary_records[('B', identifier)]['status']]
    if different_csv:
        sections.append('단일 입력에서 업로드 성공 여부가 달랐던 CSV의 실제 오류:\n\n' + '\n'.join(
            '- ' + identifier + ': ' + '; '.join(arm + ' ' + primary_records[(arm, identifier)]['status'] +
                (' — ' + primary_records[(arm, identifier)]['error'] if primary_records[(arm, identifier)].get('error') else '')
                for arm in ['A', 'B']) for identifier in different_csv))
    sections += ['## 독립 정답 보존 시험',
        '파일을 만들기 전에 정답을 고정했다. CSV·JSON·XLSX는 값과 자료형의 셀 일치율, TXT·MD·자체 PDF는 지정 문자열의 보존율이다. '
        '서로 다른 정의의 값을 합쳐 종합점수를 만들지 않았다.']
    preservation_rows = []
    for fmt in ['csv', 'xlsx', 'json', 'txt', 'md', 'pdf']:
        for arm in ['A', 'B']:
            selected = [s for s in primary_quality if s['arm'] == arm and s['suite'] == 'preservation' and s['format'] == fmt]
            preservation_rows.append([fmt.upper(), arm, len(selected), number(mean([preservation_rate(s) for s in selected])),
                                      sum(s['preservation'].get('exact_rows', s['preservation'].get('literal_text_match', False)) for s in selected),
                                      sum(s['preservation'].get('schema_match', False) for s in selected) if fmt in ['csv', 'xlsx', 'json'] else '해당 없음',
                                      sum(s['upload_status'] == 'succeeded' for s in selected)])
    sections.append(table(['형식', '구성', '파일 수', '값·자료형 또는 지정 문자열 일치율', '전체 행 또는 원문 일치 파일', '열 구조 일치 파일', '업로드 성공'], preservation_rows))
    sections.append('전체 행 일치는 정답 행 목록의 순서·값 동등 비교이며 자료형은 셀 일치율에서 별도로 검사했다. 원문 일치는 원문 전체 문자열의 포함 여부다. '
                    '지정 문자열이 모두 남아 있어도 공백·줄바꿈·추가 행 때문에 전체 일치 검사는 실패할 수 있으므로 함께 제시했다.')
    structural = []
    for arm in ['A', 'B']:
        selected = [s['preservation']['structure'] for s in primary_quality if s['arm'] == arm and s['suite'] == 'preservation' and s['format'] == 'xlsx']
        structural.append([arm] + [f'{sum(v[field] for v in selected)}/{len(selected)}' for field in ['sheet_names', 'merged', 'formula', 'audit_string']])
    sections += [table(['구성', '시트 출처', '병합 범위', '수식 구조', '다른 시트의 선행 0 문자열'], structural),
        '위 구조 검사는 공통 셀 로더와 별도로 선택된 문서 분석 결과에서 원본 시트·셀 연결과 수식을 확인한다. '
        'A의 수식 캐시 부재 경고와 B의 구조 증거 부재를 구분해야 한다. 이 서비스가 노출한 결과 계약의 검사이며 Docling 원본 출력의 모든 기능을 평가한 결과는 아니다. '
        'StatSheets의 셀 역할·표 범위 점수는 이번 측정에 포함하지 않았다.',
        '## B군의 두 엔진 실행 확인']
    engine_rows = []
    for label, selected in [('단일', [r for r in records if r['bundle_id'].startswith('single-')]),
                            ('기본 묶음', [r for r in read(args.primary / 'progress.json') if not r['bundle_id'].startswith('single-')]),
                            ('전체 혼합', read(args.mixed / 'progress.json'))]:
        selected = [r for r in selected if r['arm'] == 'B']
        engine_rows.append([label, len(selected), sum(r.get('raw_attempt_count', 0) > 0 for r in selected),
                            sum(r.get('mandatory_both_attempted') is True for r in selected),
                            sum(r.get('mandatory_both_succeeded') is True for r in selected)])
    sections += [table(['입력', 'B 업로드 시도', '분석 엔진 호출 도달', 'Docling·PaddleOCR 모두 호출', '분석 결과에서 양쪽 성공 확인'], engine_rows),
        '파일 크기·헤더·행 검증 또는 변환 준비에서 거절된 입력은 엔진 호출에 도달하지 못할 수 있다. '
        '이 실패도 전체 업로드와 정확도 분모에 남겼다. 분석에 도달한 B는 Docling 결과와 관계없이 PaddleOCR를 추가로 시도한다. '
        '성공 확인은 서비스 분석 결과에 남은 경로 상태를 기준으로 하며, 결과 크기 초과로 그 결과가 없으면 성공 확인 수에 더하지 않았다. '
        'Docling 자체 EasyOCR 설정과 추가 PaddleOCR 실행을 포함한 고정 구성을 비교했다.',
        '## 묶음 처리와 출처 보존']
    sections.append(table(['묶음', '구성', '입력', '업로드 성공', '누락', '중복', '출처 오연결', '실패 응답 뒤 저장된 자산', '세션 조회', '업로드 시간 합계(초)', '검증 포함 전체(초)'],
                          [[b['id'], b['arm'], b['requested'], b['succeeded'], b['missing'], b['duplicates'], b['source_mismatches'],
                            b.get('stored_without_success_response', 0), '성공' if b.get('session_api_ok', True) else '실패·DB 대조',
                            number(sum(r['upload_elapsed_seconds'] for r in records if r['bundle_id'] == b['id'] and r['arm'] == b['arm'])),
                            number(b['elapsed_seconds'])]
                           for b in bundles if not b['id'].startswith('single-')]))
    sections.append('누락은 성공 응답에 결과 자산이 없거나 저장된 성공 결과가 세션 조회에서 사라진 경우다. '
                    '거절·실패한 업로드는 성공률과 정확도 분모에 포함해 별도로 집계했다. '
                    '응답이 실패했어도 DB에 자산이 남은 사례는 별도 열로 표시한다. '
                    '업로드 시간 합계는 HTTP 응답까지의 시간이고, 검증 포함 전체 시간에는 원본 다운로드 해시 대조·전체 행 증거 저장·세션 조회도 포함된다.')
    delta_rows = []
    for arm in ['A', 'B']:
        comparisons, changed = [], 0
        for record in records:
            if record['arm'] != arm or record['bundle_id'].startswith('single-'):
                continue
            baseline = primary_records.get((arm, record['corpus_id']))
            if baseline and record['status'] == baseline['status'] == 'succeeded':
                comparisons.append(record)
                changed += record.get('semantic_sha256') != baseline.get('semantic_sha256')
        deltas = []
        for score in qualities:
            if score['arm'] != arm or score['bundle_id'].startswith('single-'):
                continue
            baseline = reference.get((arm, score['corpus_id']))
            rate, original_rate = preservation_rate(score), preservation_rate(baseline or {})
            if rate is not None and original_rate is not None:
                deltas.append(rate - original_rate)
        delta_rows.append([arm, len(comparisons), changed, number(mean(deltas))])
    sections += [table(['구성', '단일·묶음 모두 성공한 비교 수', '본문·행·표 의미 해시 변화', '보존 시험 일치율 변화 평균'], delta_rows),
        '의미 해시는 실행 시간·자산 ID를 빼고 전체 행, 열, 본문과 표 HTML·문자열에서 계산했다. '
        '읽기 순서나 공백의 변화도 달라진 결과로 기록하며, 해시 변화 자체를 정확도 하락으로 단정하지 않는다. '
        '공개 PDF·CSV의 단일/혼합 정확도 변화는 앞의 같은 지표 표를 비교한다.',
        '## 재현 자료와 해석 범위',
        '- `progress.json`: 실패와 예상 거절을 포함한 파일별 전체 기록.\n'
        '- `files/<instance>/asset.json`, `rows.json`, `analysis.json`: 실제 업로드·DB·분석 증거.\n'
        '- `service-data/document-results`: 엔진 요청·응답·원본 출력·로그.\n'
        '- `scores/file-quality.json`, `omnidocbench-official.json`: 원래 정밀도의 지표.\n'
        '- 코퍼스 manifest, 소스 배포 manifest, 모델 아카이브 해시와 6개 실행 환경 freeze를 별도 보관했다.\n'
        '- 사람의 채택·보류·검토 완료를 실행하지 않았다. 원본 다운로드 해시도 각 성공 건에서 대조했다.',
        '초기 환경 확인에서 MinerU VLM의 Vulkan 초기화 실패와 CPU 폴백을 발견했다. 해당 초기 실행은 중지하고 '
        '`INVALID_ENVIRONMENT.json`으로 표시해 전부 본 비교에서 제외했다. NVIDIA EGL ICD와 명시적 Vulkan 설정으로 '
        '같은 GGUF 모델의 실제 GPU 추론을 확인한 뒤 동일 입력·정답으로 본 실행을 새로 시작했다. '
        '호스트 드라이버나 모델 가중치는 변경하지 않았다.',
        '이 보고서는 실제 서비스 조건에서 두 구현을 비교한 결과다. 입력 형식·인코딩·행 수·출력 크기 제한, '
        '원문 렌더링과 B의 고정 병합 규칙을 포함한다. 개별 도구의 모든 설정이나 논문 전체 코퍼스에 대한 순위를 뜻하지 않는다.',
        '자료: [OmniDocBench](https://github.com/opendatalab/OmniDocBench), [공개 데이터](https://huggingface.co/datasets/opendatalab/OmniDocBench), '
        '[Pollock](https://github.com/HPI-Information-Systems/Pollock).']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('\n\n'.join(sections) + '\n', encoding='utf-8')
    print(json.dumps(dict(report=str(args.output), bytes=args.output.stat().st_size,
                          sha256=hashlib.sha256(args.output.read_bytes()).hexdigest()), ensure_ascii=False))


if __name__ == '__main__':
    main()
