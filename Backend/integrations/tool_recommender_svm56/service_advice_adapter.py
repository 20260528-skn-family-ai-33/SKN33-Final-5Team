"""Optional, dependency-injected A4B planner hints; no model loading or execution."""
from __future__ import annotations
import math

if __package__:
    from .service_recommender_bridge import adapt_recommendation
else:
    from service_recommender_bridge import adapt_recommendation


def _fallback(status: str, error_type: str | None = None) -> dict:
    result = {
        "ml_candidates": [], "ml_status": status,
        "ml_usage": "추천이 없거나 사용할 수 없습니다. 실제 등록된 전체 도구와 사용자 요청을 기준으로 검토하세요.",
        "advisory_only": True,
    }
    if error_type:
        result["ml_error_type"] = error_type
    return result


def build_svm_advice(state: dict, registry: list[dict], recommender, *, extra_margin: float | None = None) -> dict:
    """Return optional hints in the planner context; keep every registry entry usable.

    Caller supplies a cached, trusted model instance. Model-only labels are never
    represented as executable service tools. No probability or execution is implied.
    """
    try:
        if extra_margin is not None and (isinstance(extra_margin, bool) or not isinstance(extra_margin, (int, float))
                                         or not math.isfinite(extra_margin) or not -0.4 <= extra_margin < 0):
            raise ValueError('extra_margin must be finite and in [-0.4, 0)')
        messages = state.get("messages", [])
        if not isinstance(messages, list) or not isinstance(registry, list):
            raise ValueError("invalid state or registry")
        requests = [m.get("content") for m in messages if isinstance(m, dict) and m.get("role") == "user"]
        if any(not isinstance(x, str) for x in requests):
            raise ValueError("user content must be text")
        query = "\n".join(x for x in requests if x.strip())
        if not query:
            return _fallback("empty_request")
        ids = [item["id"] for item in registry]
        # Request all available scores so selected IDs beyond a display top-k
        # cannot silently lose their rank/score before the service boundary.
        prediction = recommender.predict(query, top_k=len(recommender.labels))
        adapted = adapt_recommendation(prediction, ids)
        selected = set(adapted["registered_selected_tool_ids"])
        candidates = [
            {"tool_id": row["tool_id"], "decision_score": row["decision_score"],
             "score_type": "uncalibrated_svm_margin"}
            for row in adapted["registered_ranked_candidates"] if row["tool_id"] in selected
        ]
        extra = []
        if extra_margin is not None:
            threshold = prediction.get('threshold')
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold):
                raise ValueError('prediction threshold must be finite')
            extra = [
                {'tool_id': row['tool_id'], 'decision_score': row['decision_score'],
                 'score_type': 'uncalibrated_svm_margin'}
                for row in adapted['registered_ranked_candidates']
                if row['tool_id'] not in selected and threshold + extra_margin <= row['decision_score'] < threshold
            ]
        return {
            "ml_candidates": candidates,
            "ml_additional_review_candidates": extra,
            "additional_candidate_margin": extra_margin,
            "ml_status": adapted["status"],
            "unregistered_selected_count": len(adapted["unregistered_selected_tool_ids"]),
            "untrained_registered_tool_ids": adapted["untrained_registered_tool_ids"],
            "weak_synthetic_tool_ids": [t for t in ("reply_review", "review_summary") if t in ids],
            "ml_usage": (
                "등록된 도구 중 모델이 제안한 참고 후보입니다. 점수는 확률이 아니며 "
                "필수 도구, 실행 순서 또는 실행 권한을 뜻하지 않습니다. 전체 등록 도구와 "
                "실제 요청·입출력 계약을 확인하고 목록 밖 도구도 필요하면 사용하세요. "
                "미등록 후보는 실행할 수 없으며 별도 도구 검색·등록 경로를 확인하세요. "
                "추가 검토 후보는 실험적인 완화 기준에 해당하며 필요 도구로 확정하지 않습니다. "
                "검수 도구는 소수의 AI 합성 사례로만 보강되어 별도 검증이 필요합니다."
            ),
            "advisory_only": True,
        }
    except Exception as error:
        return _fallback("failed", type(error).__name__)
