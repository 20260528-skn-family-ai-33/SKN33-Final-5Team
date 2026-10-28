"""Native inspection -> primary parse -> bounded supplemental routes -> merge.

No engine confidence is treated as document accuracy. Native values are never
overwritten by OCR, and conflicting parser candidates remain reviewable.
"""
import json
import os
import re
import time
from pathlib import Path
from . import CONTRACT_VERSION
from .adapters import LocalEngines, convert
from .contracts import AnalysisError, FORMATS, IMAGE_FORMATS, MAX_BYTES, block, bounds, project, sha256, validate
from .readers import Inspector

PRIMARY = {'pdf': 'mineru', 'hwp': 'docling', 'hwpx': 'docling', 'doc': 'docling',
           'docx': 'docling', 'ppt': 'docling', 'pptx': 'docling', 'xls': 'native', 'xlsx': 'native',
           'csv': 'native', 'tsv': 'native', 'txt': 'native', 'md': 'native',
           **{fmt: 'layout' for fmt in IMAGE_FORMATS}}


def canonical_kind(kind):
    if kind in {'table', 'table_body'}:
        return 'table'
    if kind in {'image', 'picture', 'figure'}:
        return 'image'
    if kind in {'chart', 'formula'}:
        return kind
    if kind in {'unknown', 'abandoned'}:
        return 'unknown'
    return 'text'


