-- schema.sql을 새 빈 PostgreSQL 16 DB에 복원하기 위한 선행 객체.
-- 소유권 설정과 확장 설치 권한이 있는 관리자로 실행한다.
-- 기존 RDS에는 적용하지 않는다. 실제 접속 암호는 별도 배포 설정에서 관리한다.
-- 새로 만드는 역할은 NOLOGIN이다. 기존 역할의 로그인·암호·멤버십은 변경하지 않는다.

DO $a4b_roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'a4b_app') THEN
        CREATE ROLE a4b_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'a4b_guard_owner') THEN
        CREATE ROLE a4b_guard_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$a4b_roles$;

CREATE SCHEMA IF NOT EXISTS extensions;
REVOKE CREATE ON SCHEMA extensions FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions VERSION '0.8.2';

DO $a4b_vector$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_extension e
        JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace
        WHERE e.extname = 'vector' AND e.extversion = '0.8.2' AND n.nspname = 'extensions'
    ) THEN
        RAISE EXCEPTION 'schema.sql requires pgvector 0.8.2 in the extensions schema';
    END IF;
END
$a4b_vector$;

GRANT USAGE ON SCHEMA extensions TO a4b_app, a4b_guard_owner;
