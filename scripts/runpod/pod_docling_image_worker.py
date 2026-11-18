"""Convert one normalized PNG crop with the existing local CUDA Docling runtime."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend'))
from documents.analysis.contracts import MAX_RESULT, sha256
from documents.analysis.engine_worker import docling


def convert(work):
    from PIL import Image
    import torch
    source = work / 'original.png'
    with Image.open(source) as image:
        if image.format != 'PNG' or image.width * image.height > 40_000_000:
            raise ValueError('Invalid or oversized PNG')
        image.verify()
    if not torch.cuda.is_available():
        raise RuntimeError('Docling image worker requires CUDA')
    os.environ.update(A4B_PDF_DEVICE='cuda', A4B_PDF_PARSER_MODE='standard',
                      A4B_DOCLING_LOCAL_ONLY='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    result = docling({'source': str(source), 'raw': str(work / 'raw.json')})
    result['source_sha256'] = sha256(source)
    result['model']['remote'] = True
    body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > MAX_RESULT:
        raise ValueError('Docling result size limit')
    (work / 'result.json').write_bytes(body)


if __name__ == '__main__':
    convert(Path(sys.argv[1]).resolve())
