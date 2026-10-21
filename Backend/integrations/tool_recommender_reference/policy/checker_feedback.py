"""K_checker 조건: Deep Agent 설계가 끝나면 ML 확률과 비교해 확인 요청을 한 번 보낸다(10/20 Claude 추가).

- 경고 기준은 ML_도구추천/models/tier_thresholds.json 그대로(필요 ≥ high, 제외 < low). 이 실험 결과를 보고 바꾸지 않는다.
  · 누락 의심: ML 확률 ≥ high인데 설계에 없음
  · ML 추천과 설계 불일치: 설계에 있는데 ML 확률 < low (finish, saved_workflow는 ML 예측 대상이 아니라 제외)
- 경고는 판정이 아니라 확인 요청이다. 문장도 "필요하면 그대로 두라"고 쓴다. 정답·채점 정보는 쓰지 않는다.
- 피드백은 앱의 수정 요청과 같은 경로(승인 대기 → 사용자 메시지)로 한 번만 보낸다. 경고가 없으면 보내지 않는다.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ML_ROOT = HERE.parent
SKIP = {'finish', 'saved_workflow'}
KO = {'read_table': '표 파일 읽기', 'filter_sort': '조건에 따라 정리', 'write_result': '결과 파일 만들기', 'aggregate_table': '목록 집계',
      'read_document': '문서 읽기', 'search_document': '관련 내용 검색', 'extract_information': '정보 추출', 'compose_answer': '근거 기반 답변',
      'export_result': '결과 저장', 'reply_review': '응대 초안 검수', 'review_summary': '검수 보고서 정리', 'agent_node': '에이전트 실행',
      'condition': '조건 분기', 'ask_user': '사용자 확인'}


GENERIC_RECHECK = '\n'.join([   # G_recheck용: ML 정보 없이 같은 형식·같은 대상
    '자동 점검 결과입니다. 판정이 아니라 확인 요청이니, 요청과 자료를 다시 보고 필요한 경우에만 고쳐 주세요.',
    '- 사용한 도구와 노드 연결, 각 노드의 설정이 요청에 맞는지 다시 확인하세요.',
    '이미 정한 조건과 나머지 설정은 그대로 유지하세요.'])


def thresholds():
    return json.loads((ML_ROOT / 'models' / 'tier_thresholds.json').read_text(encoding='utf8'))


def warnings(draft, probs, th=None):
    th = th or thresholds()
    used = {n['tool'] for n in ((draft or {}).get('workflow') or {}).get('nodes', [])} - SKIP
    out = []
    for tool, p in sorted(probs.items(), key=lambda x: -x[1]):
        if tool in SKIP:
            continue
        if p >= th['high'] and tool not in used:
            out.append({'kind': 'missing_suspected', 'tool': tool, 'prob': p})
        elif tool in used and p < th['low']:
            out.append({'kind': 'mismatch', 'tool': tool, 'prob': p})
    return out


def feedback_text(ws):
    if not ws:
        return None
    lines = ['자동 점검 결과입니다. 판정이 아니라 확인 요청이니, 요청과 자료를 다시 보고 필요한 경우에만 고쳐 주세요.']
    mism = [w for w in ws if w['kind'] == 'mismatch']
    miss = [w for w in ws if w['kind'] == 'missing_suspected']
    if mism:
        lines.append('- 요청에서 필요한 이유가 잘 보이지 않는 노드: ' + ', '.join(f"{KO.get(w['tool'], w['tool'])}({w['tool']})" for w in mism)
                     + '. 요청에 없는 처리라면 빼고, 필요하면 그대로 두세요.')
    if miss:
        lines.append('- 빠졌을 수 있는 도구: ' + ', '.join(f"{KO.get(w['tool'], w['tool'])}({w['tool']})" for w in miss)
                     + '. 요청을 처리하는 데 필요하면 넣고, 아니면 그대로 두세요.')
    lines.append('이미 정한 조건과 나머지 설정은 그대로 유지하세요.')
    return '\n'.join(lines)


if __name__ == '__main__':
    # 오프라인 확인: 기존 설계 결과 하나에 점검기를 적용해 보낼 문장을 출력한다(모델 호출 없음)
    import sys
    result = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    cache = json.loads((HERE / 'datasets/development/candidate_cache.json').read_text(encoding='utf8'))
    probs = cache['cases'][result['case_id']]['prediction']['probs']
    ws = warnings(result.get('draft'), probs)
    print(json.dumps(ws, ensure_ascii=False, indent=1))
    print(feedback_text(ws) or '(경고 없음: 피드백을 보내지 않음)')
