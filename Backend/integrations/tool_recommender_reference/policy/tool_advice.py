"""Keep executable tool contracts intact; ML provides IDs and estimated probabilities."""
from copy import deepcopy
import hashlib
import json
import math

ARMS = ('A_full', 'B_rules', 'C_ml_hint', 'O_oracle', 'K_checker', 'G_recheck')
# O_oracle(진단용): B + 사람이 확정한 필수 도구를 알려 준다. 완벽한 추천기를 가정한 상한 실험이며 운영·독립 평가 결과에 섞지 않는다.
# K_checker: 설계 중에는 B와 같고, 설계가 끝나면 checker_feedback.py로 확인 요청을 한 번 보낸다(10/20 Claude 추가).
# G_recheck: K와 같은 대상(ML 경고가 있는 ready 설계)에 ML 정보 없이 일반 재검토 요청만 보낸다. K − G가 ML 정보의 몫이다.
RULES = [
    '전체 도구 명세를 확인하고 실제 입출력 포트가 호환되도록 연결합니다.',
    'finish는 필수 도구가 아닙니다. 표 출력을 finish에 직접 연결하지 않습니다.',
    '파일 저장은 실제 요청 조건을 따릅니다. 자료 종류만으로 저장 도구를 강제하지 않습니다.',
]


def fingerprint(case):
    # Whitelist actual runtime inputs. Labels and expected outputs never participate.
    body = {key: case.get(key) for key in ('request', 'runtime_request', 'asset')}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def build_advice(arm, registry, prediction=None, oracle_tools=None):
    if arm not in ARMS:
        raise ValueError('Unknown arm')
    if arm == 'A_full':
        return None, {'mode': 'baseline', 'recommended': []}
    advice = {'common_rules': list(RULES)}
    meta = {'mode': 'rules_only', 'recommended': []}
    if arm == 'B_rules':
        return advice, meta
    if arm == 'K_checker':
        return advice, {'mode': 'rules_then_post_design_check', 'recommended': []}
    if arm == 'G_recheck':
        return advice, {'mode': 'rules_then_generic_recheck', 'recommended': []}
    if arm == 'O_oracle':
        known = {item['id'] for item in registry}
        ids = [t for t in (oracle_tools or []) if t in known]
        if not ids:
            return advice, {**meta, 'fallback_reason': 'missing_oracle'}
        advice['required_tool_ids'] = ids
        advice['required_tools_usage'] = '이 요청에 필요한 도구 목록입니다. 이 도구들을 모두 사용해 설계하세요. 순서, 연결, 설정은 요청과 도구 명세에 맞게 직접 정하세요.'
        return advice, {'mode': 'oracle_diagnostic', 'recommended': ids}
    if not isinstance(prediction, dict):
        return advice, {**meta, 'fallback_reason': 'missing_prediction'}
    if prediction.get('full_list') is not False:
        return advice, {**meta, 'fallback_reason': 'full_list_or_invalid_flag'}
    probs, candidates = prediction.get('probs'), prediction.get('candidates')
    if not isinstance(probs, dict) or not isinstance(candidates, list):
        return advice, {**meta, 'fallback_reason': 'invalid_prediction'}
    known = {item['id'] for item in registry}
    ids = []
    for tool_id in candidates:
        # finish/saved_workflow were rule additions, not model-class predictions.
        if tool_id in ('finish', 'saved_workflow'):
            continue
        score = probs.get(tool_id) if isinstance(tool_id, str) else None
        if not isinstance(tool_id, str) or tool_id not in known or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            return advice, {**meta, 'fallback_reason': 'unknown_tool_or_invalid_score'}
        if tool_id not in ids:
            ids.append(tool_id)
    if not ids:
        return advice, {**meta, 'fallback_reason': 'empty_candidates'}
    # Preserve the original frozen top-k policy; do not tune thresholds on exposed D2.
    advice['ml_candidates'] = [{'tool_id': tool_id, 'estimated_probability': probs[tool_id]} for tool_id in ids]
    advice['ml_usage'] = ('도구별 추정 확률을 포함한 참고 정보이며 정답이나 실행 순서가 아닙니다. '
                          '목록에 포함됐다고 필수 도구는 아니며, 낮은 점수의 후보를 채우기 위해 노드를 추가하지 않습니다. '
                          '실제 요청과 도구 명세를 우선하고 후보 밖 도구도 필요하면 사용합니다. '
                          '도구별 점수는 서로 독립적인 다중 라벨 추정치로 합이 1일 필요가 없고 실제 필요성을 보장하지 않습니다.')
    return advice, {'mode': 'ml_hint', 'hint_version': 'scored_top5_v2', 'recommended': ids}


def full_tools(registry):
    return deepcopy(registry)
