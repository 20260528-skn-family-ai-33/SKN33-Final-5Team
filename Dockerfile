FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json tsconfig.json ./
RUN npm ci
COPY Frontend/ ./Frontend/
COPY shared/ ./shared/
COPY Backend/api/ ./Backend/api/
RUN npm run build
FROM node:24-bookworm-slim
WORKDIR /app
# libgl1/libglib2.0-0/libgomp1 are runtime libraries of the Paddle OCR stack (OpenCV, OpenMP).
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv libgl1 libglib2.0-0 libgomp1 poppler-utils && rm -rf /var/lib/apt/lists/*
# OCR for photos and scanned pages: PaddleOCR on CPU (the host GPU is left to Ollama).
# Models are downloaded at build time; engine_worker.py runs them offline from A4B_PADDLE_MODEL_DIR.
ENV PADDLE_PDX_CACHE_HOME=/app/models/paddlex A4B_PADDLE_MODEL_DIR=/app/models/paddlex/official_models A4B_PADDLE_DEVICE=cpu
COPY Backend/requirements/paddle-cpu.txt /app/Backend/requirements/
RUN python3 -m venv /app/Backend/.venv-paddle && /app/Backend/.venv-paddle/bin/python -m pip install --no-cache-dir -r /app/Backend/requirements/paddle-cpu.txt \
 && /app/Backend/.venv-paddle/bin/python -c "from paddleocr import LayoutDetection, PaddleOCR; LayoutDetection(model_name='PP-DocLayout_plus-L', device='cpu'); PaddleOCR(text_detection_model_name='PP-OCRv5_mobile_det', text_recognition_model_name='korean_PP-OCRv5_mobile_rec', use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, device='cpu')" \
 && ls /app/models/paddlex/official_models/PP-DocLayout_plus-L /app/models/paddlex/official_models/PP-OCRv5_mobile_det /app/models/paddlex/official_models/korean_PP-OCRv5_mobile_rec >/dev/null
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY Backend/requirements/agent-lock.txt Backend/requirements/canonical-tools.txt Backend/requirements/document-analysis.txt /app/Backend/requirements/
RUN python3 -m venv /app/Backend/.venv-agent && /app/Backend/.venv-agent/bin/python -m pip install --no-cache-dir -r /app/Backend/requirements/agent-lock.txt
# Pinned SVM56 runtime is enabled by default. Weights stay in a read-only host mount.
ARG A4B_WITH_ML=1
COPY Backend/requirements/ml-svm56.txt /app/Backend/requirements/
RUN case "$A4B_WITH_ML" in 1) /app/Backend/.venv-agent/bin/python -m pip install --no-cache-dir -r /app/Backend/requirements/ml-svm56.txt ;; 0) : ;; *) exit 1 ;; esac
# Native document readers (CSV/Excel/PDF text/Word/PowerPoint). Every upload is routed here
# (A4B_DOCUMENT_ROUTING defaults to on), so the image cannot accept files without it.
RUN python3 -m venv /app/Backend/.venv-document-analysis && /app/Backend/.venv-document-analysis/bin/python -m pip install --no-cache-dir -r /app/Backend/requirements/document-analysis.txt
COPY --from=build /app/Frontend/dist-app ./Frontend/dist-app
COPY --from=build /app/Backend/api/build-server ./Backend/api/build-server
# Python worker packages (Backend/ is the import root). verification/ and documents/ were missing
# from the original image (a4b_verifier, document_analysis); they are imported by the design graph.
COPY Backend/service/ /app/Backend/service/
COPY Backend/agent_builder/ /app/Backend/agent_builder/
COPY Backend/workflow_engine/ /app/Backend/workflow_engine/
COPY Backend/ml/ /app/Backend/ml/
COPY Backend/documents/ /app/Backend/documents/
COPY Backend/verification/ /app/Backend/verification/
COPY Backend/infrastructure/ /app/Backend/infrastructure/
COPY Backend/contracts/ /app/Backend/contracts/
# docs/ is not copied: the server and workers never read it (examples serve tests and dev scripts).
COPY scripts/run-deferred-verifications.py /app/scripts/run-deferred-verifications.py
# Fail the build if any registered tool's executor is missing from the image (a broad
# .dockerignore rule once dropped workflow_engine/executors/output).
RUN cd /app/Backend && /app/Backend/.venv-agent/bin/python -c "import importlib; from workflow_engine.catalog.registry import TOOLS; [importlib.import_module(t['module_path'].split(':')[0]) for t in TOOLS]; print(len(TOOLS), 'executors importable')"
ENV HOST=0.0.0.0 PORT=3030 EASYFLOW_DATA_DIR=/app/data A4B_FOCUSED_DEMO=1
EXPOSE 3030
CMD ["node","Backend/api/build-server/index.mjs"]
