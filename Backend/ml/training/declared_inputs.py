"""Offline fixture metadata from input descriptions, never from gold tool labels.

These are declared formats, not uploaded/parsed assets. Ambiguous descriptions
remain reviewable; this module must not fabricate uploads in the running app.
"""
import re

_FORMATS = re.compile(r'(?<![a-z0-9])(?:xlsx|xls|csv|tsv|docx|doc|pptx|ppt|hwpx|hwp|pdf|txt|md|html|htm|jpeg|jpg|png|webp|heic|tiff|tif|bmp|json)(?![a-z0-9])', re.IGNORECASE)
_OUTPUT = re.compile(r'출력|저장|내보낼|내보내기|생성할|결과\s*파일|목표\s*(?:파일|형식)|새\s*(?:파일|경로)')
_INPUT = re.compile(r'입력|원본|첨부|기존|읽을|읽기|제공된|제공한|소스|수정할|변환할')
_TABLE = {'csv', 'tsv', 'xlsx', 'xls'}
_PROVIDED_CONTENT = re.compile(r'OCR\s*결과|분석\s*결과|추출(?:된|한|되어|\s*결과)|본문')
_INLINE_DATA = re.compile(r'레코드|원문|본문|클립보드|사용자\s*댓글|노트|이슈|(?:거래|예산|날씨|기기).*(?:데이터|정보|내역)')


def declared_resources(descriptions):
    if not isinstance(descriptions, list) or any(not isinstance(d, str) for d in descriptions):
        raise ValueError('provided_inputs_ko must be a list of strings')
    resources, ambiguous, excluded, inline = [], [], [], []
    for index, description in enumerate(descriptions):
        # Preserve description-level output scope across fragments such as
        # "CSV 저장 경로·열 구조"; it is not a source-file attachment.
        output_scope = bool(_OUTPUT.search(description)) and not _INPUT.search(description)
        for part in re.split(r'[·;；\n]', description):
            formats = list(dict.fromkeys(m.group().lower() for m in _FORMATS.finditer(part)))
            if not formats:
                if not output_scope and _INLINE_DATA.search(part):
                    inline.append(part)
                elif not output_scope and re.search(r'첨부|원본\s*파일|문서|스프레드시트|워크북|이미지|사진', part):
                    ambiguous.append({'description': part, 'reason': 'file_type_not_explicit'})
                continue
            if output_scope or _OUTPUT.search(part) and not _INPUT.search(part):
                excluded.append({'description': part, 'reason': 'output_description'})
                continue
            if (_OUTPUT.search(part) and _INPUT.search(part)) or ('경로' in part and not _INPUT.search(part)):
                ambiguous.append({'description': part, 'reason': 'input_output_role_not_resolved'})
                continue
            if _PROVIDED_CONTENT.search(part):
                if re.search(r'원본\s*파일|첨부\s*파일', part):
                    ambiguous.append({'description': part, 'reason': 'file_and_content_role_mixed'})
                else:
                    inline.append(part)
                continue
            for fmt in formats:
                if fmt in {'json', 'xls'}:
                    ambiguous.append({'description': part, 'reason': 'json_shape_unknown' if fmt=='json' else 'legacy_conversion_required'})
                    continue
                resources.append({'id': f'declared-{index}-{len(resources)}',
                                  'kind': 'table' if fmt in _TABLE else 'document', 'format': fmt,
                                  'columns': [], 'provenance': 'declared_input_description',
                                  'upload_verified': False, 'description': part})
    status = ('partial_declared' if resources else 'unresolved') if ambiguous else (
        'declared_files' if resources else 'no_explicit_input_file')
    if resources and inline:
        status = 'declared_files_with_inline_values'
    elif not resources and not ambiguous and inline:
        status = 'provided_values_only'
    return {'resources': resources, 'status': status, 'ambiguous': ambiguous,
            'provided_inline_values': inline, 'excluded_outputs': excluded, 'actual_uploads_verified': False}
