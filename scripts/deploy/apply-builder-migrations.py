"""Apply infrastructure/db/migrations to the business-data schema (a4b_builder).

The app never creates PostgreSQL tables itself; run this once per new migration, before
starting the app with A4B_STORAGE=postgres. Use an administrator account that can create
schemas, not the app account:

  A4B_MIGRATION_DATABASE_URL='postgresql://ADMIN:URL_ENCODED_PASSWORD@RDS_HOST:5432/a4b_db' \
  Backend/.venv-agent/bin/python scripts/deploy/apply-builder-migrations.py --dry-run

TLS follows the app rule (sslmode=verify-full with A4B_DATABASE_CA_FILE and, through a
tunnel, A4B_DATABASE_TLS_SERVER_NAME). Reads the repository-root .env like the app;
exported variables win. --dry-run lists pending migrations without changing anything.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Backend'))


def load_env():
    path = ROOT / os.environ.get('A4B_ENV_FILE', '.env')
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.split(' #', 1)[0].strip().strip('"\''))


def main():
    parser = argparse.ArgumentParser(description='Apply business-data PostgreSQL migrations.')
    parser.add_argument('--dry-run', action='store_true', help='list pending migrations only')
    parser.add_argument('--existing-only', action='store_true', help='upgrade an existing schema; refuse an uninitialized database')
    args = parser.parse_args()
    load_env()
    from psycopg.conninfo import make_conninfo
    from infrastructure.persistence.db import postgres_conninfo
    from infrastructure.persistence import migrations
    url = os.environ.get('A4B_MIGRATION_DATABASE_URL', '').strip()
    if not url:
        sys.exit('A4B_MIGRATION_DATABASE_URL에 스키마를 만들 수 있는 관리자 계정 주소를 넣으세요.')
    params = postgres_conninfo({**os.environ, 'A4B_DATABASE_URL': url})
    params.pop('options', None)  # Migration files name the a4b_builder schema explicitly.
    conninfo = make_conninfo(**{**params, 'application_name': 'a4b-builder-migrate'})
    todo = migrations.pending(conninfo)
    if args.existing_only and len(todo) == len(migrations.MIGRATIONS):
        sys.exit('운영 자동 배포는 빈 업무 데이터 DB를 초기화하지 않습니다. DB 대상과 기존 스키마를 확인하세요.')
    if args.dry_run:
        print('적용할 마이그레이션: ' + (', '.join(name for _, name in todo) if todo else '없음'))
        return
    if not todo:
        print('완료: 이미 최신입니다.')
        return
    done = migrations.apply(conninfo)
    print('완료: ' + (', '.join(done) if done else '이미 최신입니다.'))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # libpq exceptions can contain connection details; never print the URL.
        sys.exit('업무 데이터 마이그레이션 실패 (' + type(error).__name__ + '). 관리자 DB 연결과 SQL 상태를 확인하세요.')