def run(source, work, *, engines=None, converter=convert, max_supplements=16):
    source, work = Path(source).resolve(), Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    fmt = source.suffix[1:].lower()
    if fmt not in FORMATS:
        raise AnalysisError('unsupported_format', '지원하지 않는 문서 형식입니다.')
    if not source.is_file() or not 0 < source.stat().st_size <= MAX_BYTES:
        raise AnalysisError('input_limit', '문서는 0바이트 초과·8MB 이하로 연결하세요.')
    if not 0 <= max_supplements <= 32:
        raise AnalysisError('invalid_configuration', '보완 처리 상한은 0~32여야 합니다.')
    engines = engines or LocalEngines(work)
    result = dict(contract_version=CONTRACT_VERSION, document_id=sha256(source), source_sha256=sha256(source),
                  source_format=fmt, primary_processor=PRIMARY[fmt], blocks=[], units=[], routes=engines.attempts,
                  warnings=[], review_required=False, review_reasons=[], conversions=[], signals=[],
                  status='succeeded', routing_decisions=[dict(phase='primary', rule='source_format_default',
                  processor=PRIMARY[fmt], source_format=fmt)], policy=dict(max_supplements=max_supplements, scan_characters=30,
                  broken_ratio=.05, scan_image_fraction=.6, detection_threshold=.5, thresholds_are_provisional=True))
    def review(reason):
        if reason not in result['warnings']:
            result['warnings'].append(reason)
        if reason not in result['review_reasons']:
            result['review_reasons'].append(reason)
        result['review_required'] = True

    effective, effective_fmt = source, fmt
    if fmt in {'hwp', 'hwpx', 'doc', 'ppt', 'xls'}:
        conversion_start = time.perf_counter()
        effective, effective_fmt, record = converter(source, fmt, work)
        result['conversions'].append({**record, 'elapsed_seconds': time.perf_counter() - conversion_start})
        review('converted_source_requires_preservation_check')
    inspection_start = time.perf_counter()
    inspector = Inspector(effective, work)
    try:
        inspected = inspector.run(effective_fmt)
    except AnalysisError as exc:
        # Native PDF inspection is mandatory: geometry and page coverage cannot be invented.
        if effective_fmt == 'pdf' or exc.code != 'dependency_missing':
            raise
        inspected = dict(units=[], blocks=[], images=[], warnings=[exc.code], signals=[])
    result.update(blocks=inspected['blocks'], units=inspected['units'], signals=inspected['signals'])
    for warning in inspected['warnings']:
        review(warning)
    result['timings'] = dict(inspection_seconds=time.perf_counter() - inspection_start)
    primary_failed = False

    def add_parsed(parsed, *, parent=None, unit=None, location=None, selected=True, phase='primary'):
        added = []
        for entry in parsed.get('blocks', []):
            original_location = entry.get('location', {})
            page = original_location.get('page')
            loc = location or original_location
            native_unit = unit or ('page-' + str(page) if effective_fmt == 'pdf' and page else
                                   result['units'][0]['id'] if result['units'] else 'document')
            if effective_fmt != 'pdf' and page and not location:
                loc = {'parser_object_index': len(added), 'parser_reported_page': page}
            geometry = entry.get('bbox')
            item = block('parsed-' + str(len(result['blocks']) + 1), canonical_kind(entry['kind']), loc,
                         text=entry.get('text', ''), data=entry.get('data', {}), parent=parent, unit=native_unit,
                         engine=parsed['engine'], bbox=bounds(geometry, entry.get('coordinate_system', 'engine_native')) if geometry else None)
            item.update(selected=selected, parser_version=parsed['version'], phase=phase)
            result['blocks'].append(item)
            added.append(item)
        if parsed.get('partial'):
            review('partial_engine_output:' + parsed['engine'])
        return added

    if result['primary_processor'] in {'mineru', 'docling'}:
        try:
            parsed = engines.run(result['primary_processor'], source=effective, scope='document')
            # Office's original paragraphs/cells remain authoritative; Docling is a separate candidate.
            add_parsed(parsed, selected=effective_fmt == 'pdf')
        except AnalysisError as exc:
            primary_failed = True
            review('primary_' + result['primary_processor'] + ':' + exc.code)
            if result['primary_processor'] == 'mineru':
                result['routing_decisions'].append(dict(phase='fallback', rule='primary_engine_failed', processor='docling'))
                try:
                    parsed = engines.run('docling', source=effective, scope='document', phase='fallback')
                    add_parsed(parsed)
                    result['fallback_processor'] = 'docling'
                    primary_failed = False
                except AnalysisError as fallback_error:
                    review('fallback_docling:' + fallback_error.code)
            if primary_failed:
                for item in result['blocks']:
                    if item['text'].strip():
                        item['selected'] = True
                result['fallback_processor'] = 'native'

    # Reuse parsed figure boxes to inspect only those PDF images lacking overlapping text.
    images = inspected['images']
    if effective_fmt == 'pdf' and not primary_failed:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        with fitz.open(effective) as pdf:
            candidates = [b for b in list(result['blocks']) if b['engine'] != 'pymupdf' and 'image' in b['content_types'] and b.get('bbox')]
            for item in candidates:
                page_no = item['native_location'].get('page')
                if not page_no or page_no > len(pdf):
                    review('figure_source_page_unknown')
                    continue
                geometry = item['bbox']
                coords = geometry['values']
                page = pdf[page_no - 1]
                if geometry['coordinate_system'] == 'mineru_normalized_top_left':
                    coords = [coords[0]*page.rect.width, coords[1]*page.rect.height,
                              coords[2]*page.rect.width, coords[3]*page.rect.height]
                elif geometry['coordinate_system'] != 'pdf_top_left_points':
                    review('figure_coordinate_system_unknown')
                    continue
                rect = fitz.Rect(coords) & page.rect
                if rect.is_empty:
                    continue
                overlaps = False
                for other in result['blocks']:
                    if not other.get('selected', True) or not other['text'].strip() or other['native_location'].get('page') != page_no or not other.get('bbox'):
                        continue
                    box = other['bbox']
                    values = box['values']
                    if box['coordinate_system'] == 'mineru_normalized_top_left':
                        values = [values[0]*page.rect.width, values[1]*page.rect.height, values[2]*page.rect.width, values[3]*page.rect.height]
                    elif box['coordinate_system'] != 'pdf_top_left_points':
                        continue
                    region = fitz.Rect(values)
                    if region.get_area() and (region & rect).get_area() / region.get_area() > .8:
                        overlaps = True
                        break
                if overlaps:
                    item['data']['existing_text_reused'] = True
                    continue
                if len(images) >= max_supplements:
                    review('supplement_limit_reached')
                    continue
                scale = min(1.5, (4000000 / max(rect.get_area(), 1))**.5)
                path = work / f'figure-{len(images) + 1}.png'
                page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False).save(path)
                item['artifact'] = str(path.relative_to(work))
                item['data']['raster_transform'] = dict(pdf_rect=list(rect), pixels_per_point=scale)
                images.append(dict(block_id=item['id'], path=path))

    # Bound engine work, explicitly retaining skipped source objects.
    if len(images) > max_supplements:
        review('supplement_limit_reached')
    targets = images[:max_supplements]
    by_id = {b['id']: b for b in result['blocks']}
    jobs = []
    for image in targets:
        parent = by_id[image['block_id']]
        try:
            from PIL import Image, ImageOps
            with Image.open(image['path']) as original:
                orientation = original.getexif().get(274, 1)
                normalized = ImageOps.exif_transpose(original).convert('RGB')
                path = work / ('upright-' + parent['id'] + '.png')
                normalized.save(path)
                parent['data']['image_transform'] = dict(original_size=list(original.size), upright_size=list(normalized.size), exif_orientation=orientation)
                jobs.append(dict(parent=parent, path=path, size=normalized.size))
        except (ImportError, OSError, ValueError) as exc:
            parent['status'] = 'review_required'
            review('image_decode_or_dependency_error:' + type(exc).__name__)
    ocr_jobs = []
    table_jobs = []
    classified_count = 0
    if jobs:
        try:
            detected = engines.run('layout', sources=[j['path'] for j in jobs], scope=[j['parent']['id'] for j in jobs], phase='classification')
            mapped = {str(j['path']): j for j in jobs}
            seen = set()
            for prediction in detected['outputs']:
                job = mapped[prediction['source']]
                seen.add(prediction['source'])
                parent = job['parent']
                for detection in prediction['result'].get('boxes', []):
                    score = float(detection['score'])
                    if not 0 <= score <= 1 or score < result['policy']['detection_threshold']:
                        review('low_confidence_region')
                        continue
                    kind = canonical_kind(detection['label'])
                    coords = detection['coordinate']
                    region = block('region-' + str(len(result['blocks']) + 1), kind, parent['native_location'],
                                   parent=parent['id'], unit=parent['unit_id'], engine='layout',
                                   bbox=bounds(coords, 'upright_image_pixels', width=job['size'][0], height=job['size'][1]))
                    region.update(detection_score=score, detector_model=detected['model'], parser_version=detected['version'])
                    result['blocks'].append(region)
                    result['routing_decisions'].append(dict(phase='supplement', scope=region['id'],
                        rule='detected_region_type', content_type=kind,
                        processor='paddleocr' if kind == 'text' else 'docling' if kind == 'table' else 'preserve_or_review'))
                    if kind in {'text', 'table'}:
                        if len(ocr_jobs) + len(table_jobs) >= max_supplements:
                            region['status'] = 'review_required'
                            review('supplement_limit_reached')
                            continue
                        x0, y0, x1, y1 = coords
                        crop_bounds = (max(0, int(x0)), max(0, int(y0)), min(job['size'][0], int(x1 + 1)), min(job['size'][1], int(y1 + 1)))
                        if crop_bounds[0] >= crop_bounds[2] or crop_bounds[1] >= crop_bounds[3]:
                            region['status'] = 'review_required'
                            review('invalid_region_crop')
                            continue
                        crop_path = work / ('crop-' + region['id'] + '.png')
                        with Image.open(job['path']) as picture:
                            picture.crop(crop_bounds).save(crop_path)
                        region['artifact'] = str(crop_path.relative_to(work))
                        region['data']['crop_origin'] = list(crop_bounds[:2])
                        (ocr_jobs if kind == 'text' else table_jobs).append(dict(path=crop_path, parent=region))
                    elif kind in {'formula', 'chart', 'unknown'}:
                        region['status'] = 'review_required'
                        review('semantic_analysis_required:' + kind)
            if len(seen) != len(jobs):
                review('missing_image_classification_output')
            classified_count = len(seen)
        except AnalysisError as exc:
            review('image_classification:' + exc.code)
            for job in jobs:
                job['parent']['status'] = 'review_required'

    if ocr_jobs:
        try:
            recognized = engines.run('paddleocr', sources=[j['path'] for j in ocr_jobs],
                                     scope=[j['parent']['id'] for j in ocr_jobs], phase='supplement')
            mapped = {str(j['path']): j for j in ocr_jobs}
            seen = set()
            for output in recognized['outputs']:
                job = mapped[output['source']]
                seen.add(output['source'])
                region = job['parent']
                text = '\n'.join(output['result'].get('rec_texts', []))
                item = block('ocr-' + str(len(result['blocks']) + 1), 'text', region['native_location'],
                             parent=region['id'], unit=region['unit_id'], text=text, engine='paddleocr',
                             data={'ocr_scores': output['result'].get('rec_scores', []), 'crop_origin': region['data']['crop_origin']})
                item['parser_version'] = recognized['version']
                result['blocks'].append(item)
                if not text.strip():
                    review('empty_ocr_region:' + region['id'])
            if len(seen) != len(ocr_jobs):
                review('missing_ocr_output')
        except AnalysisError as exc:
            review('image_ocr:' + exc.code)

    for job in table_jobs:
        region = job['parent']
        try:
            parsed = engines.run('docling', source=job['path'], scope=region['id'], phase='supplement')
            added = add_parsed(parsed, parent=region['id'], unit=region['unit_id'], location=region['native_location'], phase='supplement')
            for item in added:
                item['data']['crop_origin'] = region['data']['crop_origin']
                item['data']['input_coordinate_system'] = 'crop_pixels_or_engine_page'
                # A crop-local engine box must not masquerade as original PDF geometry.
                if item['bbox']:
                    item['bbox']['coordinate_system'] = 'docling_crop_input_points'
            if not any('table' in b['content_types'] for b in added):
                review('table_structure_missing:' + region['id'])
        except AnalysisError as exc:
            review('image_table:' + exc.code)

    # A structurally incomplete PDF table is compared with its whole page,
    # retaining page context. Candidate output never overwrites primary values.
    if effective_fmt == 'pdf' and not primary_failed:
        problem_pages = sorted({b['native_location'].get('page') for b in result['blocks']
                                if b.get('selected', True) and 'table' in b['content_types']
                                and not b['data'].get('cells') and b['native_location'].get('page')})
        for page_no in problem_pages:
            review('primary_table_structure_incomplete')
            if len(ocr_jobs) + len(table_jobs) >= max_supplements:
                review('supplement_limit_reached')
                break
            table_jobs.append({'comparison_page': page_no})
            result['routing_decisions'].append(dict(phase='comparison', rule='missing_table_structure',
                                                     processor='docling', scope={'page': page_no}))
            one_page = work / ('compare-page-' + str(page_no) + '.pdf')
            with fitz.open(effective) as doc, fitz.open() as target:
                target.insert_pdf(doc, from_page=page_no - 1, to_page=page_no - 1)
                target.save(one_page)
            try:
                candidate = engines.run('docling', source=one_page, scope={'page': page_no}, phase='comparison')
                add_parsed(candidate, unit='page-' + str(page_no), location={'page': page_no}, selected=False, phase='comparison')
            except AnalysisError as exc:
                review('table_comparison:' + exc.code)

    # Retain important-value conflicts. No automatic spelling/number repair.
    native_text = '\n'.join(b['text'] for b in result['blocks'] if b['engine'] in {'native', 'openpyxl+xml', 'pymupdf'})
    parsed_text = '\n'.join(b['text'] for b in result['blocks'] if b['id'].startswith('parsed-') and b.get('phase') == 'primary')
    if parsed_text and native_text:
        important = set(re.findall(r'(?<!\w)\d[\d,.-]{4,}\d(?!\w)', native_text))
        absent = [value for value in important if value not in parsed_text]
        if absent:
            review('important_native_values_absent_from_primary')
            result['quality'] = {'important_native_value_conflict_count': len(absent)}
    for item in result['blocks']:
        if item['status'] == 'review_required' or 'unknown' in item['content_types']:
            review('unprocessed_or_ambiguous_object')
    selected = [b for b in result['blocks'] if b.get('selected', True) and b['text'].strip()]
    text_units = {b['unit_id'] for b in selected}
    empty_units = [u['id'] for u in result['units'] if u['id'] not in text_units]
    if empty_units:
        review('units_without_extracted_text')
    if fmt in {'hwp', 'hwpx'}:
        for item in result['blocks']:
            if 'page' in item['native_location']:
                item['native_location'] = {**item['native_location'], 'converted_page': item['native_location']['page'],
                                           'converted_artifact': result['conversions'][0]['artifact']}
                del item['native_location']['page']
        for unit in result['units']:
            unit['native_location'] = {'converted_page': unit['native_location']['page'], 'converted_artifact': result['conversions'][0]['artifact']}
    result['coverage'] = dict(total_units=len(result['units']), units_with_text=len(text_units), units_without_text=empty_units,
                              source_image_objects=len(images), classified_image_objects=classified_count,
                              unprocessed_image_objects=max(0, len(images) - classified_count))
    result['status'] = 'partial' if result['review_required'] else 'succeeded'
    result['timings']['total_seconds'] = time.perf_counter() - started
    validate(result)
    return project(result)
