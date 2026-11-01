"""Load only the pinned local candidate model and return optional planner advice."""
from __future__ import annotations
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from .service_advice_adapter import build_svm_advice

MODEL_SHA256 = "c515ff1b542cf5dee9a48534766d52568c79a605ea5bd813a6540d44d297429b"
CATALOG_SHA256 = "cd956b51658051ecd95c642b5738238b3ae7f819c89799668280ba6d96f5dd2a"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _load_pinned(model_path: str, catalog_path: str):
    model, catalog = Path(model_path), Path(catalog_path)
    if _sha256(model) != MODEL_SHA256 or _sha256(catalog) != CATALOG_SHA256:
        raise ValueError("model or catalog hash differs from pinned candidate")
    from .inference_adapter import ToolRecommender
    return ToolRecommender(model, catalog)


def recommend_svm_for_state(state: dict, registry: list[dict]) -> dict:
    """Fail closed to full-catalog review if configuration/model is unavailable."""
    model_path, catalog_path = os.getenv("A4B_SVM_MODEL_PATH"), os.getenv("A4B_SVM_CATALOG_PATH")
    if not model_path or not catalog_path:
        return {"ml_candidates": [], "ml_status": "missing_configuration", "advisory_only": True,
                "ml_usage": "추천 모델이 설정되지 않았습니다. 전체 등록 도구와 요청을 기준으로 검토하세요."}
    try:
        model = _load_pinned(model_path, catalog_path)
        return build_svm_advice(state, registry, model)
    except Exception as error:
        return {"ml_candidates": [], "ml_status": "failed", "ml_error_type": type(error).__name__,
                "advisory_only": True,
                "ml_usage": "추천을 사용할 수 없습니다. 전체 등록 도구와 요청을 기준으로 검토하세요."}
