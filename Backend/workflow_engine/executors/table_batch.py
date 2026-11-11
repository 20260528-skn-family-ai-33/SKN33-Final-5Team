"""Combine pinned folder tables while preserving the single-table reader contract."""
from copy import deepcopy


def merge_table_assets(items):
    """Return one row set; reject incompatible files instead of dropping columns."""
    if not items:
        raise ValueError('선택한 폴더에 함께 읽을 수 있는 표 파일이 없습니다.')
    columns = None
    rows = []
    source_asset_ids = []
    for item in items:
        asset = item['asset']
        name = asset.get('name') or asset.get('id') or '이름 없는 파일'
        current = asset.get('columns')
        if (asset.get('kind') != 'table' or not isinstance(current, list) or not current
                or any(not isinstance(column, str) or not column.strip() for column in current)
                or len(current) != len(set(current))):
            raise ValueError('폴더의 표 구조를 확인할 수 없습니다: ' + name)
        if columns is None:
            columns = list(current)
        elif set(current) != set(columns):
            missing = [column for column in columns if column not in current]
            extra = [column for column in current if column not in columns]
            details = []
            if missing:
                details.append('없는 열: ' + '·'.join(missing))
            if extra:
                details.append('다른 열: ' + '·'.join(extra))
            raise ValueError('폴더의 표를 함께 읽으려면 열 이름이 같아야 합니다. ' + name + ' (' + ', '.join(details) + ')')
        current_rows = item.get('rows')
        if not isinstance(current_rows, list):
            raise ValueError('폴더의 표 행을 확인할 수 없습니다: ' + name)
        known = set(columns)
        for row in current_rows:
            if not isinstance(row, dict) or set(row) - known:
                raise ValueError('표의 열과 행 내용이 맞지 않습니다: ' + name)
            rows.append({column: deepcopy(row.get(column)) for column in columns})
        source_asset_ids.append(asset['id'])
    return {'columns': columns, 'rows': rows, 'source_asset_ids': source_asset_ids, 'row_count': len(rows)}
