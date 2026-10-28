"""Isolated local engine process. Requests contain local paths, never URLs."""
import contextlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from document_analysis.tables import from_html

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
os.environ['MINERU_MODEL_SOURCE'] = 'local'


def version(package):
    return importlib.metadata.version(package)


def plain(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if hasattr(value, 'tolist'):
        return value.tolist()
    if hasattr(value, 'item'):
        return value.item()
    raise TypeError(type(value).__name__)


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(content_text(x) for x in value)
    if isinstance(value, dict):
        return content_text(value.get('content', value.get('text', '')))
    return ''


def mineru(request):
    from mineru.parser import MinerUParser
    local_engine = os.environ.get('MINERU_MODEL_VLM_ENGINE', 'llama-cpp')
    if local_engine not in {'llama-cpp', 'vllm', 'lmdeploy', 'mlx', 'auto'}:
        raise ValueError('문서 라우터는 로컬 MinerU VLM 엔진만 허용합니다.')
    os.environ['MINERU_MODEL_VLM_ENGINE'] = local_engine
    parser = MinerUParser(tier='standard', parse_mode='auto', image_analysis=False)
    parsed = parser.parse(Path(request['source']))
    raw = json.loads(parsed.to_json())
    if raw.get('schema') != 'docvortex.middle' or raw.get('schema_version') != '2.0':
        raise ValueError('MinerU 4 MiddleJson 2.0 계약이 필요합니다. 어댑터 버전을 확인하세요.')
    blocks = []
    for page in raw['pages']:
        for entry in page['blocks']:
            text = content_text(entry.get('content', ''))
            data = dict(entry)
            if entry['type'] == 'table' and '<table' in text.lower():
                data.update(from_html(text))
                text = '\n'.join(c['text'] for c in data['cells'])
            blocks.append(dict(kind=entry['type'], text=text,
                               location={'page': page['page_idx'] + 1}, bbox=entry.get('bbox'),
                               coordinate_system='mineru_normalized_top_left', data=data))
    Path(request['raw']).write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    return dict(engine='mineru', version=version('mineru'), blocks=blocks,
                partial=not raw.get('is_full_document', False), model={'tier': 'standard', 'image_analysis': False,
                    'requested_small_backend': os.environ.get('MINERU_MODEL_SMALL_BACKEND', 'auto'),
                    'requested_vlm_engine': local_engine, 'local_models_only': True},
                page_count=len(raw['pages']))


def docling(request):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from parser_converter import make_converter, parser_config
    from docling.datamodel.base_models import ConversionStatus
    from docling_core.types.doc import TableItem
    config = parser_config()
    converter = make_converter(config)
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import ImageFormatOption
    converter.format_to_options[InputFormat.IMAGE] = ImageFormatOption(pipeline_options=converter.format_to_options[InputFormat.PDF].pipeline_options)
    converted = converter.convert(Path(request['source']), max_num_pages=500, max_file_size=8 * 1024 * 1024)
    if converted.status not in {ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS}:
        raise ValueError('Docling 변환 실패')
    doc = converted.document
    Path(request['raw']).write_text(json.dumps(doc.export_to_dict(), ensure_ascii=False, default=plain), encoding='utf-8')
    blocks = []
    for index, (item, _) in enumerate(doc.iterate_items(traverse_pictures=True)):
        label = getattr(item.label, 'value', str(item.label))
        text = getattr(item, 'text', '') or ''
        data = {}
        if isinstance(item, TableItem):
            text = item.export_to_markdown(doc=doc)
            data = dict(html=item.export_to_html(doc=doc), cells=[plain(c) for c in item.data.table_cells],
                        num_rows=item.data.num_rows, num_cols=item.data.num_cols)
        locations = list(getattr(item, 'prov', []) or [])
        location = {'page': locations[0].page_no} if locations else {'object_index': index}
        bbox = None
        if locations:
            page = doc.pages.get(locations[0].page_no)
            rect = locations[0].bbox
            if page:
                rect = rect.to_top_left_origin(page.size.height)
                bbox = [rect.l, rect.t, rect.r, rect.b]
        blocks.append(dict(kind=label, text=text, location=location, bbox=bbox,
                           coordinate_system='pdf_top_left_points', data=data))
    return dict(engine='docling', version=version('docling'), blocks=blocks,
                partial=converted.status != ConversionStatus.SUCCESS or bool(converted.errors),
                page_count=len(doc.pages), model={'ocr_languages': config['ocr_languages'], 'mode': config['mode']})


def paddle(request):
    root = Path(os.environ.get('A4B_PADDLE_MODEL_DIR', ''))
    def model_path(name, override=None):
        path = Path(override) if override else root / name
        if not path.is_dir() or not str(root) or not any(path.iterdir()):
            raise FileNotFoundError('로컬 모델을 준비하세요: ' + name)
        return str(path.resolve())
    if request['engine'] == 'layout':
        from paddleocr import LayoutDetection
        name = 'PP-DocLayout_plus-L'
        model = LayoutDetection(model_name=name, model_dir=model_path(name, os.environ.get('A4B_LAYOUT_MODEL_DIR')),
                                device=os.environ.get('A4B_PADDLE_DEVICE', 'cpu'))
    else:
        from paddleocr import PaddleOCR
        det, rec = 'PP-OCRv5_mobile_det', 'korean_PP-OCRv5_mobile_rec'
        model = PaddleOCR(text_detection_model_name=det, text_detection_model_dir=model_path(det),
                          text_recognition_model_name=rec, text_recognition_model_dir=model_path(rec),
                          use_doc_orientation_classify=False, use_doc_unwarping=False,
                          use_textline_orientation=False, device=os.environ.get('A4B_PADDLE_DEVICE', 'cpu'))
        name = det + '+' + rec
    outputs = []
    for source in request['sources']:
        for predicted in model.predict(str(source)):
            raw = predicted.json
            raw = raw.get('res', raw)
            outputs.append(dict(source=str(source), result=raw))
    Path(request['raw']).write_text(json.dumps(outputs, ensure_ascii=False, default=plain), encoding='utf-8')
    return dict(engine=request['engine'], version=version('paddleocr'), model={'name': name}, outputs=outputs)


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    output = Path(sys.argv[2])
    try:
        with contextlib.redirect_stdout(sys.stderr):
            data = mineru(request) if request['engine'] == 'mineru' else docling(request) if request['engine'] == 'docling' else paddle(request)
        output.write_text(json.dumps(data, ensure_ascii=False, default=plain, allow_nan=False), encoding='utf-8')
        return 0
    except Exception as exc:
        code = 'dependency_missing' if isinstance(exc, (ImportError, FileNotFoundError)) else 'engine_error'
        output.write_text(json.dumps(dict(error=code, error_type=type(exc).__name__), ensure_ascii=False), encoding='utf-8')
        # Diagnostics stay in the local engine log, never in a protocol response.
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
