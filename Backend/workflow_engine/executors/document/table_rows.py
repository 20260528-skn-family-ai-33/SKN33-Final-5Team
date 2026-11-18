"""Extract every requested cell from parser-native tables without a model.

The parser's row/column coordinates, not adjacency in flattened OCR text, own
row identity. Unknown/merged layouts are refused rather than silently flattened.
"""
import re
import unicodedata

COVERAGE_KEY = 'row_coverage'
ROW_DESIGN_INSTRUCTION = ('문서의 반복 항목을 CSV/표로 정리하는 경우 extract_information.settings.extraction_mode=rows로 지정하고 '
                          'fields에는 사용자가 요청한 열을 넣습니다. 문서당 한 값인 메타데이터 추출만 fields 모드를 사용합니다. '
                          'CSV의 export_result.settings.fields에도 요청한 열만 지정합니다. 근거와 검증 정보는 내부 records에 유지하며 사용자 CSV 열에 넣지 않습니다. '
                          '문서 제목 때문에 반복 행을 단일 값 추출로 바꾸지 마세요.')


def label(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFC', str(text or ''))).casefold()


def native_records(parts, fields, evidence_keys, max_rows):
    records, relevant, unmatched_tables = [], False, False
    targets = {label(field): field for field in fields}
    if len(targets) != len(fields):
        raise ValueError('공백을 제외하면 같은 추출 항목이 있습니다. 열 이름을 구분해 주세요.')
    seen = set()
    for part in parts:
        cells = part.get('table_cells')
        if not cells:
            continue
        if part.get('review_required'):
            raise ValueError('표 인식 결과에 검토가 필요합니다. 원본과 대조한 뒤 실행해 주세요.')
        identity = (part['file_id'], part.get('block_id', part['source_id']))
        if identity in seen:
            continue
        seen.add(identity)
        grid = {}
        for cell in cells:
            row, col = cell.get('row', cell.get('start_row_offset_idx')), cell.get('column', cell.get('start_col_offset_idx'))
            if type(row) is not int or type(col) is not int or row < 0 or col < 0:
                raise ValueError('표 셀의 행·열 위치를 확인할 수 없습니다.')
            if col in grid.setdefault(row, {}):
                raise ValueError('동일한 행·열에 여러 셀이 있어 추출을 중단했습니다.')
            grid[row][col] = cell
        header, selected = None, {}
        for number, row in sorted(grid.items()):
            found = {}
            for column, cell in row.items():
                key = label(cell.get('text'))
                if key in targets:
                    if targets[key] in found:
                        raise ValueError('표에 같은 추출 열 이름이 여러 개 있습니다.')
                    found[targets[key]] = column
            if found:
                relevant = True
            if len(found) == len(fields):
                if any((row[column].get('row_span') or 1) != 1 or (row[column].get('col_span') or 1) != 1 for column in found.values()):
                    raise ValueError('병합된 표 머리글의 열 대응을 확인할 수 없습니다.')
                header, selected = number, found
                break
        if header is None:
            if any(label(cell.get('text')) in targets for row in grid.values() for cell in row.values()):
                raise ValueError('표에서 요청한 열을 모두 찾지 못했습니다. 원본의 열 이름을 확인해 주세요.')
            unmatched_tables = True
            continue
        for number, row in sorted(grid.items()):
            if number <= header:
                continue
            if all(label(row.get(column, {}).get('text')) == label(field) for field, column in selected.items()):
                continue  # Repeated headers within a continued table are not items.
            if not any(str(cell.get('text') or '').strip() for cell in row.values()):
                continue
            record = dict(file_name=part['file_name'], row=len(records) + 1)
            evidence = {}
            for field, column in selected.items():
                cell = row.get(column)
                if cell is None:
                    raise ValueError('표의 일부 셀 위치가 없어 모든 항목의 대응을 확인할 수 없습니다.')
                if (cell.get('row_span') or 1) != 1 or (cell.get('col_span') or 1) != 1:
                    raise ValueError('추출 열의 병합 셀을 행별로 대응할 수 없습니다. 표 구조를 확인해 주세요.')
                value = str(cell.get('text') or '').strip()
                record[field] = value or None
                evidence[field] = {**{key: part[key] for key in evidence_keys if key in part},
                                   'quote': value, 'table_row': number, 'table_column': column}
            record.update(status='verified', evidence=evidence, **{COVERAGE_KEY: 'complete'})
            records.append(record)
            if len(records) > max_rows:
                raise ValueError('추출 행 제한을 넘었습니다. 파일을 나누어 주세요. 일부 행만 저장하지 않았습니다.')
    if relevant and unmatched_tables:
        raise ValueError('요청한 열과 대응하지 않는 표가 함께 있습니다. 전체 행의 추출 범위를 확인해 주세요.')
    return records if relevant else None
