"""Run the existing document router on the Pod and return its reviewable output."""
import json
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend'))
from documents.analysis import pipeline
from documents.analysis.contracts import AnalysisError
from documents.analysis.remote_pdf import MAX_BUNDLE


def configure_workspace_mineru():
    base = Path('/workspace/a4b-mineru')
    python = base / 'venv/bin/python'
    if not (base / 'enabled').is_file() or not python.is_file():
        return
    runtime = Path('/tmp/a4b-mineru-xdg')
    runtime.mkdir(mode=0o700, exist_ok=True)
    settings = {
        'A4B_MINERU_PYTHON': str(python),
        'MINERU_HOME': str(base / 'home'),
        'HF_HOME': str(base / 'hf-cache'),
        'MINERU_MODEL_VLM_ENGINE': 'llama-cpp',
        'MINERU_LLAMA_CPP_BACKEND': 'vulkan',
        'VK_DRIVER_FILES': str(base / 'nvidia-headless-icd.json'),
        'XDG_RUNTIME_DIR': str(runtime),
    }
    for name, value in settings.items():
        os.environ.setdefault(name, value)


def analyze(work, policy=None):
    configure_workspace_mineru()
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('The PDF router requires CUDA')
    source = work / 'original.pdf'
    gpu = torch.cuda.get_device_name(0)
    # Supplemental image requests stay on this Pod, using the warm Paddle models.
    os.environ['A4B_PARSER_URL'] = os.environ.get('A4B_POD_PADDLE_URL', 'http://127.0.0.1:' + os.environ.get('A4B_PARSER_PORT', '8100'))
    os.environ.update(A4B_PARSER_FALLBACK='off', A4B_PDF_DEVICE='cuda', A4B_PDF_PARSER_MODE='standard')
    if policy == 'docling-paddle-fixed-v1':
        from documents.analysis.benchmark_baseline import run
        result = run(source, work)
    else:
        primary = 'mineru' if os.environ.get('A4B_MINERU_PYTHON') else 'docling'
        if primary == 'mineru':
            # Explicit GPU backends; a failed MinerU attempt uses the router's Docling fallback.
            os.environ.setdefault('MINERU_MODEL_SMALL_BACKEND', 'torch')
            os.environ.setdefault('MINERU_MODEL_VLM_ENGINE', 'vllm')
        result = pipeline.run(source, work, pdf_primary=primary)
    analysis = result['document_analysis']
    parsed = False
    for attempt in analysis['routes']:
        if attempt['processor'] in ('docling', 'mineru'):
            attempt['route'] = 'gpu_remote'
            if attempt['status'] in ('succeeded', 'partial'):
                if attempt['processor'] == 'docling' and (attempt['model']['device'] != 'cuda'
                                                          or not attempt['model'].get('cuda_peak_bytes')):
                    raise RuntimeError('Docling did not use CUDA')
                attempt['model'].update(gpu=gpu, remote=True)
                parsed = True
    if not parsed and policy != 'docling-paddle-fixed-v1':
        raise AnalysisError('pdf_gpu_processing_failed', 'Neither PDF engine completed on the Pod')
    return result


def write_result(work, result):
    # Only files used by the review UI leave the Pod. Never expose engine logs or absolute paths.
    previews = {}
    total = 0
    for block in result['document_analysis']['blocks']:
        if not block.get('artifact'):
            continue
        path = (work / block['artifact']).resolve()
        if not path.is_relative_to(work) or path.suffix.lower() not in ('.png', '.jpeg', '.jpg', '.webp', '.bmp'):
            raise ValueError('Invalid preview artifact')
        if path not in previews:
            previews[path] = f'pdf-preview-{len(previews)}{path.suffix.lower()}'
            total += path.stat().st_size
        block['artifact'] = previews[path]
    body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > 12 * 1024 * 1024 or total + len(body) > MAX_BUNDLE:
        raise ValueError('PDF result size limit')
    with zipfile.ZipFile(work / 'result.zip', 'w') as archive:
        archive.writestr('result.json', body)
        for path, name in previews.items():
            archive.write(path, name)


if __name__ == '__main__':
    folder = Path(sys.argv[1]).resolve()
    write_result(folder, analyze(folder, sys.argv[2] if len(sys.argv) > 2 else None))
