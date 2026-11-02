"""Advisory boundary between the fixed-label model and a live service registry.

No tool is executed, and model scores are uncalibrated margins. The caller owns
catalog access, dynamic discovery, review, permission checks, and execution.
"""
from __future__ import annotations

import math
from typing import Any, Iterable


def _ids(values: Any, name: str) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values):
        raise ValueError(f"{name} must be a list of non-empty tool IDs")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} contains duplicate tool IDs")
    return values


def adapt_recommendation(
    prediction: dict[str, Any], service_catalog_ids: Iterable[str], *, top_k: int | None = None
) -> dict[str, Any]:
    """Partition predictions against the caller's current registry.

    ``top_k`` limits only returned ranking rows, never the threshold-selected set.
    A matching ID is a candidate, not proof of operation or execution compatibility.
    Model-only IDs are retained separately for possible catalog discovery.
    """
    if not isinstance(prediction, dict):
        raise ValueError("prediction must be an object")
    if isinstance(service_catalog_ids, (str, bytes)):
        raise ValueError("service_catalog_ids must be an iterable of IDs")
    ids = list(service_catalog_ids)
    if any(not isinstance(x, str) or not x for x in ids):
        raise ValueError("service catalog IDs must be non-empty strings")
    catalog = set(ids)
    if top_k is not None and (type(top_k) is not int or top_k < 1):
        raise ValueError("top_k must be a positive integer")

    selected = _ids(prediction.get("selected_tool_ids", []), "selected_tool_ids")
    untrained = _ids(prediction.get("untrained_tool_ids", []), "untrained_tool_ids")
    if set(selected) & set(untrained):
        raise ValueError("an untrained tool cannot be threshold-selected")
    ranked = prediction.get("ranked_tools", [])
    if not isinstance(ranked, list):
        raise ValueError("ranked_tools must be a list")
    normalized, seen = [], set()
    for row in ranked:
        if not isinstance(row, dict) or not isinstance(row.get("tool_id"), str) or not row["tool_id"]:
            raise ValueError("each ranked item must have a non-empty tool_id")
        tool = row["tool_id"]
        if tool in seen or tool in untrained:
            raise ValueError("duplicate or untrained tool in ranked_tools")
        seen.add(tool)
        score = row.get("decision_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError("decision_score must be finite")
        flag = row.get("selected_by_threshold")
        if type(flag) is not bool or flag != (tool in selected):
            raise ValueError("ranked selection flag disagrees with selected_tool_ids")
        normalized.append({
            "tool_id": tool, "name": row.get("name", tool), "decision_score": float(score),
            "score_type": "uncalibrated_linear_svm_margin", "is_probability": False,
            "selected_by_threshold": flag,
        })
        if 'selection_threshold' in row:
            cutoff = row['selection_threshold']
            if isinstance(cutoff, bool) or not isinstance(cutoff, (int, float)) or not math.isfinite(cutoff):
                raise ValueError('selection_threshold must be finite')
            if flag != (score >= cutoff):
                raise ValueError('selection flag disagrees with tool threshold')
            normalized[-1]['selection_threshold'] = float(cutoff)
    if top_k is not None:
        normalized = normalized[:top_k]
    known = [tool for tool in selected if tool in catalog]
    missing = [tool for tool in selected if tool not in catalog]
    return {
        "status": "needs_review" if missing or not selected else "candidates_available",
        "model_method": prediction.get("model_method"),
        "registered_ranked_candidates": [r for r in normalized if r["tool_id"] in catalog],
        "unregistered_ranked_candidates": [r for r in normalized if r["tool_id"] not in catalog],
        "registered_selected_tool_ids": known,
        "unregistered_selected_tool_ids": missing,
        "untrained_registered_tool_ids": [tool for tool in untrained if tool in catalog],
        "untrained_unregistered_tool_ids": [tool for tool in untrained if tool not in catalog],
        "ranked_output_limit": top_k,
        "selection_is_not_limited_by_top_k": True,
        "requires_operation_contract_check": True,
        "requires_service_review": True,
        "manual_search_or_selection_required": True,
        "dynamic_discovery_required_for_unregistered": bool(missing),
        "auto_execute": False,
    }


def resolve_legacy_alias(service_tool_id: str, operation_id: str) -> None:
    """Never alias the distinct runtime write_result and export_result IDs."""
    return None
