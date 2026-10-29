"""Engine-independent contract. Coordinates always describe their own source space."""
import hashlib
import json
import math
from pathlib import Path
from . import CONTRACT_VERSION

MAX_BYTES = 8 * 1024 * 1024
MAX_UNITS = 500
MAX_BLOCKS = 20000
MAX_TEXT = 1000000
MAX_RESULT = 6 * 1024 * 1024
FORMATS = {'pdf', 'hwp', 'hwpx', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx',
           'csv', 'tsv', 'txt', 'md', 'jpg', 'jpeg', 'png', 'bmp', 'webp', 'tif', 'tiff', 'heic'}
IMAGE_FORMATS = {'jpg', 'jpeg', 'png', 'bmp', 'webp', 'tif', 'tiff', 'heic'}


class AnalysisError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def block(identifier, kind, location, *, text='', data=None, parent=None, unit=None,
          engine='native', bbox=None, artifact=None, status='succeeded'):
    return dict(id=identifier, parent_object_id=parent, unit_id=unit,
                content_types=[kind], native_location=location, text=text,
                data=data or {}, engine=engine, bbox=bbox, artifact=artifact,
                status=status, warnings=[])


def bounds(values, space, *, width=None, height=None):
    if len(values) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        raise AnalysisError('invalid_coordinates', '영역 좌표가 올바르지 않습니다.')
    x0, y0, x1, y1 = values
    if x1 < x0 or y1 < y0:
        raise AnalysisError('invalid_coordinates', '영역 좌표 순서가 올바르지 않습니다.')
    return dict(values=list(values), coordinate_system=space, width=width, height=height)


def validate(result):
    if result.get('contract_version') != CONTRACT_VERSION:
        raise AnalysisError('invalid_contract', '문서 분석 계약 버전이 올바르지 않습니다.')
    blocks = result['blocks']
    if len(blocks) > MAX_BLOCKS or len(result['units']) > MAX_UNITS:
        raise AnalysisError('result_limit', '문서 객체 또는 처리 단위 제한을 넘었습니다.')
    ids = [b['id'] for b in blocks]
    units = {u['id'] for u in result['units']}
    if len(units) != len(result['units']):
        raise AnalysisError('invalid_contract', '문서 처리 단위 ID가 중복되었습니다.')
    if len(set(ids)) != len(ids):
        raise AnalysisError('invalid_contract', '문서 객체 ID가 중복되었습니다.')
    known = set(ids)
    by_id = {item['id']: item for item in blocks}
    for b in blocks:
        if b['unit_id'] is not None and b['unit_id'] not in units:
            raise AnalysisError('invalid_contract', '원본 문서 처리 단위를 찾을 수 없습니다.')
        if b['parent_object_id'] and b['parent_object_id'] not in known:
            raise AnalysisError('invalid_contract', '부모 문서 객체를 찾을 수 없습니다.')
        seen = {b['id']}
        parent = b['parent_object_id']
        while parent:
            if parent in seen:
                raise AnalysisError('invalid_contract', '문서 객체 관계에 순환이 있습니다.')
            seen.add(parent)
            parent = by_id[parent]['parent_object_id']
        if b.get('bbox'):
            bounds(b['bbox']['values'], b['bbox']['coordinate_system'])
    if sum(len(b['text']) for b in blocks) > MAX_TEXT:
        raise AnalysisError('text_limit', '문서 본문은 1,000,000자 이내로 나누세요.')
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > MAX_RESULT:
        raise AnalysisError('result_limit', '문서 분석 결과 크기 제한을 넘었습니다.')
    return result


def project(result):
    """Compatibility view; office units are logical units, never invented PDF pages."""
    selected = [b for b in result['blocks'] if b.get('selected', True) and b['text'].strip()]
    groups = {u['id']: [] for u in result['units']}
    for b in selected:
        groups.setdefault(b['unit_id'] or 'document', []).append(b['text'])
    pages = ['\n'.join(values) for values in groups.values()] or ['']
    text = '\n\n'.join(pages)
    if len(text) > MAX_TEXT:
        raise AnalysisError('text_limit', '추출 본문은 1,000,000자 이내로 나누세요.')
    tables = [dict(text=b['text'], html=b['data'].get('html', ''),
                   pages=[b['native_location']['page']] if b['native_location'].get('page') else [],
                   block_id=b['id'], native_location=b['native_location'], data=b['data'])
              for b in selected if 'table' in b['content_types']]
    payload = dict(text=text, pages=pages, page_count=len(pages),
                page_kind='physical_page' if result['source_format'] == 'pdf' else 'logical_unit',
                sections=[dict(text=b['text'], block_id=b['id'], native_location=b['native_location'],
                               pages=[b['native_location']['page']] if b['native_location'].get('page') else [])
                          for b in selected], tables=tables, engine=result['primary_processor'],
                parser_version=CONTRACT_VERSION, parser_status=result['status'],
                warnings=result['warnings'], document_analysis=result)
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) > 12 * 1024 * 1024:
        raise AnalysisError('result_limit', '서비스 문서 결과 크기 제한을 넘었습니다.')
    return payload
