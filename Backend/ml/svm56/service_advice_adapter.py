"""Optional, dependency-injected A4B planner hints; no model loading or execution."""
from __future__ import annotations

from ml.advice.request_context import request_context, abstain
from ml.svm56.service_contracts import align_candidates

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


def build_svm_advice(state: dict, registry: list[dict], recommender) -> dict:
    """Return optional hints in the planner context; keep every registry entry usable.

    Caller supplies a cached, trusted model instance. Model-only labels are never
    represented as executable service tools. No probability or execution is implied.
    """
    try:
        if not isinstance(registry, list):
            raise ValueError("invalid state or registry")
        query, context, reason = request_context(state)
        if reason:
            return abstain(reason, context)
        ids = [item["id"] for item in registry]
        # Request all available scores so selected IDs beyond a display top-k
        # cannot silently lose their rank/score before the service boundary.
        prediction = recommender.predict(query, top_k=len(recommender.labels))
        adapted = adapt_recommendation(prediction, ids)
        selected = set(adapted["registered_selected_tool_ids"])
        candidates = [
            {"tool_id": row["tool_id"], "decision_score": row["decision_score"],
             "score_type": "uncalibrated_svm_margin", "source": "svm56",
             **({'selection_threshold': row['selection_threshold']} if 'selection_threshold' in row else {})}
            for row in adapted["registered_ranked_candidates"] if row["tool_id"] in selected
        ]
        candidates, adjustments = align_candidates(query, context, candidates, registry)
        return {
            "ml_candidates": candidates,
            "ml_status": 'needs_review' if not candidates or adapted['status'] == 'needs_review' else 'candidates_available',
            "ml_context": context,
            "ml_selection_policy": prediction.get('selection_policy', 'global_threshold'),
            "service_adjustments": adjustments,
            "unregistered_selected_count": len(adapted["unregistered_selected_tool_ids"]),
            "untrained_registered_tool_ids": adapted["untrained_registered_tool_ids"],
            "weak_synthetic_tool_ids": [t for t in ("reply_review", "review_summary") if t in ids],
            "ml_usage": (
                "등록된 도구 중 모델이 제안한 참고 후보입니다. 점수는 확률이 아니며 "
                "필수 도구, 실행 순서 또는 실행 권한을 뜻하지 않습니다. 전체 등록 도구와 "
                "실제 요청·입출력 계약을 확인하고 목록 밖 도구도 필요하면 사용하세요. "
                "미등록 후보는 실행할 수 없으며 별도 도구 검색·등록 경로를 확인하세요. "
                "source=service_contract는 모델 예측이 아닌 서비스 규칙 참고 후보이며 점수가 없습니다. "
                "미학습 도구는 untrained_registered_tool_ids에서 확인하세요. 규칙 후보도 실행·승인 검사를 생략하지 않습니다."
            ),
            "advisory_only": True,
        }
    except Exception as error:
        return _fallback("failed", type(error).__name__)
