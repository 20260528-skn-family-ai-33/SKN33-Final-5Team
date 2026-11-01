"""Advisory inference adapter for the pinned A4B 56-tool catalog."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import joblib

class ToolRecommender:
    def __init__(self, model_path: str | Path, catalog_path: str | Path):
        self.model = joblib.load(model_path)
        self.catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        self.labels = list(self.model["labels"])
        catalog_ids = [row["id"] for row in self.catalog]
        if self.labels != catalog_ids:
            raise ValueError("Pinned model labels/order do not match the supplied tool catalog")
        if len(self.model["heads"]) != len(self.labels):
            raise ValueError("Model head count does not match label count")
        self.catalog_by_id = {row["id"]: row for row in self.catalog}
        self.untrained_tool_ids = [tool_id for tool_id, head in zip(self.labels, self.model["heads"]) if head is None]
        self.trained_tool_ids = [tool_id for tool_id, head in zip(self.labels, self.model["heads"]) if head is not None]

    def predict(self, request_text: str, top_k: int = 10) -> dict[str, Any]:
        if not isinstance(request_text, str) or not request_text.strip():
            raise ValueError("request_text must be a non-empty string")
        if not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        x = self.model["vectorizer"].transform([request_text])
        scored = []
        threshold = float(self.model["threshold"])
        for tool_id, head in zip(self.labels, self.model["heads"]):
            if head is None:
                continue
            score = float(head.decision_function(x)[0])
            scored.append({
                "tool_id": tool_id,
                "name": self.catalog_by_id[tool_id].get("name", tool_id),
                "decision_score": score,
                "selected_by_threshold": score >= threshold,
            })
        scored.sort(key=lambda row: (-row["decision_score"], row["tool_id"]))
        selected = [row["tool_id"] for row in scored if row["selected_by_threshold"]]
        return {
            "model_method": self.model["method"],
            "threshold": threshold,
            "score_semantics": "raw, uncalibrated LinearSVC decision_function margin; not a probability",
            "selected_tool_ids": selected,
            "ranked_tools": scored[:top_k],
            "untrained_tool_ids": list(self.untrained_tool_ids),
            "catalog_scope": "fixed 56-tool catalog packaged with this model",
            "review_before_execution": True,
            "empty_selection_action": "Use the service's existing clarification/fallback path; do not execute an empty or guessed plan.",
            "advisory_only": True,
        }
