"""Opt-in B arm: Docling plus unconditional full-page PaddleOCR.

Never uses gold answers or the A arm to choose, merge, or repair predictions.
Native service table loaders remain shared between arms and are reported as such.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import CONTRACT_VERSION
from .adapters import LocalEngines, convert
from .contracts import AnalysisError, FORMATS, IMAGE_FORMATS, MAX_BYTES, MAX_TEXT, MAX_UNITS, block, bounds, project, sha256, validate
from .pipeline import canonical_kind

POLICY = 'docling-paddle-fixed-v1'


def literal_pdf(source, target):
    import pymupdf as fitz
    raw = source.read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp949')
    if len(text) > MAX_TEXT:
        raise AnalysisError('text_limit', '문서 본문은 1,000,000자 이내로 나누세요.')
    # Source literal text, including CSV quotes and JSON punctuation. No gold
    # delimiter, headers, table ranges, or expected values enter this renderer.
    lines = []
    for line in text.expandtabs(4).splitlines():
        lines.extend([line[i:i + 84] for i in range(0, max(len(line), 1), 84)])
    chunks = [lines[i:i + 56] for i in range(0, len(lines), 56)] or [[]]
    if len(chunks) > MAX_UNITS:
        raise AnalysisError('result_limit', '렌더링 페이지가 500개를 넘었습니다.')
    with fitz.open() as doc:
        for chunk in chunks:
            page = doc.new_page(width=842, height=842)
            for index, line in enumerate(chunk):
                page.insert_text((24, 30 + index * 14), line, fontname='korea', fontsize=9)
        doc.save(target, deflate=True)


def prepare(source, work):
    """Return Docling source and every rendered page, with explicit provenance."""
    import pymupdf as fitz
    from PIL import Image, ImageOps, ImageSequence
    fmt = source.suffix[1:].lower()
    folder = work / 'baseline-render'
    folder.mkdir(parents=True, exist_ok=True)
    pdf = source if fmt == 'pdf' else folder / 'rendered.pdf'
    docling_source = source
    conversions = []
    if fmt in IMAGE_FORMATS:
        if fmt == 'heic':
            from pillow_heif import register_heif_opener
            register_heif_opener()
        frames = []
        with Image.open(source) as image:
            if getattr(image, 'n_frames', 1) > MAX_UNITS:
                raise AnalysisError('result_limit', '이미지 프레임이 500개를 넘었습니다.')
            for frame in ImageSequence.Iterator(image):
                frames.append(ImageOps.exif_transpose(frame.copy()).convert('RGB'))
        with fitz.open() as doc:
            import io
            for frame in frames:
                buffer = io.BytesIO()
                frame.save(buffer, format='PNG')
                page = doc.new_page(width=frame.width / 2, height=frame.height / 2)
                page.insert_image(page.rect, stream=buffer.getvalue())
            doc.save(pdf, deflate=True)
        docling_source = pdf
    elif fmt in {'txt', 'md', 'csv', 'tsv', 'json'}:
        literal_pdf(source, pdf)
        if fmt in {'txt', 'tsv', 'json'}:
            docling_source = pdf
    elif fmt in {'hwp', 'hwpx'}:
        pdf, _, record = convert(source, fmt, work)
        conversions.append(record)
        docling_source = pdf
    elif fmt != 'pdf':
        executable = os.environ.get('A4B_LIBREOFFICE', 'soffice')
        profile = (folder / 'lo-profile').resolve().as_uri()
        with (folder / 'conversion.log').open('wb') as log:
            completed = subprocess.run([executable, '-env:UserInstallation=' + profile, '--headless', '--convert-to', 'pdf',
                                        '--outdir', str(folder), str(source)], stdout=log, stderr=log, timeout=60)
        produced = folder / (source.stem + '.pdf')
        if completed.returncode or not produced.is_file():
            raise AnalysisError('conversion_failed', 'B군 OCR용 Office 페이지 렌더링에 실패했습니다.')
        pdf = produced
        if fmt in {'doc', 'ppt', 'xls'}:
            docling_source, _, record = convert(source, fmt, work)
            conversions.append(record)
    images = []
    with fitz.open(pdf) as doc:
        if not 0 < len(doc) <= MAX_UNITS:
            raise AnalysisError('result_limit', 'B군 렌더링 페이지 수 제한을 넘었습니다.')
        for index, page in enumerate(doc):
            image = folder / f'page-{index + 1:04d}.png'
            pix = page.get_pixmap(dpi=144, alpha=False)
            pix.save(image)
            images.append(image)
    conversions.append(dict(processor='baseline_fixed_renderer', source_sha256=sha256(source),
                            converted_sha256=sha256(pdf), artifact=str(pdf.relative_to(work)) if pdf.is_relative_to(work) else None,
                            raster_dpi=144, page_count=len(images), literal_source=fmt in {'txt', 'md', 'csv', 'tsv', 'json'}))
    return docling_source, images, conversions


def normalized(text):
    return re.sub(r'\s+', '', text)


def run(source, work, *, engines=None):
    source, work = Path(source).resolve(), Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    fmt = source.suffix[1:].lower()
    if fmt not in FORMATS | {'json'}:
        raise AnalysisError('unsupported_format', '지원하지 않는 B군 입력 형식입니다.')
    if not source.is_file() or not 0 < source.stat().st_size <= MAX_BYTES:
        raise AnalysisError('input_limit', '문서는 0바이트 초과·8MB 이하로 연결하세요.')
    # Same 240-second per-engine ceiling as A; share the HTTP 300-second limit.
    # Reserve a first OCR attempt even if Docling exhausts its own budget.
    engines = engines or LocalEngines(work, timeout=240)
    result = dict(contract_version=CONTRACT_VERSION, document_id=sha256(source), source_sha256=sha256(source),
                  source_format=fmt, primary_processor='docling+paddleocr', blocks=[], units=[], routes=engines.attempts,
                  warnings=[], review_required=False, review_reasons=[], conversions=[], signals=[], status='succeeded',
                  routing_decisions=[dict(phase='primary', rule=POLICY, processor='docling+paddleocr')],
                  policy=dict(baseline=POLICY, unconditional_ocr=True, renderer_dpi=144,
                              internal_deadline_seconds=290, max_engine_seconds=240, reserved_ocr_seconds=30,
                              merge='docling_primary_append_nonduplicate_ocr_per_page', human_review=False))
    try:
        docling_source, images, result['conversions'] = prepare(source, work)
    except Exception as error:
        # Explicit preparation failure, never pretend either model ran.
        (work / 'baseline-preparation-failure.json').write_text(json.dumps({'error_type': type(error).__name__, 'message': str(error)}))
        raise
    result['units'] = ([] if fmt == 'pdf' else [dict(id='document', native_location={'document': True})]) + [
        dict(id=f'page-{i + 1}', native_location={'page': i + 1} if fmt == 'pdf' else {'rendered_page': i + 1})
        for i in range(len(images))]
    def review(reason):
        result['warnings'].append(reason)
        result['review_reasons'].append(reason)
        result['review_required'] = True
        result['status'] = 'partial'
    def add(entry, engine, version, phase, selected=True):
        location = entry.get('location', {})
        page = location.get('page', 1)
        unit = f'page-{page}' if fmt == 'pdf' or phase == 'mandatory_ocr' else 'document'
        if fmt != 'pdf' and location.get('page'):
            location = {'rendered_page': page} if phase == 'mandatory_ocr' else {'parser_reported_page': page}
        item = block('baseline-' + str(len(result['blocks']) + 1), canonical_kind(entry.get('kind', 'text')), location,
                     text=entry.get('text', ''), data=entry.get('data', {}), engine=engine, unit=unit,
                     bbox=bounds(entry['bbox'], entry.get('coordinate_system', 'engine_native')) if entry.get('bbox') else None)
        item.update(phase=phase, selected=selected, parser_version=version)
        result['blocks'].append(item)
    primary = None
    try:
        engines.timeout = max(1, min(240, 290 - (time.perf_counter() - started) - 30))
        primary = engines.run('docling', source=docling_source, scope='document', phase='primary')
        for entry in primary.get('blocks', []):
            add(entry, 'docling', primary['version'], 'primary')
        if primary.get('partial'):
            review('partial_docling')
    except AnalysisError as error:
        review('docling:' + error.code)
    # Deliberately unconditional, including when Docling returned complete text.
    try:
        engines.timeout = max(1, min(240, 290 - (time.perf_counter() - started)))
        ocr = engines.run('paddleocr', sources=images, scope='all_rendered_pages', phase='mandatory_ocr')
        if len(ocr.get('outputs', [])) != len(images):
            review('paddle_page_coverage_incomplete')
        page_lookup = {str(image): index + 1 for index, image in enumerate(images)}
        for output in ocr.get('outputs', []):
            page = page_lookup[output['source']]
            candidates = [b for b in result['blocks'] if b['engine'] == 'docling' and
                          (fmt != 'pdf' or b['native_location'].get('page') == page)]
            existing = normalized('\n'.join(b['text'] for b in candidates))
            raw = output['result']
            for index, text in enumerate(raw.get('rec_texts', [])):
                key = normalized(text)
                selected = bool(key) and key not in existing
                add(dict(kind='text', text=text, location={'page': page},
                         data=dict(ocr_score=(raw.get('rec_scores', []) + [None] * (index + 1))[index],
                                   duplicate_of_docling=not selected, raster_artifact=str(images[page - 1].relative_to(work)))),
                    'paddleocr', ocr['version'], 'mandatory_ocr', selected)
    except AnalysisError as error:
        review('paddleocr:' + error.code)
    result['coverage'] = dict(rendered_pages=len(images), docling_succeeded=any(r['processor'] == 'docling' and r['status'] == 'succeeded' for r in engines.attempts),
                              paddleocr_succeeded=any(r['processor'] == 'paddleocr' and r['status'] == 'succeeded' for r in engines.attempts))
    result['timings'] = dict(total_seconds=time.perf_counter() - started)
    validate(result)
    return project(result)
