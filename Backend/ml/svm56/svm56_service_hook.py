"""Load only the pinned local candidate model and return optional planner advice."""
from __future__ import annotations
import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path

from .service_advice_adapter import build_svm_advice

# Model from the team ML package (A4B_ML_Qwen_전체작업자료_2026-10-31, 08_서비스연동패키지/model/
# recommender_char_svm_5431.joblib). The earlier c515ff1b… candidate is not in that package.
MODEL_SHA256 = "df14a313659394fcc9b9efda431da7643e692ad4b33ee923f02edc97cc1dbbea"
CATALOG_SHA256 = "cd956b51658051ecd95c642b5738238b3ae7f819c89799668280ba6d96f5dd2a"


DEFAULT_BUNDLE = Path(__file__).resolve().parents[3] / 'runtime' / 'ml-bundle'

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _load_pinned(model_path: str, catalog_path: str, expected_model_sha256: str = MODEL_SHA256):
    if not re.fullmatch(r'[0-9a-f]{64}', expected_model_sha256):
        raise ValueError('Expected model hash must be a SHA256 digest')
    model, catalog = Path(model_path), Path(catalog_path)
    if _sha256(model) != expected_model_sha256 or _sha256(catalog) != CATALOG_SHA256:
        raise ValueError("model or catalog hash differs from pinned candidate")
    from .inference_adapter import ToolRecommender
    return ToolRecommender(model, catalog)


def recommend_svm_for_state(state: dict, registry: list[dict]) -> dict:
    """Fail closed to full-catalog review if configuration/model is unavailable."""
    model_path = os.getenv("A4B_SVM_MODEL_PATH") or str(DEFAULT_BUNDLE / "model/recommender_char_svm_5431.joblib")
    catalog_path = os.getenv("A4B_SVM_CATALOG_PATH") or str(DEFAULT_BUNDLE / "catalog/catalog_56.json")
    if (not os.getenv("A4B_SVM_MODEL_PATH") and not Path(model_path).is_file() or
        not os.getenv("A4B_SVM_CATALOG_PATH") and not Path(catalog_path).is_file()):
        return {"ml_candidates": [], "ml_status": "missing_configuration", "advisory_only": True,
                "ml_usage": "추천 모델이 설정되지 않았습니다. 전체 등록 도구와 요청을 기준으로 검토하세요."}
    try:
        expected = os.getenv('A4B_SVM_EXPECTED_MODEL_SHA256') or MODEL_SHA256
        model = _load_pinned(model_path, catalog_path, expected)
        return {**build_svm_advice(state, registry, model), 'ml_model_sha256': expected}
    except Exception as error:
        return {"ml_candidates": [], "ml_status": "failed", "ml_error_type": type(error).__name__,
                "advisory_only": True,
                "ml_usage": "추천을 사용할 수 없습니다. 전체 등록 도구와 요청을 기준으로 검토하세요."}
