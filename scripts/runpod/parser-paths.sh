# Shared defaults for preparation and startup. Source this file from either script.
export A4B_BASE_DIR=${A4B_BASE_DIR:-/workspace/a4b}
export A4B_PARSER_SOURCE_ROOT=${A4B_PARSER_SOURCE_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)}
export A4B_PDF_VENV=${A4B_PDF_VENV:-/opt/a4b-envs/pdf}
export A4B_PDF_MODELS=${A4B_PDF_MODELS:-$A4B_BASE_DIR/models/pdf}
export A4B_PDF_ROUTER_PYTHON=${A4B_PDF_ROUTER_PYTHON:-$A4B_PDF_VENV/bin/python}
export EASYFLOW_DOCLING_PYTHON=${EASYFLOW_DOCLING_PYTHON:-$A4B_PDF_VENV/bin/python}
export A4B_PDF_ARTIFACTS_DIR=${A4B_PDF_ARTIFACTS_DIR:-$A4B_PDF_MODELS/docling}
export A4B_PDF_OCR_MODEL_DIR=${A4B_PDF_OCR_MODEL_DIR:-$A4B_PDF_ARTIFACTS_DIR/EasyOcr}
