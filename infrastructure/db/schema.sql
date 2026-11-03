-- A4B a4b 스키마 정의 스냅샷
-- 추출 기준: 2026-11-02 (한국 시간), 실제 RDS PostgreSQL 16.15 / pg_dump 16.15.
-- 범위: a4b 스키마 전체. 테이블 19개, 컬럼 198개, 외래키 44개,
--       함수 39개, 트리거 25개와 인덱스·주석·소유권·GRANT/REVOKE.
-- 테이블 데이터, 회원 정보, 비밀번호, 접속 주소, 카탈로그 초기 데이터는 포함하지 않는다.
--
-- 검토 및 새 빈 DB 복원용 기준본이다. 기존 RDS에 재실행하는 마이그레이션이 아니다.
-- PostgreSQL 16 및 psql 16.15 이상을 사용하고, 새 DB에 prerequisites.sql을 먼저 적용한다.
-- postgres 소유권과 a4b_guard_owner 소유 SECURITY DEFINER 함수 5개의 권한 경계를 보존한다.
-- 앱 시작 시 자동 실행하지 않는다. 갱신·복원 절차: docs/cognito-login.md의 DB 스키마 정의 절.

--
-- PostgreSQL database dump
--

\restrict tzI4RQbrgZDt22ahWIX37wtYsVRaUmM2sqtE7b0udlOe62QDDMa0bhmbZntgOED

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: a4b; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA a4b;


ALTER SCHEMA a4b OWNER TO postgres;

--
-- Name: SCHEMA a4b; Type: COMMENT; Schema: -; Owner: postgres
--

COMMENT ON SCHEMA a4b IS 'A4B 초기 단일 팀 공유 문서 워크플로우. 다중 고객사 격리는 별도 설계.';


--
-- Name: check_evidence_scope(); Type: FUNCTION; Schema: a4b; Owner: a4b_guard_owner
--

CREATE FUNCTION a4b.check_evidence_scope() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$ DECLARE v_output a4b.outputs%ROWTYPE; v_document_run uuid;
BEGIN
    SELECT * INTO v_output FROM a4b.outputs WHERE id=NEW.output_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'output not found' USING ERRCODE='23503'; END IF;
    SELECT run_id INTO v_document_run FROM a4b.run_documents WHERE id=NEW.run_document_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'run document not found' USING ERRCODE='23503'; END IF;
    IF v_output.run_id IS DISTINCT FROM v_document_run THEN
        RAISE EXCEPTION 'evidence must use a document pinned to the same run' USING ERRCODE='23514';
    END IF;
    IF v_output.output_type='extraction' AND v_output.source_run_document_id IS DISTINCT FROM NEW.run_document_id THEN
        RAISE EXCEPTION 'extraction evidence must cite its declared source document' USING ERRCODE='23514';
    END IF;
    IF NEW.claim_path<>'' AND NOT a4b.json_pointer_exists(v_output.content_json,NEW.claim_path) THEN
        RAISE EXCEPTION 'evidence claim path is absent from output JSON' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM a4b.approval_requests WHERE target_output_id=NEW.output_id) THEN
        RAISE EXCEPTION 'evidence is sealed once an approval request exists' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.check_evidence_scope() OWNER TO a4b_guard_owner;

--
-- Name: check_previous_output(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.check_previous_output() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$ DECLARE v_prev a4b.outputs%ROWTYPE;
BEGIN
    IF NEW.previous_output_id IS NOT NULL THEN
        SELECT * INTO v_prev FROM a4b.outputs WHERE id=NEW.previous_output_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'previous output not found' USING ERRCODE='23503'; END IF;
        IF v_prev.revision_no >= NEW.revision_no THEN
            RAISE EXCEPTION 'previous output revision must be lower' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.check_previous_output() OWNER TO postgres;

--
-- Name: extraction_matches_schema(jsonb, jsonb); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.extraction_matches_schema(p_value jsonb, p_schema jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE
    v_values jsonb[] := ARRAY[p_value];
    v_schemas jsonb[] := ARRAY[p_schema];
    v_index integer := 1;
    v_value jsonb;
    v_schema jsonb;
    v_type text;
    v_actual text;
    v_key text;
    v_child jsonb;
    v_number numeric;
    v_text text;
BEGIN
    IF p_value IS NULL OR NOT a4b.supported_extraction_schema(p_schema) THEN RETURN false; END IF;
    WHILE v_index <= cardinality(v_values) LOOP
        v_value := v_values[v_index];
        v_schema := v_schemas[v_index];
        v_index := v_index + 1;
        v_actual := jsonb_typeof(v_value);
        IF v_schema ? 'enum' AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(v_schema -> 'enum') WHERE value = v_value
        ) THEN RETURN false; END IF;
        IF jsonb_typeof(v_schema -> 'type') = 'array' THEN
            IF v_actual = 'null' THEN CONTINUE; END IF;
            SELECT value #>> '{}' INTO v_type FROM jsonb_array_elements(v_schema -> 'type')
            WHERE value <> '"null"'::jsonb;
        ELSE v_type := v_schema ->> 'type'; END IF;
        IF v_type = 'integer' THEN
            IF v_actual <> 'number' THEN RETURN false; END IF;
            v_number := (v_value #>> '{}')::numeric;
            IF trunc(v_number) <> v_number THEN RETURN false; END IF;
        ELSIF v_actual IS DISTINCT FROM v_type THEN RETURN false;
        END IF;

        CASE v_type
        WHEN 'object' THEN
            IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(coalesce(v_schema -> 'required','[]'::jsonb)) r
                       WHERE NOT (v_value ? r)) THEN RETURN false; END IF;
            FOR v_key, v_child IN SELECT key, value FROM jsonb_each(v_value) LOOP
                IF coalesce((v_schema -> 'properties') ? v_key, false) THEN
                    v_values := array_append(v_values, v_child);
                    v_schemas := array_append(v_schemas, v_schema -> 'properties' -> v_key);
                ELSIF v_schema -> 'additionalProperties' = 'false'::jsonb THEN RETURN false;
                END IF;
            END LOOP;
        WHEN 'array' THEN
            IF v_schema ? 'minItems' AND jsonb_array_length(v_value)::numeric < (v_schema ->> 'minItems')::numeric
            THEN RETURN false; END IF;
            IF v_schema ? 'maxItems' AND jsonb_array_length(v_value)::numeric > (v_schema ->> 'maxItems')::numeric
            THEN RETURN false; END IF;
            IF v_schema ? 'items' THEN
                FOR v_child IN SELECT value FROM jsonb_array_elements(v_value) LOOP
                    v_values := array_append(v_values, v_child);
                    v_schemas := array_append(v_schemas, v_schema -> 'items');
                END LOOP;
            END IF;
        WHEN 'string' THEN
            v_text := v_value #>> '{}';
            IF v_schema ? 'minLength' AND char_length(v_text)::numeric < (v_schema ->> 'minLength')::numeric
            THEN RETURN false; END IF;
            IF v_schema ? 'maxLength' AND char_length(v_text)::numeric > (v_schema ->> 'maxLength')::numeric
            THEN RETURN false; END IF;
            IF v_schema ->> 'format' = 'date' THEN
                IF v_text !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RETURN false; END IF;
                BEGIN
                    PERFORM make_date(substring(v_text FROM 1 FOR 4)::integer,
                                      substring(v_text FROM 6 FOR 2)::integer,
                                      substring(v_text FROM 9 FOR 2)::integer);
                EXCEPTION WHEN datetime_field_overflow OR invalid_datetime_format THEN RETURN false;
                END;
            END IF;
        WHEN 'number', 'integer' THEN
            v_number := (v_value #>> '{}')::numeric;
            IF v_schema ? 'minimum' AND v_number < (v_schema ->> 'minimum')::numeric THEN RETURN false; END IF;
            IF v_schema ? 'maximum' AND v_number > (v_schema ->> 'maximum')::numeric THEN RETURN false; END IF;
        ELSE NULL;
        END CASE;
    END LOOP;
    RETURN true;
END $_$;


ALTER FUNCTION a4b.extraction_matches_schema(p_value jsonb, p_schema jsonb) OWNER TO postgres;

--
-- Name: guard_approval_request(); Type: FUNCTION; Schema: a4b; Owner: a4b_guard_owner
--

CREATE FUNCTION a4b.guard_approval_request() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$ DECLARE v_run a4b.workflow_runs%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'approval history is immutable' USING ERRCODE='23514'; END IF;
    SELECT * INTO v_run FROM a4b.workflow_runs WHERE id=NEW.run_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'run not found' USING ERRCODE='23503'; END IF;
    IF TG_OP='INSERT' AND NEW.status <> 'pending' THEN
        RAISE EXCEPTION 'new approval must start pending' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF ROW(NEW.id,NEW.run_id,NEW.waiting_node_run_id,NEW.target_output_id,NEW.reviewer_id,NEW.expected_state_revision,NEW.created_at)
            IS DISTINCT FROM ROW(OLD.id,OLD.run_id,OLD.waiting_node_run_id,OLD.target_output_id,OLD.reviewer_id,OLD.expected_state_revision,OLD.created_at) THEN
            RAISE EXCEPTION 'approval target/assignment is immutable' USING ERRCODE='23514';
        END IF;
        IF OLD.status <> 'pending' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'completed approval decisions are immutable' USING ERRCODE='23514';
        END IF;
    END IF;
    IF v_run.status <> 'waiting_approval' OR v_run.state_revision <> NEW.expected_state_revision THEN
        RAISE EXCEPTION 'approval is stale or run is not waiting for approval' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM a4b.node_runs WHERE id=NEW.waiting_node_run_id
                   AND run_id=NEW.run_id AND status='waiting') THEN
        RAISE EXCEPTION 'approval node must be waiting in the same run' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM a4b.users WHERE id=NEW.reviewer_id AND status='active') THEN
        RAISE EXCEPTION 'reviewer must be active' USING ERRCODE='23514';
    END IF;
    -- 출력 행을 잠가 근거 추가와 승인 생성의 경쟁을 직렬화한다.
    PERFORM 1 FROM a4b.outputs WHERE id=NEW.target_output_id AND run_id=NEW.run_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'target output not found in the same run' USING ERRCODE='23503'; END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_approval_request() OWNER TO a4b_guard_owner;

--
-- Name: guard_chunk_location(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.guard_chunk_location() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$ DECLARE v_status text; v_id uuid;
BEGIN
    IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'replace chunk location links before publication' USING ERRCODE='23514'; END IF;
    v_id := CASE WHEN TG_OP='DELETE' THEN OLD.document_version_id ELSE NEW.document_version_id END;
    SELECT status INTO v_status FROM a4b.document_versions WHERE id=v_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'document version not found' USING ERRCODE='23503'; END IF;
    IF v_status <> 'processing' THEN RAISE EXCEPTION 'chunk locations are editable only while processing' USING ERRCODE='23514'; END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_chunk_location() OWNER TO postgres;

--
-- Name: guard_document(); Type: FUNCTION; Schema: a4b; Owner: a4b_guard_owner
--

CREATE FUNCTION a4b.guard_document() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$ BEGIN
    IF TG_OP = 'UPDATE' AND ROW(NEW.id,NEW.created_by,NEW.created_at)
        IS DISTINCT FROM ROW(OLD.id,OLD.created_by,OLD.created_at) THEN
        RAISE EXCEPTION 'document identity is immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'INSERT' OR NEW.document_type_code IS DISTINCT FROM OLD.document_type_code THEN
        PERFORM 1 FROM a4b.document_types WHERE code=NEW.document_type_code AND is_active FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'active document type required' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_document() OWNER TO a4b_guard_owner;

--
-- Name: guard_document_chunk(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.guard_document_chunk() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$ DECLARE v_parent a4b.document_versions%ROWTYPE; v_id uuid;
BEGIN
    v_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.document_version_id ELSE NEW.document_version_id END;
    SELECT * INTO v_parent FROM a4b.document_versions WHERE id = v_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'document version not found' USING ERRCODE = '23503'; END IF;
    IF v_parent.status <> 'processing' THEN
        RAISE EXCEPTION 'chunks can change only while their version is processing' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        (to_jsonb(NEW) - ARRAY['embedding','embedding_status'])
        IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['embedding','embedding_status'])
    ) THEN
        RAISE EXCEPTION 'chunk identity/content is immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP <> 'DELETE' AND NEW.embedding IS NOT NULL
       AND NOT a4b.valid_embedding_profile(v_parent.processing_config_json -> 'embedding') THEN
        RAISE EXCEPTION 'vector requires a matching embedding profile' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_document_chunk() OWNER TO postgres;

--
-- Name: guard_document_type(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.guard_document_type() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$ BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'disable document types instead of deleting' USING ERRCODE='23514'; END IF;
    IF NEW.code IS DISTINCT FROM OLD.code OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'document type identity is immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_document_type() OWNER TO postgres;

--
-- Name: guard_document_version(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.guard_document_version() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$ DECLARE v_type text;
BEGIN
    IF TG_OP='INSERT' THEN
        SELECT document_type_code INTO v_type FROM a4b.documents WHERE id=NEW.document_id FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'document not found' USING ERRCODE='23503'; END IF;
        IF NEW.document_type_code IS NULL THEN NEW.document_type_code := v_type; END IF;
        IF NEW.document_type_code IS DISTINCT FROM v_type THEN
            RAISE EXCEPTION 'new document version must snapshot its current document type' USING ERRCODE='23514';
        END IF;
        IF NEW.status NOT IN ('uploaded','processing') THEN
            RAISE EXCEPTION 'new versions must start uploaded or processing' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'document version history cannot be deleted' USING ERRCODE='23514'; END IF;
    IF (to_jsonb(NEW) - ARRAY['status','parsed_s3_key','error_json',
            'parser_contract_version','parsed_s3_version_id','parsed_json_sha256',
            'canonical_text_sha256','canonical_text_length','parser_metadata_json'])
        IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','parsed_s3_key','error_json',
            'parser_contract_version','parsed_s3_version_id','parsed_json_sha256',
            'canonical_text_sha256','canonical_text_length','parser_metadata_json']) THEN
        RAISE EXCEPTION 'document source/config/type snapshot is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.status IN ('ready','failed') AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'completed processing versions are immutable' USING ERRCODE='23514';
    END IF;
    IF (OLD.status='uploaded' AND NEW.status NOT IN ('uploaded','processing','failed'))
        OR (OLD.status='processing' AND NEW.status NOT IN ('processing','ready','failed')) THEN
        RAISE EXCEPTION 'invalid document processing transition' USING ERRCODE='23514';
    END IF;
    IF ROW(NEW.parser_contract_version,NEW.parsed_s3_version_id,NEW.parsed_json_sha256,
            NEW.canonical_text_sha256,NEW.canonical_text_length,NEW.parser_metadata_json)
       IS DISTINCT FROM ROW(OLD.parser_contract_version,OLD.parsed_s3_version_id,OLD.parsed_json_sha256,
            OLD.canonical_text_sha256,OLD.canonical_text_length,OLD.parser_metadata_json)
       AND NOT (OLD.status = 'processing' OR NEW.status = 'processing') THEN
        RAISE EXCEPTION 'parser fields change only while processing' USING ERRCODE='23514';
    END IF;
    IF NEW.status='ready' THEN
        IF NOT EXISTS (SELECT 1 FROM a4b.source_locations WHERE document_version_id=OLD.id) THEN
            RAISE EXCEPTION 'ready version requires at least one source location' USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM a4b.document_chunks WHERE document_version_id=OLD.id AND embedding_status IN ('pending','failed')) THEN
            RAISE EXCEPTION 'all chunks must be ready or not_required before publication' USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM a4b.document_chunks ch WHERE ch.document_version_id=OLD.id
                   AND NOT EXISTS (SELECT 1 FROM a4b.chunk_locations cl WHERE cl.chunk_id=ch.id)) THEN
            RAISE EXCEPTION 'every chunk requires source location links before publication' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_document_version() OWNER TO postgres;

--
-- Name: guard_extraction_output(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.guard_extraction_output() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$ DECLARE v_schema a4b.extraction_schema_versions%ROWTYPE; v_type text;
BEGIN
    IF NEW.output_type <> 'extraction' THEN RETURN NEW; END IF;
    SELECT * INTO v_schema FROM a4b.extraction_schema_versions
        WHERE id=NEW.extraction_schema_version_id AND schema_key=NEW.schema_key;
    IF NOT FOUND THEN RAISE EXCEPTION 'extraction schema version/key not found' USING ERRCODE='23503'; END IF;
    SELECT dv.document_type_code INTO v_type FROM a4b.run_documents rd
        JOIN a4b.document_versions dv ON dv.id=rd.document_version_id
        WHERE rd.id=NEW.source_run_document_id AND rd.run_id=NEW.run_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'extraction source document must be pinned to the same run' USING ERRCODE='23503'; END IF;
    IF v_type IS DISTINCT FROM v_schema.document_type_code THEN
        RAISE EXCEPTION 'extraction schema must match the version type snapshot' USING ERRCODE='23514';
    END IF;
    IF NOT a4b.extraction_matches_schema(NEW.content_json,v_schema.schema_json) THEN
        RAISE EXCEPTION 'extraction result does not match its registered schema' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_extraction_output() OWNER TO postgres;

--
-- Name: guard_node_run(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.guard_node_run() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$ BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'node history is immutable' USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' THEN
        IF ROW(NEW.id,NEW.run_id,NEW.node_key,NEW.attempt_no,NEW.input_json,NEW.started_at)
            IS DISTINCT FROM ROW(OLD.id,OLD.run_id,OLD.node_key,OLD.attempt_no,OLD.input_json,OLD.started_at) THEN
            RAISE EXCEPTION 'node identity/input is immutable' USING ERRCODE='23514';
        END IF;
        IF OLD.status IN ('succeeded','failed','canceled') AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'terminal attempts are immutable; insert a new attempt' USING ERRCODE='23514';
        END IF;
        IF OLD.responded_by IS NOT NULL AND ROW(NEW.responded_by,NEW.responded_at,NEW.assignee_id,NEW.output_json)
            IS DISTINCT FROM ROW(OLD.responded_by,OLD.responded_at,OLD.assignee_id,OLD.output_json) THEN
            RAISE EXCEPTION 'a human response can only be confirmed once' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_node_run() OWNER TO postgres;

--
-- Name: guard_parser_chunk_segments(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.guard_parser_chunk_segments() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v_length bigint; v_contract text;
BEGIN
    SELECT canonical_text_length, parser_contract_version INTO v_length, v_contract
        FROM a4b.document_versions WHERE id = NEW.document_version_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'document version not found' USING ERRCODE='23503'; END IF;
    IF jsonb_typeof(NEW.source_segments_json) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'chunk source segments must be an array' USING ERRCODE='23514';
    END IF;
    IF jsonb_array_length(NEW.source_segments_json) > 0 AND v_contract IS NULL THEN
        RAISE EXCEPTION 'source segments require an opted-in parser contract' USING ERRCODE='23514';
    END IF;
    IF NOT a4b.parser_source_segments_valid(NEW.source_segments_json, NEW.content,
        NEW.document_version_id, v_length) THEN
        RAISE EXCEPTION 'invalid chunk source segments' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_parser_chunk_segments() OWNER TO postgres;

--
-- Name: guard_parser_contract_version(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.guard_parser_contract_version() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE
    v_has_result boolean; v_chunk a4b.document_chunks%ROWTYPE;
    v_segment_locations integer[]; v_link_locations integer[];
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.parser_contract_version IS NOT NULL
       AND NEW.parser_contract_version IS DISTINCT FROM OLD.parser_contract_version THEN
        RAISE EXCEPTION 'registered parser contract cannot change or be cleared' USING ERRCODE='23514';
    END IF;
    IF NEW.parser_contract_version IS NULL THEN
        IF NEW.parsed_s3_version_id IS NOT NULL OR NEW.parsed_json_sha256 IS NOT NULL
           OR NEW.canonical_text_sha256 IS NOT NULL OR NEW.canonical_text_length IS NOT NULL
           OR NEW.parser_metadata_json IS NOT NULL THEN
            RAISE EXCEPTION 'new parser result fields require explicit contract opt-in' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.parser_contract_version NOT IN ('a4b.parser_result.v0.1-draft','a4b.parser_result.v0.2') THEN
        RAISE EXCEPTION 'unsupported parser contract' USING ERRCODE='23514';
    END IF;
    v_has_result := NEW.parsed_s3_key IS NOT NULL OR NEW.parsed_s3_version_id IS NOT NULL
        OR NEW.parsed_json_sha256 IS NOT NULL OR NEW.canonical_text_sha256 IS NOT NULL
        OR NEW.canonical_text_length IS NOT NULL OR NEW.parser_metadata_json IS NOT NULL;
    IF v_has_result THEN
        IF NEW.parsed_s3_key IS NULL OR NEW.parsed_json_sha256 IS NULL
           OR NEW.canonical_text_sha256 IS NULL OR NEW.canonical_text_length IS NULL
           OR NEW.parser_metadata_json IS NULL THEN
            RAISE EXCEPTION 'parser result key, both hashes, canonical length and metadata attach atomically' USING ERRCODE='23514';
        END IF;
        IF NEW.status = 'uploaded' THEN
            RAISE EXCEPTION 'parser results require processing before attachment' USING ERRCODE='23514';
        END IF;
        IF NEW.parser_contract_version = 'a4b.parser_result.v0.1-draft' THEN
            IF NOT a4b.parser_contract_metadata_valid(NEW.parser_metadata_json, NEW.content_hash,
                NEW.mime_type, NEW.size_bytes, NEW.processing_config_json) THEN
                RAISE EXCEPTION 'invalid parser metadata, source identity, fixed config or processing coverage' USING ERRCODE='23514';
            END IF;
        ELSE
            IF NOT a4b.unified_parser_metadata_valid(NEW.parser_metadata_json, NEW.content_hash,
                NEW.mime_type, NEW.size_bytes, NEW.processing_config_json) THEN
                RAISE EXCEPTION 'invalid unified parser metadata, profile, fixed config or processing coverage' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.parser_metadata_json #>> '{processing,outcome}' = 'failed'
           AND NEW.canonical_text_length <> 0 THEN
            RAISE EXCEPTION 'failed parser payload cannot contain usable canonical text' USING ERRCODE='23514';
        END IF;
        IF NEW.parser_contract_version = 'a4b.parser_result.v0.2' AND NEW.size_bytes = 0
           AND NEW.canonical_text_length <> 0 THEN
            RAISE EXCEPTION 'zero-byte CSV cannot contain fabricated canonical text' USING ERRCODE='23514';
        END IF;
    END IF;
    -- document_versions.failed covers the whole pipeline, including embedding failures
    -- after parsing succeeded. Preserve that meaning instead of equating it to parser outcome.
    IF NEW.status = 'failed'
       AND jsonb_typeof(NEW.error_json) IS DISTINCT FROM 'object'
       AND NOT a4b.parser_error_valid(NEW.parser_metadata_json #> '{processing,error}') THEN
        RAISE EXCEPTION 'failed parser version requires a pipeline or parser error object' USING ERRCODE='23514';
    END IF;
    IF NEW.status = 'ready' THEN
        IF NOT v_has_result OR NEW.error_json IS NOT NULL
           OR NEW.parser_metadata_json #>> '{processing,scope}' IS DISTINCT FROM 'full_document'
           OR NEW.parser_metadata_json #>> '{processing,outcome}' IS DISTINCT FROM 'succeeded'
           OR NEW.parser_metadata_json #> '{processing,whole_document_complete}' IS DISTINCT FROM 'true'::jsonb
        THEN RAISE EXCEPTION 'ready requires an error-free whole-document succeeded parser result' USING ERRCODE='23514'; END IF;
        IF NEW.parser_contract_version = 'a4b.parser_result.v0.1-draft' THEN
            IF EXISTS (SELECT 1 FROM a4b.source_locations s WHERE s.document_version_id = NEW.id
                AND (s.char_end > NEW.canonical_text_length
                     OR s.page_no > (NEW.parser_metadata_json #>> '{source,page_count_total}')::numeric::bigint)) THEN
                RAISE EXCEPTION 'source location exceeds canonical text or page count' USING ERRCODE='23514';
            END IF;
        ELSE
            IF NEW.size_bytes = 0 AND EXISTS (SELECT 1 FROM a4b.document_chunks
                 WHERE document_version_id=NEW.id) THEN
                RAISE EXCEPTION 'zero-byte CSV cannot publish fabricated nonempty chunks' USING ERRCODE='23514';
            END IF;
            IF EXISTS (SELECT 1 FROM a4b.source_locations s WHERE s.document_version_id = NEW.id
                AND NOT a4b.unified_parser_location_valid(to_jsonb(s),NEW.parser_metadata_json,NEW.canonical_text_length)) THEN
                RAISE EXCEPTION 'source location disagrees with unified profile, canonical bounds or source grid extent' USING ERRCODE='23514';
            END IF;
            IF NEW.parser_metadata_json#>>'{source,profile}' = 'page' THEN
                IF EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.parser_metadata_json#>'{processing,completed_units}') u(value)
                    WHERE NOT EXISTS (SELECT 1 FROM a4b.source_locations s
                        WHERE s.document_version_id=NEW.id AND s.location_type='page'
                          AND s.page_no=(u.value#>>'{}')::numeric::bigint)) THEN
                    RAISE EXCEPTION 'every completed page requires its original page location' USING ERRCODE='23514';
                END IF;
            ELSE
                IF EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.parser_metadata_json#>'{source,tables}') t(value)
                    WHERE (t.value->>'row_count')::numeric > 0
                      AND NOT EXISTS (SELECT 1 FROM a4b.source_locations s
                        WHERE s.document_version_id=NEW.id AND s.location_type='sheet'
                          AND s.sheet_name=t.value->>'sheet_name')) THEN
                    RAISE EXCEPTION 'every nonempty completed source grid requires its original sheet/record location' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
        FOR v_chunk IN SELECT * FROM a4b.document_chunks WHERE document_version_id = NEW.id LOOP
            -- Source locations can be edited during processing: validate again at publication.
            IF NOT a4b.parser_source_segments_valid(v_chunk.source_segments_json,
                v_chunk.content, NEW.id, NEW.canonical_text_length) THEN
                RAISE EXCEPTION 'chunk segments no longer match source locations' USING ERRCODE='23514';
            END IF;
            IF jsonb_array_length(v_chunk.source_segments_json) > 0 THEN
                SELECT array_agg(location_no ORDER BY first_ordinality) INTO v_segment_locations
                FROM (SELECT (e.value ->> 'location_no')::numeric::integer AS location_no,
                             min(e.ordinality) AS first_ordinality
                      FROM jsonb_array_elements(v_chunk.source_segments_json) WITH ORDINALITY AS e(value,ordinality)
                      GROUP BY (e.value ->> 'location_no')::numeric::integer) AS ordered_locations;
                SELECT array_agg(s.location_no ORDER BY cl.location_order) INTO v_link_locations
                FROM a4b.chunk_locations cl JOIN a4b.source_locations s ON s.id = cl.source_location_id
                    AND s.document_version_id = cl.document_version_id
                WHERE cl.chunk_id = v_chunk.id AND cl.document_version_id = NEW.id;
                IF v_segment_locations IS DISTINCT FROM v_link_locations THEN
                    RAISE EXCEPTION 'chunk location links must equal distinct segment locations in first-appearance order' USING ERRCODE='23514';
                END IF;
            END IF;
        END LOOP;
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_parser_contract_version() OWNER TO postgres;

--
-- Name: guard_run_document(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.guard_run_document() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$ DECLARE v_run a4b.workflow_runs%ROWTYPE; v_run_id uuid;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'replace run documents before starting; do not update a pinned row' USING ERRCODE = '23514';
    END IF;
    v_run_id := CASE WHEN TG_OP='DELETE' THEN OLD.run_id ELSE NEW.run_id END;
    SELECT * INTO v_run FROM a4b.workflow_runs WHERE id=v_run_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'run not found' USING ERRCODE = '23503'; END IF;
    IF v_run.status <> 'queued' OR v_run.started_at IS NOT NULL THEN
        RAISE EXCEPTION 'documents are pinned once a run starts' USING ERRCODE = '23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    PERFORM 1 FROM a4b.document_versions v JOIN a4b.documents d ON d.id=v.document_id
        WHERE v.id=NEW.document_version_id AND v.status='ready' AND d.archived_at IS NULL FOR SHARE OF d;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'run document must be ready and non-archived' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_run_document() OWNER TO postgres;

--
-- Name: guard_source_location(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.guard_source_location() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$ DECLARE v_status text; v_id uuid;
BEGIN
    v_id := CASE WHEN TG_OP='DELETE' THEN OLD.document_version_id ELSE NEW.document_version_id END;
    IF TG_OP='UPDATE' AND ROW(NEW.id,NEW.document_version_id,NEW.created_at)
        IS DISTINCT FROM ROW(OLD.id,OLD.document_version_id,OLD.created_at) THEN
        RAISE EXCEPTION 'source location identity is immutable' USING ERRCODE='23514';
    END IF;
    SELECT status INTO v_status FROM a4b.document_versions WHERE id=v_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'document version not found' USING ERRCODE='23503'; END IF;
    IF v_status <> 'processing' THEN RAISE EXCEPTION 'source locations are editable only while processing' USING ERRCODE='23514'; END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_source_location() OWNER TO postgres;

--
-- Name: guard_workflow_resource(); Type: FUNCTION; Schema: a4b; Owner: a4b_guard_owner
--

CREATE FUNCTION a4b.guard_workflow_resource() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$ BEGIN
    PERFORM 1 FROM a4b.workflow_versions WHERE id = NEW.workflow_version_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'workflow version not found' USING ERRCODE = '23503'; END IF;
    IF EXISTS (SELECT 1 FROM a4b.workflow_runs WHERE workflow_version_id = NEW.workflow_version_id) THEN
        RAISE EXCEPTION 'resources cannot be appended after a run exists' USING ERRCODE = '23514';
    END IF;
    PERFORM 1 FROM a4b.document_versions v JOIN a4b.documents d ON d.id = v.document_id
        WHERE v.id = NEW.document_version_id AND v.status = 'ready' AND d.archived_at IS NULL
        FOR SHARE OF d;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'resource must be a ready, non-archived document version' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_workflow_resource() OWNER TO a4b_guard_owner;

--
-- Name: guard_workflow_run(); Type: FUNCTION; Schema: a4b; Owner: a4b_guard_owner
--

CREATE FUNCTION a4b.guard_workflow_run() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$ BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'queued' OR NEW.started_at IS NOT NULL OR NEW.state_revision <> 0
           OR NEW.lease_owner IS NOT NULL THEN
            RAISE EXCEPTION 'new runs must be queued with revision zero' USING ERRCODE = '23514';
        END IF;
        PERFORM 1 FROM a4b.workflow_versions WHERE id = NEW.workflow_version_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'workflow version not found' USING ERRCODE = '23503'; END IF;
        PERFORM 1 FROM a4b.workflow_versions v JOIN a4b.workflows w ON w.id=v.workflow_id
            WHERE v.id=NEW.workflow_version_id AND w.status='active' FOR SHARE OF w;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'workflow is archived' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'run history cannot be deleted by the application' USING ERRCODE = '23514';
    END IF;
    IF (to_jsonb(NEW) - ARRAY['status','state_json','state_revision','lease_owner','lease_expires_at','started_at','finished_at'])
        IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','state_json','state_revision','lease_owner','lease_expires_at','started_at','finished_at']) THEN
        RAISE EXCEPTION 'run identity/version/input is immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('succeeded','failed','canceled') AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'terminal run is immutable; start a new run' USING ERRCODE = '23514';
    END IF;
    IF OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at THEN
        RAISE EXCEPTION 'started_at is immutable after start' USING ERRCODE = '23514';
    END IF;
    IF ROW(NEW.status,NEW.state_json,NEW.lease_owner) IS DISTINCT FROM ROW(OLD.status,OLD.state_json,OLD.lease_owner) THEN
        IF NEW.state_revision <> OLD.state_revision + 1 THEN
            RAISE EXCEPTION 'state revision must increase by one' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.state_revision NOT IN (OLD.state_revision, OLD.state_revision + 1) THEN
        RAISE EXCEPTION 'state revision cannot skip or move backward' USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'waiting_input' AND EXISTS (
        SELECT 1 FROM a4b.approval_requests WHERE run_id=OLD.id AND status='pending'
    ) THEN
        RAISE EXCEPTION 'input and approval cannot wait together' USING ERRCODE = '23514';
    END IF;
    IF NEW.status <> 'waiting_approval' AND EXISTS (
        SELECT 1 FROM a4b.approval_requests WHERE run_id=OLD.id AND status='pending'
    ) THEN
        RAISE EXCEPTION 'close the pending approval before resuming the run' USING ERRCODE = '23514';
    END IF;
    IF NEW.state_revision <> OLD.state_revision AND EXISTS (
        SELECT 1 FROM a4b.approval_requests WHERE run_id=OLD.id AND status='pending'
    ) THEN
        RAISE EXCEPTION 'close the pending approval before advancing its state revision' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.guard_workflow_run() OWNER TO a4b_guard_owner;

--
-- Name: json_pointer_exists(jsonb, text); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.json_pointer_exists(p_value jsonb, p_pointer text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $_$ DECLARE v jsonb := p_value; part text; parts text[];
BEGIN
    IF p_value IS NULL OR p_pointer IS NULL THEN RETURN false; END IF;
    IF p_pointer='' THEN RETURN true; END IF;
    IF p_pointer !~ '^(/([^~/]|~[01])*)+$' THEN RETURN false; END IF;
    parts := CASE WHEN p_pointer='/' THEN ARRAY[''] ELSE string_to_array(substr(p_pointer,2),'/') END;
    FOREACH part IN ARRAY parts LOOP
        part := replace(replace(part,'~1','/'),'~0','~');
        IF jsonb_typeof(v)='object' THEN
            IF NOT v ? part THEN RETURN false; END IF;
            v := v -> part;
        ELSIF jsonb_typeof(v)='array' THEN
            IF part !~ '^(0|[1-9][0-9]*)$' OR length(part)>9 THEN RETURN false; END IF;
            IF part::integer >= jsonb_array_length(v) THEN RETURN false; END IF;
            v := v -> part::integer;
        ELSE RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END $_$;


ALTER FUNCTION a4b.json_pointer_exists(p_value jsonb, p_pointer text) OWNER TO postgres;

--
-- Name: parser_contract_metadata_valid(jsonb, text, text, bigint, jsonb); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.parser_contract_metadata_valid(p_metadata jsonb, p_content_hash text, p_mime_type text, p_size_bytes bigint, p_processing_config jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE
    v_source jsonb; v_processing jsonb; v_parser jsonb; v_provenance jsonb;
    v_item jsonb; v_page_count bigint; v_requested jsonb; v_completed jsonb; v_failed jsonb;
    v_req_count bigint; v_done_count bigint; v_failed_count bigint; v_outcome text; v_scope text;
    v_whole boolean;
BEGIN
    IF jsonb_typeof(p_metadata) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF NOT p_metadata ?& ARRAY['source','processing','coordinate_system','text_indexing']
       OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_metadata) AS k(key)
                  WHERE key NOT IN ('source','processing','coordinate_system','text_indexing'))
    THEN RETURN false; END IF;
    v_source := p_metadata -> 'source'; v_processing := p_metadata -> 'processing';
    IF jsonb_typeof(v_source) IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_processing) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF p_metadata -> 'coordinate_system' IS DISTINCT FROM
        '{"origin":"top_left","unit":"normalized_0_1","bbox_meaning":"visible_text_bounds"}'::jsonb
       OR p_metadata -> 'text_indexing' IS DISTINCT FROM
        '{"unit":"unicode_code_point","start_index":0,"end":"exclusive","normalization":"none"}'::jsonb
    THEN RETURN false; END IF;
    IF NOT v_source ?& ARRAY['file_name','mime_type','sha256','size_bytes','page_count_total']
       OR EXISTS (SELECT 1 FROM jsonb_object_keys(v_source) AS k(key)
                  WHERE key NOT IN ('file_name','mime_type','sha256','size_bytes','page_count_total'))
       OR jsonb_typeof(v_source -> 'file_name') IS DISTINCT FROM 'string'
       OR jsonb_typeof(v_source -> 'mime_type') IS DISTINCT FROM 'string'
       OR jsonb_typeof(v_source -> 'sha256') IS DISTINCT FROM 'string'
       OR coalesce(v_source ->> 'mime_type' NOT IN ('application/pdf','image/png','image/jpeg'), true)
       OR (v_source ->> 'sha256') IS DISTINCT FROM p_content_hash
       OR coalesce((v_source ->> 'sha256') !~ '^[0-9a-f]{64}$', true)
       OR (v_source ->> 'mime_type') IS DISTINCT FROM p_mime_type
       OR NOT a4b.parser_json_integer_valid(v_source -> 'size_bytes', 1, 9223372036854775807)
       OR NOT a4b.parser_json_integer_valid(v_source -> 'page_count_total', 1, 2147483647)
    THEN RETURN false; END IF;
    IF (v_source ->> 'size_bytes')::numeric IS DISTINCT FROM p_size_bytes::numeric THEN RETURN false; END IF;
    v_page_count := (v_source ->> 'page_count_total')::numeric::bigint;
    IF (v_source ->> 'mime_type') IN ('image/png','image/jpeg') AND v_page_count <> 1 THEN RETURN false; END IF;
    IF NOT v_processing ?& ARRAY['outcome','scope','requested_pages','completed_pages','failed_pages',
        'whole_document_complete','parser','config_json','provenance','warnings','error']
       OR EXISTS (SELECT 1 FROM jsonb_object_keys(v_processing) AS k(key)
                  WHERE key NOT IN ('outcome','scope','requested_pages','completed_pages','failed_pages',
                    'whole_document_complete','parser','config_json','provenance','warnings','error'))
       OR jsonb_typeof(v_processing -> 'outcome') IS DISTINCT FROM 'string'
       OR jsonb_typeof(v_processing -> 'scope') IS DISTINCT FROM 'string'
       OR jsonb_typeof(v_processing -> 'whole_document_complete') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(v_processing -> 'config_json') IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_processing_config) IS DISTINCT FROM 'object'
       OR v_processing -> 'config_json' IS DISTINCT FROM p_processing_config
       OR jsonb_typeof(v_processing -> 'warnings') IS DISTINCT FROM 'array'
    THEN RETURN false; END IF;
    v_outcome := v_processing ->> 'outcome'; v_scope := v_processing ->> 'scope';
    IF v_outcome NOT IN ('succeeded','partial','failed') OR v_scope NOT IN ('full_document','selected_pages')
    THEN RETURN false; END IF;
    v_requested := v_processing -> 'requested_pages';
    v_completed := v_processing -> 'completed_pages'; v_failed := v_processing -> 'failed_pages';
    IF NOT a4b.parser_page_numbers_valid(v_requested, v_page_count, false)
       OR NOT a4b.parser_page_numbers_valid(v_completed, v_page_count, true)
       OR NOT a4b.parser_page_numbers_valid(v_failed, v_page_count, true) THEN RETURN false; END IF;
    -- Arrays are already validated: numeric integer casts are safe and equality is set-based.
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_completed) AS d(value)
               WHERE NOT v_requested @> jsonb_build_array(d.value) OR v_failed @> jsonb_build_array(d.value))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_failed) AS f(value)
                  WHERE NOT v_requested @> jsonb_build_array(f.value))
    THEN RETURN false; END IF;
    v_req_count := jsonb_array_length(v_requested); v_done_count := jsonb_array_length(v_completed);
    v_failed_count := jsonb_array_length(v_failed);
    IF v_req_count <> v_done_count + v_failed_count THEN RETURN false; END IF;
    -- All page numbers are unique and within 1..N. Count=N proves exact coverage;
    -- never generate_series(1,N), including for very large/malicious declared N.
    IF v_scope = 'full_document' AND v_req_count <> v_page_count THEN RETURN false; END IF;
    v_whole := v_scope = 'full_document' AND v_failed_count = 0 AND v_done_count = v_page_count;
    IF v_processing -> 'whole_document_complete' IS DISTINCT FROM to_jsonb(v_whole) THEN RETURN false; END IF;
    IF v_outcome = 'succeeded' THEN
        IF v_failed_count <> 0 OR v_processing -> 'error' IS DISTINCT FROM 'null'::jsonb THEN RETURN false; END IF;
    ELSIF v_outcome = 'partial' THEN
        IF v_done_count = 0 OR v_failed_count = 0 THEN RETURN false; END IF;
        IF v_processing -> 'error' IS DISTINCT FROM 'null'::jsonb
           AND NOT a4b.parser_error_valid(v_processing -> 'error') THEN RETURN false; END IF;
    ELSE
        IF v_done_count <> 0 OR v_failed_count <> v_req_count
           OR NOT a4b.parser_error_valid(v_processing -> 'error') THEN RETURN false; END IF;
    END IF;
    v_parser := v_processing -> 'parser'; v_provenance := v_processing -> 'provenance';
    IF jsonb_typeof(v_parser) IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_provenance) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF NOT v_parser ?& ARRAY['engine','package_version','postprocessor_revision']
       OR EXISTS (SELECT 1 FROM jsonb_object_keys(v_parser) AS k(key)
                  WHERE key NOT IN ('engine','package_version','postprocessor_revision'))
       OR v_parser -> 'engine' IS DISTINCT FROM '"docling"'::jsonb
       OR coalesce(jsonb_typeof(v_parser -> 'package_version') NOT IN ('string','null'), true)
       OR jsonb_typeof(v_parser -> 'postprocessor_revision') IS DISTINCT FROM 'string'
       OR coalesce((v_parser ->> 'postprocessor_revision') !~ '^[0-9a-f]{64}$', true)
    THEN RETURN false; END IF;
    IF NOT v_provenance ?& ARRAY['derivation','service_integration','source_artifacts']
       OR EXISTS (SELECT 1 FROM jsonb_object_keys(v_provenance) AS k(key)
                  WHERE key NOT IN ('derivation','service_integration','source_artifacts'))
       OR jsonb_typeof(v_provenance -> 'derivation') IS DISTINCT FROM 'string'
       OR coalesce(v_provenance ->> 'derivation' NOT IN
           ('native_runtime','adapter_replay_from_verified_artifacts','illustrative_example'), true)
       OR jsonb_typeof(v_provenance -> 'service_integration') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(v_provenance -> 'source_artifacts') IS DISTINCT FROM 'array'
    THEN RETURN false; END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_provenance -> 'source_artifacts') LOOP
        IF jsonb_typeof(v_item) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
        IF NOT v_item ?& ARRAY['name','sha256']
           OR EXISTS (SELECT 1 FROM jsonb_object_keys(v_item) AS k(key) WHERE key NOT IN ('name','sha256'))
           OR jsonb_typeof(v_item -> 'name') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_item -> 'sha256') IS DISTINCT FROM 'string'
           OR coalesce((v_item ->> 'sha256') !~ '^[0-9a-f]{64}$', true) THEN RETURN false; END IF;
    END LOOP;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_processing -> 'warnings') LOOP
        IF jsonb_typeof(v_item) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
        IF NOT v_item ?& ARRAY['code','severity','message']
           OR EXISTS (SELECT 1 FROM jsonb_object_keys(v_item) AS k(key) WHERE key NOT IN ('code','severity','message'))
           OR jsonb_typeof(v_item -> 'code') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_item -> 'severity') IS DISTINCT FROM 'string'
           OR coalesce(v_item ->> 'severity' NOT IN ('info','warning','error'), true)
           OR jsonb_typeof(v_item -> 'message') IS DISTINCT FROM 'string' THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation THEN RETURN false;
END $_$;


ALTER FUNCTION a4b.parser_contract_metadata_valid(p_metadata jsonb, p_content_hash text, p_mime_type text, p_size_bytes bigint, p_processing_config jsonb) OWNER TO postgres;

--
-- Name: parser_error_valid(jsonb); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.parser_error_valid(p_error jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
    IF jsonb_typeof(p_error) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    RETURN coalesce(p_error ?& ARRAY['code','message','retryable']
        AND NOT EXISTS (SELECT 1 FROM jsonb_object_keys(p_error) AS k(key)
                        WHERE key NOT IN ('code','message','retryable'))
        AND jsonb_typeof(p_error -> 'code') = 'string'
        AND jsonb_typeof(p_error -> 'message') = 'string'
        AND jsonb_typeof(p_error -> 'retryable') = 'boolean', false);
END $$;


ALTER FUNCTION a4b.parser_error_valid(p_error jsonb) OWNER TO postgres;

--
-- Name: parser_json_integer_valid(jsonb, bigint, bigint); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.parser_json_integer_valid(p_value jsonb, p_min bigint, p_max bigint) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v_number numeric;
BEGIN
    IF jsonb_typeof(p_value) IS DISTINCT FROM 'number'
       OR p_min IS NULL OR p_max IS NULL OR p_min > p_max THEN RETURN false; END IF;
    v_number := (p_value #>> '{}')::numeric;
    RETURN coalesce(v_number = trunc(v_number) AND v_number >= p_min AND v_number <= p_max, false);
EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation THEN RETURN false;
END $$;


ALTER FUNCTION a4b.parser_json_integer_valid(p_value jsonb, p_min bigint, p_max bigint) OWNER TO postgres;

--
-- Name: parser_page_numbers_valid(jsonb, bigint, boolean); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.parser_page_numbers_valid(p_pages jsonb, p_page_count bigint, p_allow_empty boolean) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v_page jsonb;
BEGIN
    IF jsonb_typeof(p_pages) IS DISTINCT FROM 'array'
       OR p_page_count IS NULL OR p_page_count < 1 OR p_allow_empty IS NULL THEN RETURN false; END IF;
    IF NOT p_allow_empty AND jsonb_array_length(p_pages) = 0 THEN RETURN false; END IF;
    FOR v_page IN SELECT value FROM jsonb_array_elements(p_pages) LOOP
        IF NOT a4b.parser_json_integer_valid(v_page, 1, p_page_count) THEN RETURN false; END IF;
    END LOOP;
    RETURN (SELECT count(*) = count(DISTINCT value) FROM jsonb_array_elements(p_pages));
END $$;


ALTER FUNCTION a4b.parser_page_numbers_valid(p_pages jsonb, p_page_count bigint, p_allow_empty boolean) OWNER TO postgres;

--
-- Name: parser_source_segments_valid(jsonb, text, uuid, bigint); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.parser_source_segments_valid(p_segments jsonb, p_content text, p_document_version_id uuid, p_canonical_length bigint) RETURNS boolean
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE
    v_segment jsonb; v_content_start bigint; v_content_end bigint;
    v_source_start bigint; v_source_end bigint; v_previous_end bigint := 0; v_location_no integer;
    v_location a4b.source_locations%ROWTYPE;
BEGIN
    IF jsonb_typeof(p_segments) IS DISTINCT FROM 'array' OR p_content IS NULL
       OR p_document_version_id IS NULL OR (p_canonical_length IS NOT NULL AND p_canonical_length < 0)
    THEN RETURN false; END IF;
    FOR v_segment IN SELECT value FROM jsonb_array_elements(p_segments) LOOP
        IF jsonb_typeof(v_segment) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
        IF NOT v_segment ?& ARRAY['content_start','content_end','source_char_start','source_char_end','location_no']
           OR EXISTS (SELECT 1 FROM jsonb_object_keys(v_segment) AS k(key)
                      WHERE key NOT IN ('content_start','content_end','source_char_start','source_char_end','location_no'))
           OR NOT a4b.parser_json_integer_valid(v_segment -> 'content_start', 0, char_length(p_content))
           OR NOT a4b.parser_json_integer_valid(v_segment -> 'content_end', 0, char_length(p_content))
           OR NOT a4b.parser_json_integer_valid(v_segment -> 'source_char_start', 0, 9223372036854775807)
           OR NOT a4b.parser_json_integer_valid(v_segment -> 'source_char_end', 0, 9223372036854775807)
           OR NOT a4b.parser_json_integer_valid(v_segment -> 'location_no', 1, 2147483647)
        THEN RETURN false; END IF;
        v_content_start := (v_segment ->> 'content_start')::numeric::bigint;
        v_content_end := (v_segment ->> 'content_end')::numeric::bigint;
        v_source_start := (v_segment ->> 'source_char_start')::numeric::bigint;
        v_source_end := (v_segment ->> 'source_char_end')::numeric::bigint;
        v_location_no := (v_segment ->> 'location_no')::numeric::integer;
        IF v_content_start < v_previous_end OR v_content_end <= v_content_start
           OR v_source_end <= v_source_start
           OR v_content_end - v_content_start <> v_source_end - v_source_start
           OR (p_canonical_length IS NOT NULL AND v_source_end > p_canonical_length)
        THEN RETURN false; END IF;
        SELECT * INTO v_location FROM a4b.source_locations
            WHERE document_version_id = p_document_version_id AND location_no = v_location_no;
        IF NOT FOUND OR v_location.char_start IS NULL OR v_location.char_end IS NULL
           OR v_source_start < v_location.char_start OR v_source_end > v_location.char_end
        THEN RETURN false; END IF;
        v_previous_end := v_content_end;
    END LOOP;
    RETURN true;
EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation THEN RETURN false;
END $$;


ALTER FUNCTION a4b.parser_source_segments_valid(p_segments jsonb, p_content text, p_document_version_id uuid, p_canonical_length bigint) OWNER TO postgres;

--
-- Name: reject_snapshot_change(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.reject_snapshot_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$ BEGIN
    RAISE EXCEPTION '% is immutable; create a new version/revision instead', TG_TABLE_NAME
        USING ERRCODE = '23514';
END $$;


ALTER FUNCTION a4b.reject_snapshot_change() OWNER TO postgres;

--
-- Name: search_document_chunks(uuid, uuid, extensions.vector, jsonb, integer); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.search_document_chunks(p_actor_id uuid, p_run_id uuid, p_query extensions.vector, p_embedding_profile jsonb, p_limit integer DEFAULT 5) RETURNS TABLE(chunk_id uuid, document_version_id uuid, content text, location_json jsonb, cosine_distance double precision)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog', 'a4b', 'extensions'
    AS $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM a4b.users WHERE id=p_actor_id AND status='active') THEN
        RAISE EXCEPTION 'active team member required' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM a4b.workflow_runs WHERE id=p_run_id) THEN
        RAISE EXCEPTION 'run not found' USING ERRCODE='23503';
    END IF;
    IF p_query IS NULL OR extensions.vector_dims(p_query) <> 1024 OR extensions.vector_norm(p_query) = 0
       OR NOT a4b.valid_embedding_profile(p_embedding_profile) THEN
        RAISE EXCEPTION 'query vector/profile must match the configured model dimension' USING ERRCODE='22023';
    END IF;
    IF p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
        RAISE EXCEPTION 'limit must be between 1 and 100' USING ERRCODE='22023';
    END IF;
    RETURN QUERY
    SELECT ch.id, ch.document_version_id, ch.content,
           jsonb_build_object('locations', (SELECT jsonb_agg(
               jsonb_strip_nulls(to_jsonb(sl) - 'created_at') ORDER BY cl.location_order)
               FROM a4b.chunk_locations cl JOIN a4b.source_locations sl ON sl.id=cl.source_location_id
               WHERE cl.chunk_id=ch.id)),
           ch.embedding OPERATOR(extensions.<=>) p_query
    FROM a4b.document_chunks AS ch
    JOIN a4b.document_versions AS dv ON dv.id=ch.document_version_id
    JOIN a4b.documents AS d ON d.id=dv.document_id
    WHERE ch.embedding_status='ready' AND dv.status='ready' AND d.archived_at IS NULL
      AND dv.processing_config_json -> 'embedding' = p_embedding_profile
      AND EXISTS (SELECT 1 FROM a4b.run_documents rd
                  WHERE rd.run_id=p_run_id AND rd.document_version_id=ch.document_version_id)
    ORDER BY ch.embedding OPERATOR(extensions.<=>) p_query
    LIMIT p_limit;
END $$;


ALTER FUNCTION a4b.search_document_chunks(p_actor_id uuid, p_run_id uuid, p_query extensions.vector, p_embedding_profile jsonb, p_limit integer) OWNER TO postgres;

--
-- Name: supported_extraction_schema(jsonb); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.supported_extraction_schema(p_schema jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE
    v_queue jsonb[] := ARRAY[p_schema];
    v_index integer := 1;
    v_node jsonb;
    v_type text;
    v_key text;
    v_child jsonb;
    v_item jsonb;
    v_number numeric;
BEGIN
    IF p_schema IS NULL OR jsonb_typeof(p_schema) <> 'object'
       OR p_schema -> 'type' IS DISTINCT FROM '"object"'::jsonb THEN
        RETURN false;
    END IF;
    WHILE v_index <= cardinality(v_queue) LOOP
        v_node := v_queue[v_index];
        v_index := v_index + 1;
        IF jsonb_typeof(v_node) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
        FOR v_key IN SELECT jsonb_object_keys(v_node) LOOP
            IF v_key NOT IN ('type','properties','required','additionalProperties','items',
                'minItems','maxItems','minLength','maxLength','enum','minimum','maximum',
                'format','$schema','title','description') THEN RETURN false; END IF;
        END LOOP;

        IF jsonb_typeof(v_node -> 'type') = 'string' THEN
            v_type := v_node ->> 'type';
        ELSIF jsonb_typeof(v_node -> 'type') = 'array' THEN
            IF jsonb_array_length(v_node -> 'type') <> 2
               OR NOT (v_node -> 'type' @> '["null"]'::jsonb)
               OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_node -> 'type') e
                          WHERE jsonb_typeof(e) <> 'string')
               OR (SELECT count(DISTINCT e) FROM jsonb_array_elements(v_node -> 'type') e) <> 2
            THEN RETURN false; END IF;
            SELECT value #>> '{}' INTO v_type
            FROM jsonb_array_elements(v_node -> 'type') WHERE value <> '"null"'::jsonb;
        ELSE RETURN false;
        END IF;
        IF v_type NOT IN ('string','number','integer','boolean','null','object','array')
        THEN RETURN false; END IF;

        IF v_node ? '$schema' AND v_node -> '$schema' IS DISTINCT FROM
            '"https://json-schema.org/draft/2020-12/schema"'::jsonb THEN RETURN false; END IF;
        FOREACH v_key IN ARRAY ARRAY['title','description'] LOOP
            IF v_node ? v_key AND jsonb_typeof(v_node -> v_key) <> 'string' THEN RETURN false; END IF;
        END LOOP;
        IF v_node ? 'enum' THEN
            IF jsonb_typeof(v_node -> 'enum') <> 'array' THEN RETURN false; END IF;
            IF jsonb_array_length(v_node -> 'enum') = 0
               OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(v_node -> 'enum'))
                    <> jsonb_array_length(v_node -> 'enum') THEN RETURN false; END IF;
        END IF;

        IF v_type <> 'object' AND v_node ?| ARRAY['properties','required','additionalProperties']
        THEN RETURN false; END IF;
        IF v_type = 'object' THEN
            IF v_node ? 'properties' THEN
                IF jsonb_typeof(v_node -> 'properties') <> 'object' THEN RETURN false; END IF;
                FOR v_child IN SELECT value FROM jsonb_each(v_node -> 'properties') LOOP
                    IF jsonb_typeof(v_child) <> 'object' THEN RETURN false; END IF;
                    v_queue := array_append(v_queue, v_child);
                END LOOP;
            END IF;
            IF v_node ? 'required' THEN
                IF jsonb_typeof(v_node -> 'required') <> 'array' THEN RETURN false; END IF;
                IF (SELECT count(DISTINCT value) FROM jsonb_array_elements(v_node -> 'required'))
                    <> jsonb_array_length(v_node -> 'required') THEN RETURN false; END IF;
                FOR v_item IN SELECT value FROM jsonb_array_elements(v_node -> 'required') LOOP
                    IF jsonb_typeof(v_item) <> 'string'
                       OR NOT coalesce((v_node -> 'properties') ? (v_item #>> '{}'), false)
                    THEN RETURN false; END IF;
                END LOOP;
            END IF;
            IF v_node ? 'additionalProperties'
               AND jsonb_typeof(v_node -> 'additionalProperties') <> 'boolean'
            THEN RETURN false; END IF;
        END IF;

        IF v_type <> 'array' AND v_node ?| ARRAY['items','minItems','maxItems']
        THEN RETURN false; END IF;
        IF v_type = 'array' AND v_node ? 'items' THEN
            IF jsonb_typeof(v_node -> 'items') <> 'object' THEN RETURN false; END IF;
            v_queue := array_append(v_queue, v_node -> 'items');
        END IF;
        IF v_type <> 'string' AND v_node ?| ARRAY['minLength','maxLength','format']
        THEN RETURN false; END IF;
        IF v_node ? 'format' AND v_node -> 'format' IS DISTINCT FROM '"date"'::jsonb
        THEN RETURN false; END IF;
        IF v_type NOT IN ('integer','number') AND v_node ?| ARRAY['minimum','maximum']
        THEN RETURN false; END IF;
        FOREACH v_key IN ARRAY ARRAY['minItems','maxItems','minLength','maxLength','minimum','maximum'] LOOP
            IF v_node ? v_key THEN
                IF jsonb_typeof(v_node -> v_key) <> 'number' THEN RETURN false; END IF;
                v_number := (v_node ->> v_key)::numeric;
                IF v_key IN ('minItems','maxItems','minLength','maxLength')
                   AND (v_number < 0 OR trunc(v_number) <> v_number) THEN RETURN false; END IF;
            END IF;
        END LOOP;
        IF v_node ?& ARRAY['minItems','maxItems']
           AND (v_node ->> 'minItems')::numeric > (v_node ->> 'maxItems')::numeric THEN RETURN false; END IF;
        IF v_node ?& ARRAY['minLength','maxLength']
           AND (v_node ->> 'minLength')::numeric > (v_node ->> 'maxLength')::numeric THEN RETURN false; END IF;
        IF v_node ?& ARRAY['minimum','maximum']
           AND (v_node ->> 'minimum')::numeric > (v_node ->> 'maximum')::numeric THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
END $_$;


ALTER FUNCTION a4b.supported_extraction_schema(p_schema jsonb) OWNER TO postgres;

--
-- Name: touch_editable_row(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.touch_editable_row() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$ BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
        RAISE EXCEPTION 'id/creator cannot change' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'design_sessions' THEN
        IF NEW.workflow_id IS DISTINCT FROM OLD.workflow_id THEN
            RAISE EXCEPTION 'session workflow cannot change' USING ERRCODE = '23514';
        END IF;
        IF NEW.revision <> OLD.revision + 1 THEN
            RAISE EXCEPTION 'session revision must increase by one' USING ERRCODE = '23514';
        END IF;
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$;


ALTER FUNCTION a4b.touch_editable_row() OWNER TO postgres;

--
-- Name: unified_parser_keys_valid(jsonb, text[]); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.unified_parser_keys_valid(p_object jsonb, p_keys text[]) RETURNS boolean
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$ SELECT CASE WHEN jsonb_typeof(p_object) = 'object' AND p_keys IS NOT NULL
             THEN p_object ?& p_keys
                  AND NOT EXISTS (SELECT 1 FROM jsonb_object_keys(p_object) k(key)
                                  WHERE NOT (key = ANY(p_keys)))
             ELSE false END $$;


ALTER FUNCTION a4b.unified_parser_keys_valid(p_object jsonb, p_keys text[]) OWNER TO postgres;

--
-- Name: unified_parser_location_valid(jsonb, jsonb, bigint); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.unified_parser_location_valid(p_location jsonb, p_metadata jsonb, p_canonical_length bigint) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v_profile text; v_type text; v_table jsonb; v_page bigint;
BEGIN
    IF jsonb_typeof(p_location) IS DISTINCT FROM 'object' OR p_canonical_length IS NULL
       OR p_canonical_length < 0 THEN RETURN false; END IF;
    v_profile := p_metadata#>>'{source,profile}'; v_type := p_location->>'location_type';
    IF p_location->>'char_end' IS NOT NULL THEN
        IF NOT a4b.parser_json_integer_valid(p_location->'char_end',1,p_canonical_length)
           OR NOT a4b.parser_json_integer_valid(p_location->'char_start',0,p_canonical_length)
           OR (p_location->>'char_start')::numeric >= (p_location->>'char_end')::numeric
        THEN RETURN false; END IF;
    ELSIF p_location->>'char_start' IS NOT NULL THEN RETURN false;
    END IF;
    IF v_type = 'document' THEN RETURN true; END IF;
    IF v_profile = 'page' AND v_type = 'page' THEN
        IF NOT a4b.parser_json_integer_valid(p_location->'page_no',1,
            (p_metadata#>>'{source,page_count_total}')::numeric::bigint) THEN RETURN false; END IF;
        v_page := (p_location->>'page_no')::numeric::bigint;
        RETURN coalesce((p_metadata#>'{processing,completed_units}' @> jsonb_build_array(v_page))
            OR (p_metadata#>'{processing,partial_units}' @> jsonb_build_array(v_page)),false);
    ELSIF v_profile = 'grid' AND v_type = 'sheet' THEN
        SELECT value INTO v_table FROM jsonb_array_elements(p_metadata#>'{source,tables}')
            WHERE value->>'sheet_name' = p_location->>'sheet_name';
        IF NOT FOUND THEN RETURN false; END IF;
        IF NOT a4b.parser_json_integer_valid(p_location->'row_start',1,(v_table->>'row_count')::numeric::bigint)
           OR NOT a4b.parser_json_integer_valid(p_location->'row_end',1,(v_table->>'row_count')::numeric::bigint)
           OR NOT a4b.parser_json_integer_valid(p_location->'col_start',1,(v_table->>'column_count')::numeric::bigint)
           OR NOT a4b.parser_json_integer_valid(p_location->'col_end',1,(v_table->>'column_count')::numeric::bigint)
           OR (p_location->>'row_start')::numeric > (p_location->>'row_end')::numeric
           OR (p_location->>'col_start')::numeric > (p_location->>'col_end')::numeric
        THEN RETURN false; END IF;
        RETURN coalesce((p_metadata#>'{processing,completed_units}' @> jsonb_build_array(v_table->>'table_id'))
            OR (p_metadata#>'{processing,partial_units}' @> jsonb_build_array(v_table->>'table_id')),false);
    END IF;
    RETURN false;
EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation OR invalid_parameter_value THEN RETURN false;
END $$;


ALTER FUNCTION a4b.unified_parser_location_valid(p_location jsonb, p_metadata jsonb, p_canonical_length bigint) OWNER TO postgres;

--
-- Name: unified_parser_metadata_valid(jsonb, text, text, bigint, jsonb); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.unified_parser_metadata_valid(p_metadata jsonb, p_content_hash text, p_mime_type text, p_size_bytes bigint, p_processing_config jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE
    v_source jsonb; v_processing jsonb; v_profile text; v_mime text;
    v_tables jsonb; v_table jsonb; v_requested jsonb; v_completed jsonb;
    v_partial jsonb; v_failed jsonb; v_item jsonb; v_parser jsonb;
    v_provenance jsonb; v_policy jsonb; v_unit jsonb; v_outcome text;
    v_total bigint; v_req bigint; v_done bigint; v_part bigint; v_fail bigint;
    v_rows bigint; v_cols bigint; v_whole boolean;
BEGIN
    IF NOT a4b.unified_parser_keys_valid(p_metadata,
        ARRAY['source','processing','coordinate_system','text_indexing']) THEN RETURN false; END IF;
    v_source := p_metadata->'source'; v_processing := p_metadata->'processing';
    IF NOT a4b.unified_parser_keys_valid(v_source,
        ARRAY['file_name','mime_type','sha256','size_bytes','profile','page_count_total','tables'])
       OR NOT a4b.unified_parser_text_valid(v_source->'file_name',1024)
       OR jsonb_typeof(v_source->'sha256') IS DISTINCT FROM 'string'
       OR v_source->>'sha256' IS DISTINCT FROM p_content_hash
       OR coalesce((v_source->>'sha256') !~ '^[0-9a-f]{64}$',true)
       OR jsonb_typeof(v_source->'mime_type') IS DISTINCT FROM 'string'
       OR v_source->>'mime_type' IS DISTINCT FROM p_mime_type
       OR NOT a4b.parser_json_integer_valid(v_source->'size_bytes',0,9223372036854775807)
       OR (v_source->>'size_bytes')::numeric IS DISTINCT FROM p_size_bytes::numeric
    THEN RETURN false; END IF;
    IF p_metadata->'text_indexing' IS DISTINCT FROM
       '{"unit":"unicode_code_point","start_index":0,"end":"exclusive","normalization":"none"}'::jsonb
    THEN RETURN false; END IF;
    v_profile := v_source->>'profile'; v_mime := v_source->>'mime_type';
    v_tables := v_source->'tables';
    IF jsonb_typeof(v_tables) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
    IF v_profile = 'page' THEN
        IF v_mime NOT IN ('application/pdf','image/png','image/jpeg')
           OR (v_source->>'size_bytes')::numeric <= 0
           OR NOT a4b.parser_json_integer_valid(v_source->'page_count_total',1,2147483647)
           OR jsonb_array_length(v_tables) <> 0
           OR p_metadata->'coordinate_system' IS DISTINCT FROM
             '{"origin":"top_left","unit":"normalized_0_1","bbox_meaning":"visible_text_bounds"}'::jsonb
        THEN RETURN false; END IF;
        v_total := (v_source->>'page_count_total')::numeric::bigint;
        IF v_mime IN ('image/png','image/jpeg') AND v_total <> 1 THEN RETURN false; END IF;
    ELSIF v_profile = 'grid' THEN
        IF v_mime NOT IN ('text/csv','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
           OR v_source->'page_count_total' IS DISTINCT FROM 'null'::jsonb
           OR jsonb_array_length(v_tables) NOT BETWEEN 1 AND 1024
           OR p_metadata->'coordinate_system' IS DISTINCT FROM
             '{"unit":"cell","row_start_index":1,"column_start_index":1,"range_end":"inclusive","csv_rows":"logical_record_including_header","xlsx_rows":"original_worksheet_row"}'::jsonb
        THEN RETURN false; END IF;
        FOR v_table IN SELECT value FROM jsonb_array_elements(v_tables) LOOP
            IF NOT a4b.unified_parser_keys_valid(v_table,
                 ARRAY['table_id','sheet_name','row_count','column_count'])
               OR NOT a4b.unified_parser_text_valid(v_table->'table_id',100)
               OR NOT a4b.unified_parser_text_valid(v_table->'sheet_name',100)
               OR NOT a4b.parser_json_integer_valid(v_table->'row_count',0,2147483647)
               OR NOT a4b.parser_json_integer_valid(v_table->'column_count',0,2147483647)
            THEN RETURN false; END IF;
            v_rows := (v_table->>'row_count')::numeric::bigint;
            v_cols := (v_table->>'column_count')::numeric::bigint;
            IF (v_rows = 0) <> (v_cols = 0) THEN RETURN false; END IF;
            IF v_mime = 'text/csv' THEN
                IF jsonb_array_length(v_tables) <> 1 OR v_table->>'table_id' <> 'csv'
                   OR v_table->>'sheet_name' <> 'CSV' THEN RETURN false; END IF;
            ELSE
                IF (v_source->>'size_bytes')::numeric <= 0
                   OR length(v_table->>'sheet_name') > 31 OR v_rows > 1048576 OR v_cols > 16384
                THEN RETURN false; END IF;
            END IF;
        END LOOP;
        IF (SELECT count(*) <> count(DISTINCT value->>'table_id')
                OR count(*) <> count(DISTINCT value->>'sheet_name')
            FROM jsonb_array_elements(v_tables)) THEN RETURN false; END IF;
        v_total := jsonb_array_length(v_tables);
    ELSE RETURN false;
    END IF;
    IF NOT a4b.unified_parser_keys_valid(v_processing, ARRAY[
        'outcome','scope','requested_units','completed_units','partial_units','failed_units',
        'whole_document_complete','parser','config_json','provenance','warnings','error','format_policy'])
       OR jsonb_typeof(v_processing->'config_json') IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_processing_config) IS DISTINCT FROM 'object'
       OR v_processing->'config_json' IS DISTINCT FROM p_processing_config
       OR jsonb_typeof(v_processing->'whole_document_complete') IS DISTINCT FROM 'boolean'
       OR coalesce(v_processing->>'scope' NOT IN ('full_document','selected_units'),true)
       OR jsonb_typeof(v_processing->'warnings') IS DISTINCT FROM 'array'
       OR jsonb_array_length(v_processing->'warnings') > 1024
    THEN RETURN false; END IF;
    v_outcome := v_processing->>'outcome';
    IF coalesce(v_outcome NOT IN ('succeeded','partial','failed'),true) THEN RETURN false; END IF;
    v_requested := v_processing->'requested_units'; v_completed := v_processing->'completed_units';
    v_partial := v_processing->'partial_units'; v_failed := v_processing->'failed_units';
    FOREACH v_unit IN ARRAY ARRAY[v_requested,v_completed,v_partial,v_failed] LOOP
        IF jsonb_typeof(v_unit) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
        IF jsonb_array_length(v_unit) > 65536
           OR (SELECT count(*) <> count(DISTINCT value) FROM jsonb_array_elements(v_unit))
        THEN RETURN false; END IF;
        FOR v_item IN SELECT value FROM jsonb_array_elements(v_unit) LOOP
            IF v_profile = 'page' THEN
                IF NOT a4b.parser_json_integer_valid(v_item,1,v_total) THEN RETURN false; END IF;
            ELSE
                IF NOT a4b.unified_parser_text_valid(v_item,100)
                   OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_tables) t(value)
                                  WHERE t.value->>'table_id' = v_item#>>'{}')
                THEN RETURN false; END IF;
            END IF;
        END LOOP;
    END LOOP;
    v_req := jsonb_array_length(v_requested); v_done := jsonb_array_length(v_completed);
    v_part := jsonb_array_length(v_partial); v_fail := jsonb_array_length(v_failed);
    IF v_req < 1 OR v_req <> v_done + v_part + v_fail
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_completed||v_partial||v_failed) u(value)
                  WHERE NOT v_requested @> jsonb_build_array(u.value))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_completed) u(value)
                  WHERE v_partial @> jsonb_build_array(u.value) OR v_failed @> jsonb_build_array(u.value))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_partial) u(value)
                  WHERE v_failed @> jsonb_build_array(u.value))
       OR (v_processing->>'scope' = 'full_document' AND v_req <> v_total)
    THEN RETURN false; END IF;
    -- Bounds/uniqueness plus count prove exact whole-source coverage without generating
    -- huge source ranges. A partial unit can preserve useful output but cannot be ready.
    v_whole := v_processing->>'scope' = 'full_document' AND v_done = v_total
                  AND v_part = 0 AND v_fail = 0;
    IF v_processing->'whole_document_complete' IS DISTINCT FROM to_jsonb(v_whole)
    THEN RETURN false; END IF;
    IF v_outcome = 'succeeded' THEN
        IF v_part <> 0 OR v_fail <> 0 OR v_processing->'error' IS DISTINCT FROM 'null'::jsonb
        THEN RETURN false; END IF;
    ELSIF v_outcome = 'partial' THEN
        IF NOT (v_part > 0 OR (v_done > 0 AND v_fail > 0)) THEN RETURN false; END IF;
        IF v_processing->'error' IS DISTINCT FROM 'null'::jsonb
           AND NOT a4b.parser_error_valid(v_processing->'error') THEN RETURN false; END IF;
    ELSE
        IF v_done <> 0 OR v_part <> 0 OR v_fail <> v_req
           OR NOT a4b.parser_error_valid(v_processing->'error') THEN RETURN false; END IF;
    END IF;
    v_parser := v_processing->'parser'; v_provenance := v_processing->'provenance';
    IF NOT a4b.unified_parser_keys_valid(v_parser,
          ARRAY['engine','package_version','postprocessor_revision'])
       OR NOT a4b.unified_parser_text_valid(v_parser->'engine',200)
       OR NOT a4b.unified_parser_text_valid(v_parser->'package_version',200)
       OR jsonb_typeof(v_parser->'postprocessor_revision') IS DISTINCT FROM 'string'
       OR coalesce((v_parser->>'postprocessor_revision') !~ '^[0-9a-f]{64}$',true)
       OR NOT a4b.unified_parser_keys_valid(v_provenance,
          ARRAY['derivation','service_integration','source_artifacts'])
       OR coalesce(v_provenance->>'derivation' NOT IN
          ('native_runtime','adapter_replay_from_verified_artifacts','illustrative_example'),true)
       OR jsonb_typeof(v_provenance->'service_integration') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(v_provenance->'source_artifacts') IS DISTINCT FROM 'array'
       OR jsonb_array_length(v_provenance->'source_artifacts') > 1024
    THEN RETURN false; END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_provenance->'source_artifacts') LOOP
        IF NOT a4b.unified_parser_keys_valid(v_item,ARRAY['name','sha256'])
           OR NOT a4b.unified_parser_text_valid(v_item->'name',1024)
           OR jsonb_typeof(v_item->'sha256') IS DISTINCT FROM 'string'
           OR coalesce((v_item->>'sha256') !~ '^[0-9a-f]{64}$',true)
        THEN RETURN false; END IF;
    END LOOP;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_processing->'warnings') LOOP
        IF NOT a4b.unified_parser_keys_valid(v_item,ARRAY['code','severity','message'])
           OR NOT a4b.unified_parser_text_valid(v_item->'code',200)
           OR NOT a4b.unified_parser_text_valid(v_item->'message',10000)
           OR coalesce(v_item->>'severity' NOT IN ('info','warning','error'),true)
        THEN RETURN false; END IF;
    END LOOP;
    v_policy := v_processing->'format_policy';
    IF v_profile = 'page' THEN
        IF v_policy IS DISTINCT FROM
          '{"policy_version":"a4b.page.v1","source_values":"preserve_text","canonical_text":"parser_emitted_immutable"}'::jsonb
        THEN RETURN false; END IF;
    ELSIF v_mime = 'text/csv' THEN
        IF NOT a4b.unified_parser_keys_valid(v_policy,
          ARRAY['policy_version','encoding','delimiter','quote','header_row','blank_records','value_policy','canonical_text'])
           OR v_policy->>'policy_version' IS DISTINCT FROM 'a4b.csv.v1'
           OR NOT a4b.unified_parser_text_valid(v_policy->'encoding',100)
           OR jsonb_typeof(v_policy->'delimiter') IS DISTINCT FROM 'string'
           OR char_length(v_policy->>'delimiter') <> 1
           OR jsonb_typeof(v_policy->'quote') IS DISTINCT FROM 'string'
           OR char_length(v_policy->>'quote') <> 1
           OR v_policy->>'delimiter' = v_policy->>'quote'
           OR v_policy->>'delimiter' IN (E'\r',E'\n') OR v_policy->>'quote' IN (E'\r',E'\n')
           OR (v_policy->'header_row' IS DISTINCT FROM 'null'::jsonb
               AND NOT a4b.parser_json_integer_valid(v_policy->'header_row',1,
                   (v_tables->0->>'row_count')::numeric::bigint))
           OR v_policy->>'blank_records' IS DISTINCT FROM 'preserve'
           OR v_policy->>'value_policy' IS DISTINCT FROM 'raw_text'
           OR v_policy->>'canonical_text' IS DISTINCT FROM 'parser_emitted_immutable'
        THEN RETURN false; END IF;
        IF (v_source->>'size_bytes')::numeric = 0
           AND ((v_tables->0->>'row_count')::numeric <> 0
                OR (v_tables->0->>'column_count')::numeric <> 0
                OR v_policy->'header_row' IS DISTINCT FROM 'null'::jsonb)
        THEN RETURN false; END IF;
    ELSE
        IF v_policy IS DISTINCT FROM
          '{"policy_version":"a4b.xlsx.v1","cell_values":"raw_text","formulas":"preserve_formula_and_cached_value_no_evaluation","dates":"preserve_serial_and_number_format","merged_cells":"anchor_once_with_span","canonical_text":"parser_emitted_immutable"}'::jsonb
        THEN RETURN false; END IF;
    END IF;
    RETURN true;
EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation OR invalid_parameter_value THEN RETURN false;
END $_$;


ALTER FUNCTION a4b.unified_parser_metadata_valid(p_metadata jsonb, p_content_hash text, p_mime_type text, p_size_bytes bigint, p_processing_config jsonb) OWNER TO postgres;

--
-- Name: unified_parser_text_valid(jsonb, integer); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.unified_parser_text_valid(p_value jsonb, p_max integer) RETURNS boolean
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$ SELECT coalesce(jsonb_typeof(p_value) = 'string' AND p_max > 0
          AND (p_value #>> '{}') ~ '[^[:space:]]'
          AND length(p_value #>> '{}') <= p_max, false) $$;


ALTER FUNCTION a4b.unified_parser_text_valid(p_value jsonb, p_max integer) OWNER TO postgres;

--
-- Name: valid_embedding_profile(jsonb); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.valid_embedding_profile(p_profile jsonb) RETURNS boolean
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
    SELECT coalesce(
        jsonb_typeof(p_profile) = 'object'
        AND jsonb_typeof(p_profile -> 'provider') = 'string'
        AND length(btrim(p_profile ->> 'provider')) > 0
        AND jsonb_typeof(p_profile -> 'model') = 'string'
        AND length(btrim(p_profile ->> 'model')) > 0
        AND jsonb_typeof(p_profile -> 'revision') = 'string'
        AND length(btrim(p_profile ->> 'revision')) > 0
        AND p_profile -> 'dimensions' = '1024'::jsonb,
        false
    );
$$;


ALTER FUNCTION a4b.valid_embedding_profile(p_profile jsonb) OWNER TO postgres;

--
-- Name: verification_document_valid(jsonb); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.verification_document_valid(p_document jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v_item jsonb; v_child jsonb; v_ids text[] := ARRAY[]::text[];
BEGIN
    IF NOT a4b.verification_object_shape(p_document,
        ARRAY['id','version','parse_status'], ARRAY['id','version','parse_status','chunks','tables'])
        OR NOT a4b.verification_string_valid(p_document->'id',1,100)
        OR NOT a4b.verification_string_valid(p_document->'version',1,100)
        OR NOT COALESCE(p_document->>'parse_status' IN ('ready','partial','failed'),false)
        THEN RETURN false; END IF;
    IF p_document ? 'chunks' THEN
        IF jsonb_typeof(p_document->'chunks') <> 'array' THEN RETURN false; END IF;
        IF jsonb_array_length(p_document->'chunks') > 50 THEN RETURN false; END IF;
        FOR v_item IN SELECT value FROM jsonb_array_elements(p_document->'chunks') LOOP
            IF NOT a4b.verification_object_shape(v_item,ARRAY['id','text','locator'],ARRAY['id','text','locator'])
                OR NOT a4b.verification_string_valid(v_item->'id',1,100)
                OR NOT a4b.verification_string_valid(v_item->'text',1,30000)
                OR NOT a4b.verification_string_valid(v_item->'locator',1,200)
                OR (v_item->>'id')=ANY(v_ids) THEN RETURN false; END IF;
            v_ids := array_append(v_ids,v_item->>'id');
        END LOOP;
    END IF;
    v_ids := ARRAY[]::text[];
    IF p_document ? 'tables' THEN
        IF jsonb_typeof(p_document->'tables') <> 'array' THEN RETURN false; END IF;
        IF jsonb_array_length(p_document->'tables') > 10 THEN RETURN false; END IF;
        FOR v_item IN SELECT value FROM jsonb_array_elements(p_document->'tables') LOOP
            IF NOT a4b.verification_object_shape(v_item,ARRAY['id','columns'],ARRAY['id','columns','sample_rows'])
                OR NOT a4b.verification_string_valid(v_item->'id',1,100)
                OR (v_item->>'id')=ANY(v_ids)
                OR jsonb_typeof(v_item->'columns') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
            v_ids := array_append(v_ids,v_item->>'id');
            IF jsonb_array_length(v_item->'columns') NOT BETWEEN 1 AND 200 THEN RETURN false; END IF;
            FOR v_child IN SELECT value FROM jsonb_array_elements(v_item->'columns') LOOP
                IF jsonb_typeof(v_child) <> 'string' THEN RETURN false; END IF;
            END LOOP;
            IF v_item ? 'sample_rows' THEN
                IF jsonb_typeof(v_item->'sample_rows') <> 'array' THEN RETURN false; END IF;
                IF jsonb_array_length(v_item->'sample_rows') > 20 THEN RETURN false; END IF;
                FOR v_child IN SELECT value FROM jsonb_array_elements(v_item->'sample_rows') LOOP
                    IF jsonb_typeof(v_child) <> 'object' THEN RETURN false; END IF;
                END LOOP;
            END IF;
        END LOOP;
    END IF;
    -- Semantic rule explicitly stated in docs/CONTRACT.md:19, beyond JSON Schema.
    IF p_document->>'parse_status'='failed'
        AND (COALESCE(jsonb_array_length(p_document->'chunks'),0)<>0
             OR COALESCE(jsonb_array_length(p_document->'tables'),0)<>0) THEN RETURN false; END IF;
    RETURN true;
END $$;


ALTER FUNCTION a4b.verification_document_valid(p_document jsonb) OWNER TO postgres;

--
-- Name: verification_draft_valid(jsonb); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.verification_draft_valid(p_draft jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog'
    AS $$ DECLARE v_item jsonb; v_workflow jsonb;
BEGIN
    IF p_draft IS NULL OR p_draft='null'::jsonb THEN RETURN true; END IF;
    IF NOT a4b.verification_object_shape(p_draft,ARRAY['id','version','workflow'],
        ARRAY['id','version','document_ids','workflow'])
        OR NOT a4b.verification_string_valid(p_draft->'id',0,NULL)
        OR NOT a4b.verification_string_valid(p_draft->'version',0,NULL) THEN RETURN false; END IF;
    IF p_draft ? 'document_ids' THEN
        IF jsonb_typeof(p_draft->'document_ids') <> 'array' THEN RETURN false; END IF;
        FOR v_item IN SELECT value FROM jsonb_array_elements(p_draft->'document_ids') LOOP
            IF jsonb_typeof(v_item) <> 'string' THEN RETURN false; END IF;
        END LOOP;
    END IF;
    v_workflow := p_draft->'workflow';
    IF NOT a4b.verification_object_shape(v_workflow,ARRAY['entry','nodes'],ARRAY['entry','nodes','edges'])
        OR jsonb_typeof(v_workflow->'entry') IS DISTINCT FROM 'string'
        OR jsonb_typeof(v_workflow->'nodes') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
    IF jsonb_array_length(v_workflow->'nodes') NOT BETWEEN 1 AND 15 THEN RETURN false; END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_workflow->'nodes') LOOP
        IF jsonb_typeof(v_item) <> 'object' THEN RETURN false; END IF;
    END LOOP;
    IF v_workflow ? 'edges' THEN
        IF jsonb_typeof(v_workflow->'edges') <> 'array' THEN RETURN false; END IF;
        IF jsonb_array_length(v_workflow->'edges') > 25 THEN RETURN false; END IF;
        FOR v_item IN SELECT value FROM jsonb_array_elements(v_workflow->'edges') LOOP
            IF jsonb_typeof(v_item) <> 'object' THEN RETURN false; END IF;
        END LOOP;
    END IF;
    RETURN true;
END $$;


ALTER FUNCTION a4b.verification_draft_valid(p_draft jsonb) OWNER TO postgres;

--
-- Name: verification_history_guard(); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.verification_history_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE
    v_context jsonb := NEW.context_json; v_map jsonb; v_document jsonb;
    v_document_id uuid; v_version_id uuid; v_version_no integer;
    v_stored a4b.document_versions%ROWTYPE; v_ids text[] := ARRAY[]::text[];
    v_session a4b.design_sessions%ROWTYPE; v_parent a4b.verification_checks%ROWTYPE;
    v_key text;
BEGIN
    -- The database observation time is not a caller-supplied historical claim.
    NEW.recorded_at := clock_timestamp();
    -- BEFORE triggers run before CHECK constraints. Reject malformed input here
    -- before array traversal can produce an unrelated JSON type exception.
    IF NOT a4b.verification_request_valid(NEW.request_json) THEN
        RAISE EXCEPTION 'verification input fails the database contract boundary' USING ERRCODE='23514';
    END IF;
    IF NOT a4b.verification_object_shape(v_context,
        ARRAY['document_mappings','previous_draft_original','candidate_draft_original','input_scope_note'],
        ARRAY['document_mappings','previous_draft_original','candidate_draft_original','input_scope_note'])
        OR jsonb_typeof(v_context->'document_mappings') IS DISTINCT FROM 'array'
        OR NOT a4b.verification_string_valid(v_context->'input_scope_note',1,2000) THEN
        RAISE EXCEPTION 'verification context has an invalid local envelope' USING ERRCODE='23514';
    END IF;
    FOREACH v_key IN ARRAY ARRAY['previous_draft','candidate_draft'] LOOP
        IF COALESCE(NEW.request_json->v_key,'null'::jsonb)='null'::jsonb THEN
            IF v_context->(v_key||'_original')<>'null'::jsonb THEN
                RAISE EXCEPTION 'original draft snapshot must be null when no projected draft was supplied' USING ERRCODE='23514';
            END IF;
        ELSIF jsonb_typeof(v_context->(v_key||'_original')) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'full original draft snapshot is required in context' USING ERRCODE='23514';
        END IF;
    END LOOP;
    IF NEW.design_session_id IS NOT NULL THEN
        SELECT * INTO v_session FROM a4b.design_sessions WHERE id=NEW.design_session_id;
        IF NOT FOUND OR v_session.created_by<>NEW.created_by
            OR NEW.design_session_revision>v_session.revision THEN
            RAISE EXCEPTION 'session creator/revision does not match recorded context' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.retry_of_check_id IS NOT NULL THEN
        SELECT * INTO v_parent FROM a4b.verification_checks WHERE id=NEW.retry_of_check_id;
        IF NOT FOUND OR v_parent.created_by<>NEW.created_by
            OR v_parent.request_namespace<>NEW.request_namespace
            OR v_parent.design_session_id IS DISTINCT FROM NEW.design_session_id
            OR v_parent.mode<>NEW.mode OR v_parent.completed_at>NEW.started_at THEN
            RAISE EXCEPTION 'retry parent must be an earlier check in the same caller/user/session/mode' USING ERRCODE='23514';
        END IF;
    END IF;
    IF jsonb_array_length(v_context->'document_mappings')>10
        OR jsonb_array_length(v_context->'document_mappings')
            <>COALESCE(jsonb_array_length(NEW.request_json->'documents'),0) THEN
        RAISE EXCEPTION 'every supplied document requires one version mapping' USING ERRCODE='23514';
    END IF;
    FOR v_map IN SELECT value FROM jsonb_array_elements(v_context->'document_mappings') LOOP
        IF NOT a4b.verification_object_shape(v_map,ARRAY['input_id','input_version','storage_kind','coverage','coverage_detail'],
            ARRAY['input_id','input_version','storage_kind','coverage','coverage_detail',
                  'document_id','document_version_id','version_no','content_hash'])
            OR NOT a4b.verification_string_valid(v_map->'input_id',1,100)
            OR NOT a4b.verification_string_valid(v_map->'input_version',1,100)
            OR NOT a4b.verification_string_valid(v_map->'coverage_detail',1,2000)
            OR NOT COALESCE(v_map->>'coverage' IN ('full_document','subset','none'),false)
            OR (v_map->>'input_id')=ANY(v_ids) THEN
            RAISE EXCEPTION 'invalid or duplicate document version mapping' USING ERRCODE='23514';
        END IF;
        v_ids := array_append(v_ids,v_map->>'input_id');
        SELECT value INTO v_document FROM jsonb_array_elements(COALESCE(NEW.request_json->'documents','[]'::jsonb))
            WHERE value->>'id'=v_map->>'input_id' AND value->>'version'=v_map->>'input_version';
        IF NOT FOUND THEN RAISE EXCEPTION 'mapping does not match input document ID/version' USING ERRCODE='23514'; END IF;
        IF v_document->>'parse_status'='failed' AND v_map->>'coverage'<>'none' THEN
            RAISE EXCEPTION 'failed document has no reliable coverage' USING ERRCODE='23514';
        END IF;
        IF v_document->>'parse_status'='partial' AND v_map->>'coverage'='full_document' THEN
            RAISE EXCEPTION 'partial input must not claim whole-document coverage' USING ERRCODE='23514';
        END IF;
        IF v_map->>'storage_kind'='inline_fixture' THEN
            IF NEW.mode<>'fixture' OR v_map ?| ARRAY['document_id','document_version_id','version_no','content_hash'] THEN
                RAISE EXCEPTION 'inline fixture mappings are fixture-only and cannot claim database references' USING ERRCODE='23514';
            END IF;
        ELSIF v_map->>'storage_kind'='database' THEN
            IF NOT v_map ?& ARRAY['document_id','document_version_id','version_no','content_hash']
                OR jsonb_typeof(v_map->'document_id') IS DISTINCT FROM 'string'
                OR jsonb_typeof(v_map->'document_version_id') IS DISTINCT FROM 'string'
                OR jsonb_typeof(v_map->'version_no') IS DISTINCT FROM 'number'
                OR jsonb_typeof(v_map->'content_hash') IS DISTINCT FROM 'string'
                OR v_map->>'content_hash' !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'database mapping requires exact source identity and hash' USING ERRCODE='23514';
            END IF;
            BEGIN
                v_document_id := (v_map->>'document_id')::uuid;
                v_version_id := (v_map->>'document_version_id')::uuid;
                IF (v_map->>'version_no')::numeric<>trunc((v_map->>'version_no')::numeric) THEN
                    RAISE EXCEPTION 'noninteger version';
                END IF;
                v_version_no := ((v_map->>'version_no')::numeric)::integer;
            EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR raise_exception THEN
                RAISE EXCEPTION 'database document version identifiers are invalid' USING ERRCODE='23514';
            END;
            SELECT * INTO v_stored FROM a4b.document_versions WHERE id=v_version_id;
            IF NOT FOUND OR v_stored.document_id<>v_document_id OR v_stored.version_no<>v_version_no
                OR v_stored.content_hash<>v_map->>'content_hash'
                OR v_map->>'input_version'<>v_stored.version_no::text THEN
                RAISE EXCEPTION 'database document source/version/hash mismatch' USING ERRCODE='23514';
            END IF;
            -- Do not demand parser v0.1 fields: legacy and new unified formats
            -- may both supply evidence. Failed rows cannot masquerade as ready.
            IF (v_stored.status='failed' AND v_document->>'parse_status'<>'failed')
                OR (v_document->>'parse_status'='ready' AND v_stored.status<>'ready') THEN
                RAISE EXCEPTION 'input readiness is incompatible with stored version' USING ERRCODE='23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'unknown mapping storage_kind' USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END $_$;


ALTER FUNCTION a4b.verification_history_guard() OWNER TO postgres;

--
-- Name: verification_object_shape(jsonb, text[], text[]); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.verification_object_shape(p_value jsonb, p_required text[], p_allowed text[]) RETURNS boolean
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'pg_catalog'
    AS $$ SELECT COALESCE(jsonb_typeof(p_value) = 'object'
    AND p_value ?& p_required
    AND NOT EXISTS (SELECT FROM jsonb_object_keys(
        CASE WHEN jsonb_typeof(p_value)='object' THEN p_value ELSE '{}'::jsonb END) k
        WHERE NOT (k=ANY(p_allowed))), false) $$;


ALTER FUNCTION a4b.verification_object_shape(p_value jsonb, p_required text[], p_allowed text[]) OWNER TO postgres;

--
-- Name: verification_request_valid(jsonb); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.verification_request_valid(p_request jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog'
    AS $$ DECLARE v_item jsonb; v_ids text[] := ARRAY[]::text[];
BEGIN
    IF NOT a4b.verification_object_shape(p_request,ARRAY['contract_version','request_id','user_request','tools'],
        ARRAY['contract_version','request_id','user_request','tools','messages',
              'latest_user_answer_id','documents','previous_draft','candidate_draft'])
        OR p_request->>'contract_version' IS DISTINCT FROM 'a4b.verifier.v1'
        OR NOT a4b.verification_string_valid(p_request->'request_id',1,100)
        OR NOT a4b.verification_string_valid(p_request->'user_request',1,20000)
        OR jsonb_typeof(p_request->'tools') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
    IF jsonb_array_length(p_request->'tools') NOT BETWEEN 1 AND 100 THEN RETURN false; END IF;
    -- Tool content/port semantic validation belongs to the actual input schema.
    FOR v_item IN SELECT value FROM jsonb_array_elements(p_request->'tools') LOOP
        IF jsonb_typeof(v_item) <> 'object' THEN RETURN false; END IF;
    END LOOP;
    IF p_request ? 'messages' THEN
        IF jsonb_typeof(p_request->'messages') <> 'array' THEN RETURN false; END IF;
        IF jsonb_array_length(p_request->'messages') > 50 THEN RETURN false; END IF;
        FOR v_item IN SELECT value FROM jsonb_array_elements(p_request->'messages') LOOP
            IF NOT a4b.verification_object_shape(v_item,ARRAY['id','role','content'],ARRAY['id','role','content'])
                OR NOT a4b.verification_string_valid(v_item->'id',1,100)
                OR NOT a4b.verification_string_valid(v_item->'content',1,20000)
                OR NOT COALESCE(v_item->>'role' IN ('user','assistant'),false)
                OR (v_item->>'id')=ANY(v_ids) THEN RETURN false; END IF;
            v_ids := array_append(v_ids,v_item->>'id');
        END LOOP;
    END IF;
    IF p_request ? 'latest_user_answer_id' AND p_request->'latest_user_answer_id'<>'null'::jsonb THEN
        IF jsonb_typeof(p_request->'latest_user_answer_id') <> 'string' THEN RETURN false; END IF;
        IF NOT EXISTS (SELECT FROM jsonb_array_elements(COALESCE(p_request->'messages','[]'::jsonb)) m
            WHERE m->>'id'=p_request->>'latest_user_answer_id' AND m->>'role'='user') THEN RETURN false; END IF;
    END IF;
    v_ids := ARRAY[]::text[];
    IF p_request ? 'documents' THEN
        IF jsonb_typeof(p_request->'documents') <> 'array' THEN RETURN false; END IF;
        IF jsonb_array_length(p_request->'documents') > 10 THEN RETURN false; END IF;
        FOR v_item IN SELECT value FROM jsonb_array_elements(p_request->'documents') LOOP
            IF NOT a4b.verification_document_valid(v_item)
                OR (v_item->>'id')=ANY(v_ids) THEN RETURN false; END IF;
            v_ids := array_append(v_ids,v_item->>'id');
        END LOOP;
    END IF;
    RETURN a4b.verification_draft_valid(p_request->'previous_draft')
       AND a4b.verification_draft_valid(p_request->'candidate_draft');
END $$;


ALTER FUNCTION a4b.verification_request_valid(p_request jsonb) OWNER TO postgres;

--
-- Name: verification_result_valid(jsonb, text, text); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.verification_result_valid(p_result jsonb, p_request_id text, p_mode text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog'
    AS $$ DECLARE v_output jsonb; v_item jsonb; v_key text; v_number numeric;
BEGIN
    IF NOT a4b.verification_object_shape(p_result,
        ARRAY['request_id','model_output','findings','route','mode','model','latency_ms'],
        ARRAY['contract_version','request_id','model_output','findings','route',
              'execution_allowed','mode','model','latency_ms','error_code'])
        OR jsonb_typeof(p_result->'request_id') IS DISTINCT FROM 'string'
        OR p_result->>'request_id' IS DISTINCT FROM p_request_id
        OR COALESCE(p_result->>'contract_version','a4b.verifier.v1') <> 'a4b.verifier.v1'
        OR (p_result ? 'contract_version' AND jsonb_typeof(p_result->'contract_version') <> 'string')
        OR COALESCE(p_result->'execution_allowed','false'::jsonb) <> 'false'::jsonb
        OR NOT COALESCE(p_result->>'mode' IN ('fixture','live'),false)
        OR p_result->>'mode' IS DISTINCT FROM p_mode
        OR NOT COALESCE(p_result->>'route' IN
            ('ask_user','repair_plan','unsupported','manual_review','continue_review'),false)
        OR jsonb_typeof(p_result->'findings') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_result->'latency_ms') IS DISTINCT FROM 'number'
        OR jsonb_typeof(p_result->'model') NOT IN ('string','null') THEN RETURN false; END IF;
    v_number := (p_result->>'latency_ms')::numeric;
    IF v_number < 0 OR v_number::text IN ('NaN','Infinity','-Infinity') THEN RETURN false; END IF;
    IF p_result ? 'error_code' AND jsonb_typeof(p_result->'error_code') NOT IN ('string','null') THEN RETURN false; END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(p_result->'findings') LOOP
        IF NOT a4b.verification_object_shape(v_item,ARRAY['code','severity','detail'],ARRAY['code','severity','detail','path'])
            OR jsonb_typeof(v_item->'code') IS DISTINCT FROM 'string'
            OR jsonb_typeof(v_item->'detail') IS DISTINCT FROM 'string'
            OR NOT COALESCE(v_item->>'severity' IN ('block','review'),false)
            OR (v_item ? 'path' AND jsonb_typeof(v_item->'path') NOT IN ('string','null')) THEN RETURN false; END IF;
    END LOOP;
    v_output := p_result->'model_output';
    IF v_output='null'::jsonb THEN RETURN true; END IF;
    IF NOT a4b.verification_object_shape(v_output,
        ARRAY['request_status','evidence_status','missing_conditions','unsupported_actions',
              'draft_verdict','reflection_verdict','evidence','issues','summary'],
        ARRAY['request_status','evidence_status','missing_conditions','unsupported_actions',
              'draft_verdict','reflection_verdict','evidence','issues','summary'])
        OR NOT (v_output->'request_status'='null'::jsonb
             OR v_output->>'request_status' IN ('ready','clarify','unsupported'))
        OR NOT COALESCE(v_output->>'evidence_status' IN ('sufficient','insufficient'),false)
        OR NOT COALESCE(v_output->>'draft_verdict' IN ('pass','fail','undetermined','not_applicable'),false)
        OR NOT COALESCE(v_output->>'reflection_verdict' IN ('pass','fail','undetermined','not_applicable'),false)
        OR NOT a4b.verification_string_valid(v_output->'summary',1,500) THEN RETURN false; END IF;
    FOREACH v_key IN ARRAY ARRAY['missing_conditions','unsupported_actions','evidence','issues'] LOOP
        IF jsonb_typeof(v_output->v_key) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
    END LOOP;
    IF jsonb_array_length(v_output->'missing_conditions') > 8
        OR jsonb_array_length(v_output->'unsupported_actions') > 8
        OR jsonb_array_length(v_output->'evidence') > 8
        OR jsonb_array_length(v_output->'issues') > 10 THEN RETURN false; END IF;
    RETURN true;
EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation THEN RETURN false;
END $$;


ALTER FUNCTION a4b.verification_result_valid(p_result jsonb, p_request_id text, p_mode text) OWNER TO postgres;

--
-- Name: verification_string_valid(jsonb, integer, integer); Type: FUNCTION; Schema: a4b; Owner: postgres
--

CREATE FUNCTION a4b.verification_string_valid(p_value jsonb, p_min integer, p_max integer) RETURNS boolean
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'pg_catalog'
    AS $$ SELECT COALESCE(jsonb_typeof(p_value)='string'
    AND length(p_value #>> '{}') >= p_min
    AND (p_max IS NULL OR length(p_value #>> '{}') <= p_max), false) $$;


ALTER FUNCTION a4b.verification_string_valid(p_value jsonb, p_min integer, p_max integer) OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: approval_requests; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.approval_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id uuid NOT NULL,
    waiting_node_run_id uuid NOT NULL,
    target_output_id uuid NOT NULL,
    reviewer_id uuid NOT NULL,
    decider_id uuid,
    status text DEFAULT 'pending'::text NOT NULL,
    expected_state_revision bigint NOT NULL,
    comment text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_at timestamp with time zone,
    CONSTRAINT approval_requests_check CHECK (((decided_at IS NULL) OR (decided_at >= created_at))),
    CONSTRAINT approval_requests_decision_ck CHECK ((((status = ANY (ARRAY['approved'::text, 'rejected'::text])) AND (decider_id IS NOT NULL) AND (decider_id = reviewer_id) AND (decided_at IS NOT NULL)) OR ((status <> ALL (ARRAY['approved'::text, 'rejected'::text])) AND (decider_id IS NULL) AND (decided_at IS NULL)))),
    CONSTRAINT approval_requests_expected_state_revision_check CHECK ((expected_state_revision >= 0)),
    CONSTRAINT approval_requests_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text, 'superseded'::text, 'canceled'::text])))
);


ALTER TABLE a4b.approval_requests OWNER TO postgres;

--
-- Name: chunk_locations; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.chunk_locations (
    chunk_id uuid NOT NULL,
    source_location_id uuid NOT NULL,
    document_version_id uuid NOT NULL,
    location_order integer NOT NULL,
    CONSTRAINT chunk_locations_location_order_check CHECK ((location_order > 0))
);


ALTER TABLE a4b.chunk_locations OWNER TO postgres;

--
-- Name: TABLE chunk_locations; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON TABLE a4b.chunk_locations IS '한 검색 청크와 여러 원문 위치의 N:M 연결. 양쪽 문서 버전 일치 강제.';


--
-- Name: design_sessions; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.design_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_id uuid NOT NULL,
    created_by uuid NOT NULL,
    base_version_id uuid,
    messages_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    draft_graph_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    revision bigint DEFAULT 0 NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT design_sessions_draft_graph_json_check CHECK ((jsonb_typeof(draft_graph_json) = 'object'::text)),
    CONSTRAINT design_sessions_messages_json_check CHECK ((jsonb_typeof(messages_json) = 'array'::text)),
    CONSTRAINT design_sessions_revision_check CHECK ((revision >= 0)),
    CONSTRAINT design_sessions_status_check CHECK ((status = ANY (ARRAY['open'::text, 'closed'::text])))
);


ALTER TABLE a4b.design_sessions OWNER TO postgres;

--
-- Name: document_chunks; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.document_chunks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_version_id uuid NOT NULL,
    chunk_no integer NOT NULL,
    content text NOT NULL,
    content_hash text NOT NULL,
    embedding extensions.vector(1024),
    embedding_status text DEFAULT 'pending'::text NOT NULL,
    source_segments_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT document_chunks_chunk_no_check CHECK ((chunk_no > 0)),
    CONSTRAINT document_chunks_content_check CHECK ((length(btrim(content)) > 0)),
    CONSTRAINT document_chunks_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT document_chunks_embedding_state_ck CHECK ((((embedding_status = 'ready'::text) AND (embedding IS NOT NULL)) OR ((embedding_status <> 'ready'::text) AND (embedding IS NULL)))),
    CONSTRAINT document_chunks_embedding_status_check CHECK ((embedding_status = ANY (ARRAY['not_required'::text, 'pending'::text, 'ready'::text, 'failed'::text]))),
    CONSTRAINT document_chunks_nonzero_vector_ck CHECK (((embedding IS NULL) OR (extensions.vector_norm(embedding) > (0)::double precision))),
    CONSTRAINT document_chunks_source_segments_json_ck CHECK ((jsonb_typeof(source_segments_json) = 'array'::text))
);


ALTER TABLE a4b.document_chunks OWNER TO postgres;

--
-- Name: COLUMN document_chunks.embedding; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.document_chunks.embedding IS '1024차원 예시. 모델 확정 전 차원 검토. 임베딩 생성은 worker가 수행.';


--
-- Name: COLUMN document_chunks.source_segments_json; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.document_chunks.source_segments_json IS 'Ordered segments with content_start/content_end, source_char_start/source_char_end and location_no. Half-open Unicode code-point offsets. Separators may be outside evidence segments.';


--
-- Name: document_types; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.document_types (
    code text NOT NULL,
    label text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT document_types_code_check CHECK ((code ~ '^[a-z][a-z0-9_]{1,63}$'::text)),
    CONSTRAINT document_types_label_check CHECK ((length(btrim(label)) > 0))
);


ALTER TABLE a4b.document_types OWNER TO postgres;

--
-- Name: document_versions; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.document_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_id uuid NOT NULL,
    document_type_code text NOT NULL,
    version_no integer NOT NULL,
    s3_bucket text NOT NULL,
    s3_key text NOT NULL,
    s3_version_id text,
    content_hash text NOT NULL,
    mime_type text NOT NULL,
    size_bytes bigint NOT NULL,
    parsed_s3_key text,
    processing_config_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'uploaded'::text NOT NULL,
    error_json jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    parser_contract_version text,
    parsed_s3_version_id text,
    parsed_json_sha256 text,
    canonical_text_sha256 text,
    canonical_text_length bigint,
    parser_metadata_json jsonb,
    CONSTRAINT document_versions_canonical_text_length_ck CHECK (((canonical_text_length IS NULL) OR (canonical_text_length >= 0))),
    CONSTRAINT document_versions_canonical_text_sha256_ck CHECK (((canonical_text_sha256 IS NULL) OR (canonical_text_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT document_versions_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT document_versions_embedding_config_ck CHECK (((NOT (processing_config_json ? 'embedding'::text)) OR a4b.valid_embedding_profile((processing_config_json -> 'embedding'::text)))),
    CONSTRAINT document_versions_mime_type_check CHECK ((length(btrim(mime_type)) > 0)),
    CONSTRAINT document_versions_parsed_json_sha256_ck CHECK (((parsed_json_sha256 IS NULL) OR (parsed_json_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT document_versions_parsed_s3_key_check CHECK (((parsed_s3_key IS NULL) OR (length(parsed_s3_key) > 0))),
    CONSTRAINT document_versions_parsed_s3_version_id_ck CHECK (((parsed_s3_version_id IS NULL) OR (length(parsed_s3_version_id) > 0))),
    CONSTRAINT document_versions_parser_contract_version_ck CHECK (((parser_contract_version IS NULL) OR (parser_contract_version = ANY (ARRAY['a4b.parser_result.v0.1-draft'::text, 'a4b.parser_result.v0.2'::text])))),
    CONSTRAINT document_versions_parser_metadata_json_ck CHECK (((parser_metadata_json IS NULL) OR (jsonb_typeof(parser_metadata_json) = 'object'::text))),
    CONSTRAINT document_versions_processing_config_json_check CHECK ((jsonb_typeof(processing_config_json) = 'object'::text)),
    CONSTRAINT document_versions_s3_bucket_check CHECK ((length(btrim(s3_bucket)) > 0)),
    CONSTRAINT document_versions_s3_key_check CHECK ((length(s3_key) > 0)),
    CONSTRAINT document_versions_s3_version_id_check CHECK (((s3_version_id IS NULL) OR (length(s3_version_id) > 0))),
    CONSTRAINT document_versions_size_bytes_check CHECK ((size_bytes >= 0)),
    CONSTRAINT document_versions_status_check CHECK ((status = ANY (ARRAY['uploaded'::text, 'processing'::text, 'ready'::text, 'failed'::text]))),
    CONSTRAINT document_versions_version_no_check CHECK ((version_no > 0))
);


ALTER TABLE a4b.document_versions OWNER TO postgres;

--
-- Name: COLUMN document_versions.document_type_code; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.document_versions.document_type_code IS '처리본 생성 시 문서 종류 스냅샷. 문서 재분류가 과거 처리본을 바꾸지 않음.';


--
-- Name: COLUMN document_versions.parsed_s3_key; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.document_versions.parsed_s3_key IS '같은 버킷의 추출 JSON: canonical text·페이지·블록·표·병합 셀·OCR 상세. 실제 파일과 위치 대응은 parser가 검증.';


--
-- Name: COLUMN document_versions.processing_config_json; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.document_versions.processing_config_json IS '파서·청킹·embedding(provider,model,revision,dimensions) 설정. 생성 후 불변.';


--
-- Name: COLUMN document_versions.parser_contract_version; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.document_versions.parser_contract_version IS 'Immutable explicit opt-in. v0.1-draft remains page/docling-specific; v0.2 supports a common page/grid storage envelope. NULL is legacy compatibility, not v0.2 conformance.';


--
-- Name: COLUMN document_versions.parsed_s3_version_id; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.document_versions.parsed_s3_version_id IS 'Optional S3 VersionId of the exact parsed JSON object; bucket versioning is separately managed.';


--
-- Name: COLUMN document_versions.parsed_json_sha256; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.document_versions.parsed_json_sha256 IS 'Lowercase SHA256 of original uploaded parser-result JSON bytes, verified by adapter; not JSONB serialization.';


--
-- Name: COLUMN document_versions.canonical_text_sha256; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.document_versions.canonical_text_sha256 IS 'Lowercase SHA256 of exact canonical text UTF-8 bytes, verified by adapter without normalization.';


--
-- Name: COLUMN document_versions.canonical_text_length; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.document_versions.canonical_text_length IS 'Length of exact canonical text in Unicode code points, not bytes or UTF-16 code units.';


--
-- Name: COLUMN document_versions.parser_metadata_json; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.document_versions.parser_metadata_json IS 'Unmodified source, processing, coordinate_system and text_indexing objects from the declared parser storage contract. Source bytes and parser output must be independently verified by the adapter.';


--
-- Name: documents; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_by uuid NOT NULL,
    name text NOT NULL,
    document_type_code text DEFAULT 'other'::text NOT NULL,
    source_metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    archived_at timestamp with time zone,
    CONSTRAINT documents_name_check CHECK ((length(btrim(name)) > 0)),
    CONSTRAINT documents_source_metadata_json_check CHECK ((jsonb_typeof(source_metadata_json) = 'object'::text))
);


ALTER TABLE a4b.documents OWNER TO postgres;

--
-- Name: extraction_schema_versions; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.extraction_schema_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_type_code text NOT NULL,
    schema_name text NOT NULL,
    version_no integer NOT NULL,
    schema_key text NOT NULL,
    schema_json jsonb NOT NULL,
    validator_contract text DEFAULT 'a4b-json-schema-v1'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT extraction_schema_versions_check CHECK ((schema_key = ((((document_type_code || '.'::text) || schema_name) || '.v'::text) || (version_no)::text))),
    CONSTRAINT extraction_schema_versions_schema_json_check CHECK (a4b.supported_extraction_schema(schema_json)),
    CONSTRAINT extraction_schema_versions_schema_name_check CHECK ((schema_name ~ '^[a-z][a-z0-9_]{1,63}$'::text)),
    CONSTRAINT extraction_schema_versions_validator_contract_check CHECK ((validator_contract = 'a4b-json-schema-v1'::text)),
    CONSTRAINT extraction_schema_versions_version_no_check CHECK ((version_no > 0))
);


ALTER TABLE a4b.extraction_schema_versions OWNER TO postgres;

--
-- Name: TABLE extraction_schema_versions; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON TABLE a4b.extraction_schema_versions IS '불변 문서 종류별 JSON 결과 규격. a4b-json-schema-v1 부분집합만 DB 검증.';


--
-- Name: node_runs; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.node_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id uuid NOT NULL,
    node_key text NOT NULL,
    attempt_no integer DEFAULT 1 NOT NULL,
    status text DEFAULT 'running'::text NOT NULL,
    input_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    output_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    error_json jsonb,
    assignee_id uuid,
    responded_by uuid,
    responded_at timestamp with time zone,
    model_usage_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    CONSTRAINT node_runs_attempt_no_check CHECK ((attempt_no > 0)),
    CONSTRAINT node_runs_check CHECK (((finished_at IS NULL) OR (finished_at >= started_at))),
    CONSTRAINT node_runs_check1 CHECK (((responded_at IS NULL) OR (responded_at >= started_at))),
    CONSTRAINT node_runs_input_json_check CHECK ((jsonb_typeof(input_json) = 'object'::text)),
    CONSTRAINT node_runs_model_usage_json_check CHECK ((jsonb_typeof(model_usage_json) = 'object'::text)),
    CONSTRAINT node_runs_node_key_check CHECK ((length(btrim(node_key)) > 0)),
    CONSTRAINT node_runs_output_json_check CHECK ((jsonb_typeof(output_json) = 'object'::text)),
    CONSTRAINT node_runs_response_ck CHECK ((((responded_by IS NULL) AND (responded_at IS NULL)) OR ((responded_by IS NOT NULL) AND (responded_at IS NOT NULL) AND (assignee_id IS NOT NULL) AND (responded_by = assignee_id)))),
    CONSTRAINT node_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'waiting'::text, 'succeeded'::text, 'failed'::text, 'canceled'::text]))),
    CONSTRAINT node_runs_terminal_time_ck CHECK (((status = ANY (ARRAY['succeeded'::text, 'failed'::text, 'canceled'::text])) = (finished_at IS NOT NULL)))
);


ALTER TABLE a4b.node_runs OWNER TO postgres;

--
-- Name: COLUMN node_runs.node_key; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.node_runs.node_key IS '확정 graph_json의 노드 ID. 일반 FK가 아니며 백엔드에서 그래프 소속을 검사.';


--
-- Name: output_evidence; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.output_evidence (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    output_id uuid NOT NULL,
    run_document_id uuid NOT NULL,
    document_version_id uuid NOT NULL,
    chunk_id uuid,
    source_location_id uuid NOT NULL,
    claim_path text NOT NULL,
    quote text NOT NULL,
    retrieval_score double precision,
    CONSTRAINT output_evidence_claim_path_check CHECK (((claim_path = ''::text) OR (claim_path ~ '^(/([^~/]|~[01])*)+$'::text))),
    CONSTRAINT output_evidence_quote_check CHECK ((length(btrim(quote)) > 0)),
    CONSTRAINT output_evidence_retrieval_score_check CHECK (((retrieval_score IS NULL) OR ((retrieval_score >= '-1e+308'::double precision) AND (retrieval_score <= '1e+308'::double precision))))
);


ALTER TABLE a4b.output_evidence OWNER TO postgres;

--
-- Name: COLUMN output_evidence.claim_path; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.output_evidence.claim_path IS 'RFC6901 JSON Pointer. 빈 문자열은 결과 전체, /line_items/0/amount는 필드. 원문 quote 일치는 backend 검증.';


--
-- Name: COLUMN output_evidence.quote; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.output_evidence.quote IS '실제 원문과의 일치 검증은 백엔드가 수행. 검색 점수 자체는 사실 검증이 아님.';


--
-- Name: outputs; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.outputs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id uuid NOT NULL,
    producer_node_run_id uuid,
    previous_output_id uuid,
    output_key text NOT NULL,
    revision_no integer DEFAULT 1 NOT NULL,
    output_type text NOT NULL,
    schema_key text NOT NULL,
    extraction_schema_version_id uuid,
    source_run_document_id uuid,
    content_json jsonb,
    s3_bucket text,
    s3_key text,
    s3_version_id text,
    content_hash text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT outputs_check CHECK ((previous_output_id IS DISTINCT FROM id)),
    CONSTRAINT outputs_check1 CHECK (((revision_no = 1) = (previous_output_id IS NULL))),
    CONSTRAINT outputs_check2 CHECK (((s3_bucket IS NULL) = (s3_key IS NULL))),
    CONSTRAINT outputs_check3 CHECK (((s3_version_id IS NULL) OR (s3_key IS NOT NULL))),
    CONSTRAINT outputs_check4 CHECK ((((content_json IS NOT NULL) AND (content_json <> 'null'::jsonb)) OR (s3_key IS NOT NULL))),
    CONSTRAINT outputs_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT outputs_extraction_shape_ck CHECK ((((output_type = 'extraction'::text) AND (extraction_schema_version_id IS NOT NULL) AND (source_run_document_id IS NOT NULL) AND (content_json IS NOT NULL) AND (jsonb_typeof(content_json) = 'object'::text)) OR ((output_type <> 'extraction'::text) AND (extraction_schema_version_id IS NULL) AND (source_run_document_id IS NULL)))),
    CONSTRAINT outputs_output_key_check CHECK ((length(btrim(output_key)) > 0)),
    CONSTRAINT outputs_output_type_check CHECK ((output_type = ANY (ARRAY['reply'::text, 'analysis'::text, 'report'::text, 'summary'::text, 'extraction'::text, 'other'::text]))),
    CONSTRAINT outputs_revision_no_check CHECK ((revision_no > 0)),
    CONSTRAINT outputs_s3_bucket_check CHECK (((s3_bucket IS NULL) OR (length(btrim(s3_bucket)) > 0))),
    CONSTRAINT outputs_s3_key_check CHECK (((s3_key IS NULL) OR (length(s3_key) > 0))),
    CONSTRAINT outputs_s3_version_id_check CHECK (((s3_version_id IS NULL) OR (length(s3_version_id) > 0))),
    CONSTRAINT outputs_schema_key_check CHECK ((length(btrim(schema_key)) > 0))
);


ALTER TABLE a4b.outputs OWNER TO postgres;

--
-- Name: COLUMN outputs.content_json; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.outputs.content_json IS 'extraction이면 등록된 버전별 스키마로 검증한 구조화 결과. 결과 값을 다른 테이블에 중복 저장하지 않음.';


--
-- Name: run_documents; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.run_documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id uuid NOT NULL,
    document_version_id uuid NOT NULL,
    binding_key text NOT NULL,
    usage_type text NOT NULL,
    CONSTRAINT run_documents_binding_key_check CHECK ((length(btrim(binding_key)) > 0)),
    CONSTRAINT run_documents_usage_type_check CHECK ((usage_type = ANY (ARRAY['input'::text, 'reference'::text])))
);


ALTER TABLE a4b.run_documents OWNER TO postgres;

--
-- Name: source_locations; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.source_locations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_version_id uuid NOT NULL,
    location_no integer NOT NULL,
    location_type text NOT NULL,
    page_no integer,
    sheet_name text,
    row_start integer,
    row_end integer,
    col_start integer,
    col_end integer,
    block_key text,
    char_start bigint,
    char_end bigint,
    bbox_x0 double precision,
    bbox_y0 double precision,
    bbox_x1 double precision,
    bbox_y1 double precision,
    message_no integer,
    label text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT source_locations_bbox_ck CHECK ((((bbox_x0 IS NULL) AND (bbox_y0 IS NULL) AND (bbox_x1 IS NULL) AND (bbox_y1 IS NULL)) OR ((bbox_x0 IS NOT NULL) AND (bbox_y0 IS NOT NULL) AND (bbox_x1 IS NOT NULL) AND (bbox_y1 IS NOT NULL) AND (bbox_x0 >= (0)::double precision) AND (bbox_y0 >= (0)::double precision) AND (bbox_x1 <= (1)::double precision) AND (bbox_y1 <= (1)::double precision) AND (bbox_x0 < bbox_x1) AND (bbox_y0 < bbox_y1)))),
    CONSTRAINT source_locations_block_key_check CHECK ((length(btrim(block_key)) > 0)),
    CONSTRAINT source_locations_char_ck CHECK ((((char_start IS NULL) AND (char_end IS NULL)) OR ((char_start IS NOT NULL) AND (char_end IS NOT NULL) AND (char_start >= 0) AND (char_end > char_start)))),
    CONSTRAINT source_locations_location_no_check CHECK ((location_no > 0)),
    CONSTRAINT source_locations_location_type_check CHECK ((location_type = ANY (ARRAY['document'::text, 'text'::text, 'page'::text, 'sheet'::text, 'message'::text]))),
    CONSTRAINT source_locations_message_no_check CHECK ((message_no > 0)),
    CONSTRAINT source_locations_page_no_check CHECK ((page_no > 0)),
    CONSTRAINT source_locations_shape_ck CHECK (
CASE location_type
    WHEN 'document'::text THEN ((page_no IS NULL) AND (sheet_name IS NULL) AND (char_start IS NULL) AND (bbox_x0 IS NULL) AND (message_no IS NULL) AND (block_key IS NULL))
    WHEN 'text'::text THEN ((char_start IS NOT NULL) AND (page_no IS NULL) AND (sheet_name IS NULL) AND (bbox_x0 IS NULL) AND (message_no IS NULL))
    WHEN 'page'::text THEN ((page_no IS NOT NULL) AND (sheet_name IS NULL) AND (message_no IS NULL))
    WHEN 'sheet'::text THEN ((sheet_name IS NOT NULL) AND (page_no IS NULL) AND (bbox_x0 IS NULL) AND (message_no IS NULL))
    WHEN 'message'::text THEN ((message_no IS NOT NULL) AND (page_no IS NULL) AND (sheet_name IS NULL) AND (bbox_x0 IS NULL))
    ELSE false
END),
    CONSTRAINT source_locations_sheet_ck CHECK ((((sheet_name IS NULL) AND (row_start IS NULL) AND (row_end IS NULL) AND (col_start IS NULL) AND (col_end IS NULL)) OR ((sheet_name IS NOT NULL) AND (row_start IS NOT NULL) AND (row_end IS NOT NULL) AND (col_start IS NOT NULL) AND (col_end IS NOT NULL) AND (row_start > 0) AND (row_end >= row_start) AND (col_start > 0) AND (col_end >= col_start)))),
    CONSTRAINT source_locations_sheet_name_check CHECK ((length(btrim(sheet_name)) > 0))
);


ALTER TABLE a4b.source_locations OWNER TO postgres;

--
-- Name: TABLE source_locations; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON TABLE a4b.source_locations IS '문서 버전 소속 원문 위치. 페이지·시트·정규화 OCR 좌표·Unicode code point 텍스트 범위.';


--
-- Name: users; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    name text NOT NULL,
    auth_provider text NOT NULL,
    auth_subject text NOT NULL,
    role text DEFAULT 'member'::text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_auth_provider_check CHECK ((length(btrim(auth_provider)) > 0)),
    CONSTRAINT users_auth_subject_check CHECK ((length(btrim(auth_subject)) > 0)),
    CONSTRAINT users_email_check CHECK (((email = lower(btrim(email))) AND (length(email) > 0))),
    CONSTRAINT users_name_check CHECK ((length(btrim(name)) > 0)),
    CONSTRAINT users_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'member'::text]))),
    CONSTRAINT users_status_check CHECK ((status = ANY (ARRAY['active'::text, 'disabled'::text])))
);


ALTER TABLE a4b.users OWNER TO postgres;

--
-- Name: verification_checks; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.verification_checks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    request_namespace text NOT NULL,
    request_id text NOT NULL,
    created_by uuid NOT NULL,
    design_session_id uuid,
    design_session_revision bigint,
    retry_of_check_id uuid,
    contract_version text DEFAULT 'a4b.verifier.v1'::text NOT NULL,
    mode text NOT NULL,
    request_json jsonb NOT NULL,
    result_json jsonb,
    error_json jsonb,
    context_json jsonb NOT NULL,
    model_digest text,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT verification_checks_check CHECK ((completed_at >= started_at)),
    CONSTRAINT verification_checks_check1 CHECK ((retry_of_check_id IS DISTINCT FROM id)),
    CONSTRAINT verification_checks_context_json_check CHECK ((jsonb_typeof(context_json) = 'object'::text)),
    CONSTRAINT verification_checks_contract_version_check CHECK ((contract_version = 'a4b.verifier.v1'::text)),
    CONSTRAINT verification_checks_design_session_revision_check CHECK ((design_session_revision >= 0)),
    CONSTRAINT verification_checks_finite_times_ck CHECK ((isfinite(started_at) AND isfinite(completed_at) AND isfinite(recorded_at))),
    CONSTRAINT verification_checks_mode_check CHECK ((mode = ANY (ARRAY['fixture'::text, 'live'::text]))),
    CONSTRAINT verification_checks_model_digest_check CHECK (((model_digest IS NULL) OR (length(btrim(model_digest)) > 0))),
    CONSTRAINT verification_checks_outcome_ck CHECK (COALESCE((((result_json IS NOT NULL) AND (error_json IS NULL)) OR ((result_json IS NULL) AND (jsonb_typeof(error_json) = 'object'::text) AND a4b.verification_string_valid((error_json -> 'code'::text), 1, 200))), false)),
    CONSTRAINT verification_checks_request_id_check CHECK (((length(request_id) >= 1) AND (length(request_id) <= 100))),
    CONSTRAINT verification_checks_request_json_check CHECK (a4b.verification_request_valid(request_json)),
    CONSTRAINT verification_checks_request_match_ck CHECK ((((request_json ->> 'request_id'::text) = request_id) AND ((request_json ->> 'contract_version'::text) = contract_version))),
    CONSTRAINT verification_checks_request_namespace_check CHECK (((length(btrim(request_namespace)) >= 1) AND (length(btrim(request_namespace)) <= 100))),
    CONSTRAINT verification_checks_result_ck CHECK (((result_json IS NULL) OR a4b.verification_result_valid(result_json, request_id, mode))),
    CONSTRAINT verification_checks_session_ck CHECK (((design_session_id IS NULL) = (design_session_revision IS NULL)))
);


ALTER TABLE a4b.verification_checks OWNER TO postgres;

--
-- Name: TABLE verification_checks; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON TABLE a4b.verification_checks IS 'Immutable completed/error call snapshots; caller must wire persistence. This is not execution approval or proof of whole-document/S3 verification.';


--
-- Name: workflow_resources; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.workflow_resources (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_version_id uuid NOT NULL,
    document_version_id uuid NOT NULL,
    binding_key text NOT NULL,
    usage_type text DEFAULT 'reference'::text NOT NULL,
    CONSTRAINT workflow_resources_binding_key_check CHECK ((length(btrim(binding_key)) > 0)),
    CONSTRAINT workflow_resources_usage_type_check CHECK ((usage_type = ANY (ARRAY['input'::text, 'reference'::text])))
);


ALTER TABLE a4b.workflow_resources OWNER TO postgres;

--
-- Name: workflow_runs; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.workflow_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_version_id uuid NOT NULL,
    requested_by uuid NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    input_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    state_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    state_revision bigint DEFAULT 0 NOT NULL,
    runtime_version text NOT NULL,
    lease_owner text,
    lease_expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    CONSTRAINT workflow_runs_check CHECK (((started_at IS NULL) OR (started_at >= created_at))),
    CONSTRAINT workflow_runs_check1 CHECK (((finished_at IS NULL) OR (finished_at >= COALESCE(started_at, created_at)))),
    CONSTRAINT workflow_runs_idempotency_key_check CHECK ((length(btrim(idempotency_key)) > 0)),
    CONSTRAINT workflow_runs_input_json_check CHECK ((jsonb_typeof(input_json) = 'object'::text)),
    CONSTRAINT workflow_runs_lease_owner_check CHECK (((lease_owner IS NULL) OR (length(btrim(lease_owner)) > 0))),
    CONSTRAINT workflow_runs_lease_pair_ck CHECK (((lease_owner IS NULL) = (lease_expires_at IS NULL))),
    CONSTRAINT workflow_runs_lease_status_ck CHECK (((lease_owner IS NULL) OR (status = 'running'::text))),
    CONSTRAINT workflow_runs_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT workflow_runs_runtime_version_check CHECK ((length(btrim(runtime_version)) > 0)),
    CONSTRAINT workflow_runs_started_ck CHECK (((status <> ALL (ARRAY['running'::text, 'waiting_input'::text, 'waiting_approval'::text, 'succeeded'::text])) OR (started_at IS NOT NULL))),
    CONSTRAINT workflow_runs_state_json_check CHECK ((jsonb_typeof(state_json) = 'object'::text)),
    CONSTRAINT workflow_runs_state_revision_check CHECK ((state_revision >= 0)),
    CONSTRAINT workflow_runs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'waiting_input'::text, 'waiting_approval'::text, 'succeeded'::text, 'failed'::text, 'canceled'::text]))),
    CONSTRAINT workflow_runs_terminal_time_ck CHECK (((status = ANY (ARRAY['succeeded'::text, 'failed'::text, 'canceled'::text])) = (finished_at IS NOT NULL)))
);


ALTER TABLE a4b.workflow_runs OWNER TO postgres;

--
-- Name: COLUMN workflow_runs.state_revision; Type: COMMENT; Schema: a4b; Owner: postgres
--

COMMENT ON COLUMN a4b.workflow_runs.state_revision IS '백엔드 CAS/lease 점유 검증용. UPDATE WHERE에서 예상 값·점유자·만료를 함께 검증해야 함.';


--
-- Name: workflow_versions; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.workflow_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_id uuid NOT NULL,
    version_no integer NOT NULL,
    graph_json jsonb NOT NULL,
    graph_schema_version text NOT NULL,
    graph_hash text NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT workflow_versions_graph_hash_check CHECK ((graph_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT workflow_versions_graph_json_check CHECK ((jsonb_typeof(graph_json) = 'object'::text)),
    CONSTRAINT workflow_versions_graph_schema_version_check CHECK ((length(btrim(graph_schema_version)) > 0)),
    CONSTRAINT workflow_versions_version_no_check CHECK ((version_no > 0))
);


ALTER TABLE a4b.workflow_versions OWNER TO postgres;

--
-- Name: workflows; Type: TABLE; Schema: a4b; Owner: postgres
--

CREATE TABLE a4b.workflows (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_by uuid NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT workflows_name_check CHECK ((length(btrim(name)) > 0)),
    CONSTRAINT workflows_status_check CHECK ((status = ANY (ARRAY['active'::text, 'archived'::text])))
);


ALTER TABLE a4b.workflows OWNER TO postgres;

--
-- Name: approval_requests approval_requests_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.approval_requests
    ADD CONSTRAINT approval_requests_pkey PRIMARY KEY (id);


--
-- Name: chunk_locations chunk_locations_order_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.chunk_locations
    ADD CONSTRAINT chunk_locations_order_uq UNIQUE (chunk_id, location_order);


--
-- Name: chunk_locations chunk_locations_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.chunk_locations
    ADD CONSTRAINT chunk_locations_pkey PRIMARY KEY (chunk_id, source_location_id);


--
-- Name: chunk_locations chunk_locations_scope_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.chunk_locations
    ADD CONSTRAINT chunk_locations_scope_uq UNIQUE (chunk_id, source_location_id, document_version_id);


--
-- Name: design_sessions design_sessions_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.design_sessions
    ADD CONSTRAINT design_sessions_pkey PRIMARY KEY (id);


--
-- Name: document_chunks document_chunks_number_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.document_chunks
    ADD CONSTRAINT document_chunks_number_uq UNIQUE (document_version_id, chunk_no);


--
-- Name: document_chunks document_chunks_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.document_chunks
    ADD CONSTRAINT document_chunks_pkey PRIMARY KEY (id);


--
-- Name: document_chunks document_chunks_scope_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.document_chunks
    ADD CONSTRAINT document_chunks_scope_uq UNIQUE (id, document_version_id);


--
-- Name: document_types document_types_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.document_types
    ADD CONSTRAINT document_types_pkey PRIMARY KEY (code);


--
-- Name: document_versions document_versions_number_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.document_versions
    ADD CONSTRAINT document_versions_number_uq UNIQUE (document_id, version_no);


--
-- Name: document_versions document_versions_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.document_versions
    ADD CONSTRAINT document_versions_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: extraction_schema_versions extraction_schema_versions_key_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.extraction_schema_versions
    ADD CONSTRAINT extraction_schema_versions_key_uq UNIQUE (schema_key);


--
-- Name: extraction_schema_versions extraction_schema_versions_number_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.extraction_schema_versions
    ADD CONSTRAINT extraction_schema_versions_number_uq UNIQUE (document_type_code, schema_name, version_no);


--
-- Name: extraction_schema_versions extraction_schema_versions_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.extraction_schema_versions
    ADD CONSTRAINT extraction_schema_versions_pkey PRIMARY KEY (id);


--
-- Name: extraction_schema_versions extraction_schema_versions_scope_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.extraction_schema_versions
    ADD CONSTRAINT extraction_schema_versions_scope_uq UNIQUE (id, schema_key);


--
-- Name: node_runs node_runs_attempt_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.node_runs
    ADD CONSTRAINT node_runs_attempt_uq UNIQUE (run_id, node_key, attempt_no);


--
-- Name: node_runs node_runs_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.node_runs
    ADD CONSTRAINT node_runs_pkey PRIMARY KEY (id);


--
-- Name: node_runs node_runs_scope_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.node_runs
    ADD CONSTRAINT node_runs_scope_uq UNIQUE (id, run_id);


--
-- Name: output_evidence output_evidence_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.output_evidence
    ADD CONSTRAINT output_evidence_pkey PRIMARY KEY (id);


--
-- Name: outputs outputs_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.outputs
    ADD CONSTRAINT outputs_pkey PRIMARY KEY (id);


--
-- Name: outputs outputs_revision_scope_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.outputs
    ADD CONSTRAINT outputs_revision_scope_uq UNIQUE (id, run_id, output_key);


--
-- Name: outputs outputs_revision_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.outputs
    ADD CONSTRAINT outputs_revision_uq UNIQUE (run_id, output_key, revision_no);


--
-- Name: outputs outputs_run_scope_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.outputs
    ADD CONSTRAINT outputs_run_scope_uq UNIQUE (id, run_id);


--
-- Name: run_documents run_documents_binding_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.run_documents
    ADD CONSTRAINT run_documents_binding_uq UNIQUE (run_id, binding_key, usage_type, document_version_id);


--
-- Name: run_documents run_documents_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.run_documents
    ADD CONSTRAINT run_documents_pkey PRIMARY KEY (id);


--
-- Name: run_documents run_documents_run_scope_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.run_documents
    ADD CONSTRAINT run_documents_run_scope_uq UNIQUE (id, run_id);


--
-- Name: run_documents run_documents_scope_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.run_documents
    ADD CONSTRAINT run_documents_scope_uq UNIQUE (id, document_version_id);


--
-- Name: source_locations source_locations_number_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.source_locations
    ADD CONSTRAINT source_locations_number_uq UNIQUE (document_version_id, location_no);


--
-- Name: source_locations source_locations_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.source_locations
    ADD CONSTRAINT source_locations_pkey PRIMARY KEY (id);


--
-- Name: source_locations source_locations_scope_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.source_locations
    ADD CONSTRAINT source_locations_scope_uq UNIQUE (id, document_version_id);


--
-- Name: users users_auth_identity_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.users
    ADD CONSTRAINT users_auth_identity_uq UNIQUE (auth_provider, auth_subject);


--
-- Name: users users_email_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.users
    ADD CONSTRAINT users_email_uq UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: verification_checks verification_checks_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.verification_checks
    ADD CONSTRAINT verification_checks_pkey PRIMARY KEY (id);


--
-- Name: verification_checks verification_checks_request_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.verification_checks
    ADD CONSTRAINT verification_checks_request_uq UNIQUE (request_namespace, request_id);


--
-- Name: workflow_resources workflow_resources_binding_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_resources
    ADD CONSTRAINT workflow_resources_binding_uq UNIQUE (workflow_version_id, binding_key, usage_type, document_version_id);


--
-- Name: workflow_resources workflow_resources_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_resources
    ADD CONSTRAINT workflow_resources_pkey PRIMARY KEY (id);


--
-- Name: workflow_runs workflow_runs_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_runs
    ADD CONSTRAINT workflow_runs_pkey PRIMARY KEY (id);


--
-- Name: workflow_runs workflow_runs_request_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_runs
    ADD CONSTRAINT workflow_runs_request_uq UNIQUE (requested_by, idempotency_key);


--
-- Name: workflow_versions workflow_versions_number_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_versions
    ADD CONSTRAINT workflow_versions_number_uq UNIQUE (workflow_id, version_no);


--
-- Name: workflow_versions workflow_versions_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_versions
    ADD CONSTRAINT workflow_versions_pkey PRIMARY KEY (id);


--
-- Name: workflow_versions workflow_versions_scope_uq; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_versions
    ADD CONSTRAINT workflow_versions_scope_uq UNIQUE (id, workflow_id);


--
-- Name: workflows workflows_pkey; Type: CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflows
    ADD CONSTRAINT workflows_pkey PRIMARY KEY (id);


--
-- Name: approval_requests_decider_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX approval_requests_decider_idx ON a4b.approval_requests USING btree (decider_id) WHERE (decider_id IS NOT NULL);


--
-- Name: approval_requests_one_pending_per_run_uq; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE UNIQUE INDEX approval_requests_one_pending_per_run_uq ON a4b.approval_requests USING btree (run_id) WHERE (status = 'pending'::text);


--
-- Name: approval_requests_reviewer_list_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX approval_requests_reviewer_list_idx ON a4b.approval_requests USING btree (reviewer_id, status, created_at DESC);


--
-- Name: approval_requests_target_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX approval_requests_target_idx ON a4b.approval_requests USING btree (target_output_id);


--
-- Name: approval_requests_waiting_node_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX approval_requests_waiting_node_idx ON a4b.approval_requests USING btree (waiting_node_run_id);


--
-- Name: chunk_locations_source_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX chunk_locations_source_idx ON a4b.chunk_locations USING btree (source_location_id, document_version_id);


--
-- Name: chunk_locations_version_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX chunk_locations_version_idx ON a4b.chunk_locations USING btree (document_version_id);


--
-- Name: design_sessions_base_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX design_sessions_base_idx ON a4b.design_sessions USING btree (base_version_id) WHERE (base_version_id IS NOT NULL);


--
-- Name: design_sessions_creator_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX design_sessions_creator_idx ON a4b.design_sessions USING btree (created_by);


--
-- Name: design_sessions_workflow_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX design_sessions_workflow_idx ON a4b.design_sessions USING btree (workflow_id, updated_at DESC);


--
-- Name: document_versions_processing_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX document_versions_processing_idx ON a4b.document_versions USING btree (status, created_at) WHERE (status = ANY (ARRAY['uploaded'::text, 'processing'::text]));


--
-- Name: document_versions_type_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX document_versions_type_idx ON a4b.document_versions USING btree (document_type_code);


--
-- Name: documents_active_list_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX documents_active_list_idx ON a4b.documents USING btree (document_type_code, created_at DESC) WHERE (archived_at IS NULL);


--
-- Name: documents_creator_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX documents_creator_idx ON a4b.documents USING btree (created_by);


--
-- Name: documents_type_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX documents_type_idx ON a4b.documents USING btree (document_type_code);


--
-- Name: node_runs_assignee_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX node_runs_assignee_idx ON a4b.node_runs USING btree (assignee_id) WHERE (assignee_id IS NOT NULL);


--
-- Name: node_runs_one_waiting_per_run_uq; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE UNIQUE INDEX node_runs_one_waiting_per_run_uq ON a4b.node_runs USING btree (run_id) WHERE (status = 'waiting'::text);


--
-- Name: node_runs_responded_by_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX node_runs_responded_by_idx ON a4b.node_runs USING btree (responded_by) WHERE (responded_by IS NOT NULL);


--
-- Name: output_evidence_chunk_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX output_evidence_chunk_idx ON a4b.output_evidence USING btree (chunk_id) WHERE (chunk_id IS NOT NULL);


--
-- Name: output_evidence_output_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX output_evidence_output_idx ON a4b.output_evidence USING btree (output_id);


--
-- Name: output_evidence_run_document_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX output_evidence_run_document_idx ON a4b.output_evidence USING btree (run_document_id);


--
-- Name: output_evidence_source_location_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX output_evidence_source_location_idx ON a4b.output_evidence USING btree (source_location_id);


--
-- Name: output_evidence_version_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX output_evidence_version_idx ON a4b.output_evidence USING btree (document_version_id);


--
-- Name: outputs_creator_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX outputs_creator_idx ON a4b.outputs USING btree (created_by) WHERE (created_by IS NOT NULL);


--
-- Name: outputs_extraction_schema_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX outputs_extraction_schema_idx ON a4b.outputs USING btree (extraction_schema_version_id) WHERE (extraction_schema_version_id IS NOT NULL);


--
-- Name: outputs_previous_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX outputs_previous_idx ON a4b.outputs USING btree (previous_output_id) WHERE (previous_output_id IS NOT NULL);


--
-- Name: outputs_producer_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX outputs_producer_idx ON a4b.outputs USING btree (producer_node_run_id) WHERE (producer_node_run_id IS NOT NULL);


--
-- Name: outputs_source_document_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX outputs_source_document_idx ON a4b.outputs USING btree (source_run_document_id) WHERE (source_run_document_id IS NOT NULL);


--
-- Name: run_documents_search_scope_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX run_documents_search_scope_idx ON a4b.run_documents USING btree (run_id, document_version_id);


--
-- Name: run_documents_version_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX run_documents_version_idx ON a4b.run_documents USING btree (document_version_id);


--
-- Name: source_locations_page_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX source_locations_page_idx ON a4b.source_locations USING btree (document_version_id, page_no) WHERE (page_no IS NOT NULL);


--
-- Name: source_locations_sheet_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX source_locations_sheet_idx ON a4b.source_locations USING btree (document_version_id, sheet_name, row_start, col_start) WHERE (sheet_name IS NOT NULL);


--
-- Name: verification_checks_creator_time_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX verification_checks_creator_time_idx ON a4b.verification_checks USING btree (created_by, started_at DESC);


--
-- Name: verification_checks_retry_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX verification_checks_retry_idx ON a4b.verification_checks USING btree (retry_of_check_id) WHERE (retry_of_check_id IS NOT NULL);


--
-- Name: verification_checks_session_time_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX verification_checks_session_time_idx ON a4b.verification_checks USING btree (design_session_id, started_at DESC) WHERE (design_session_id IS NOT NULL);


--
-- Name: workflow_resources_document_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX workflow_resources_document_idx ON a4b.workflow_resources USING btree (document_version_id);


--
-- Name: workflow_runs_queue_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX workflow_runs_queue_idx ON a4b.workflow_runs USING btree (status, lease_expires_at, created_at) WHERE (status = ANY (ARRAY['queued'::text, 'running'::text]));


--
-- Name: workflow_runs_requester_list_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX workflow_runs_requester_list_idx ON a4b.workflow_runs USING btree (requested_by, created_at DESC);


--
-- Name: workflow_runs_version_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX workflow_runs_version_idx ON a4b.workflow_runs USING btree (workflow_version_id);


--
-- Name: workflow_versions_creator_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX workflow_versions_creator_idx ON a4b.workflow_versions USING btree (created_by);


--
-- Name: workflows_creator_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX workflows_creator_idx ON a4b.workflows USING btree (created_by);


--
-- Name: workflows_list_idx; Type: INDEX; Schema: a4b; Owner: postgres
--

CREATE INDEX workflows_list_idx ON a4b.workflows USING btree (status, created_at DESC);


--
-- Name: approval_requests approval_requests_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER approval_requests_guard BEFORE INSERT OR DELETE OR UPDATE ON a4b.approval_requests FOR EACH ROW EXECUTE FUNCTION a4b.guard_approval_request();


--
-- Name: chunk_locations chunk_locations_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER chunk_locations_guard BEFORE INSERT OR DELETE OR UPDATE ON a4b.chunk_locations FOR EACH ROW EXECUTE FUNCTION a4b.guard_chunk_location();


--
-- Name: design_sessions design_sessions_touch; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER design_sessions_touch BEFORE UPDATE ON a4b.design_sessions FOR EACH ROW EXECUTE FUNCTION a4b.touch_editable_row();


--
-- Name: document_chunks document_chunks_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER document_chunks_guard BEFORE INSERT OR DELETE OR UPDATE ON a4b.document_chunks FOR EACH ROW EXECUTE FUNCTION a4b.guard_document_chunk();


--
-- Name: document_chunks document_chunks_parser_segments_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER document_chunks_parser_segments_guard BEFORE INSERT OR UPDATE ON a4b.document_chunks FOR EACH ROW EXECUTE FUNCTION a4b.guard_parser_chunk_segments();


--
-- Name: document_types document_types_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER document_types_guard BEFORE DELETE OR UPDATE ON a4b.document_types FOR EACH ROW EXECUTE FUNCTION a4b.guard_document_type();


--
-- Name: document_versions document_versions_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER document_versions_guard BEFORE INSERT OR DELETE OR UPDATE ON a4b.document_versions FOR EACH ROW EXECUTE FUNCTION a4b.guard_document_version();


--
-- Name: document_versions document_versions_parser_contract_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER document_versions_parser_contract_guard BEFORE INSERT OR UPDATE ON a4b.document_versions FOR EACH ROW EXECUTE FUNCTION a4b.guard_parser_contract_version();


--
-- Name: documents documents_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER documents_guard BEFORE INSERT OR UPDATE ON a4b.documents FOR EACH ROW EXECUTE FUNCTION a4b.guard_document();


--
-- Name: extraction_schema_versions extraction_schema_versions_immutable; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER extraction_schema_versions_immutable BEFORE DELETE OR UPDATE ON a4b.extraction_schema_versions FOR EACH ROW EXECUTE FUNCTION a4b.reject_snapshot_change();


--
-- Name: node_runs node_runs_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER node_runs_guard BEFORE INSERT OR DELETE OR UPDATE ON a4b.node_runs FOR EACH ROW EXECUTE FUNCTION a4b.guard_node_run();


--
-- Name: output_evidence output_evidence_immutable; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER output_evidence_immutable BEFORE DELETE OR UPDATE ON a4b.output_evidence FOR EACH ROW EXECUTE FUNCTION a4b.reject_snapshot_change();


--
-- Name: output_evidence output_evidence_scope_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER output_evidence_scope_guard BEFORE INSERT ON a4b.output_evidence FOR EACH ROW EXECUTE FUNCTION a4b.check_evidence_scope();


--
-- Name: outputs outputs_extraction_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER outputs_extraction_guard BEFORE INSERT ON a4b.outputs FOR EACH ROW EXECUTE FUNCTION a4b.guard_extraction_output();


--
-- Name: outputs outputs_immutable; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER outputs_immutable BEFORE DELETE OR UPDATE ON a4b.outputs FOR EACH ROW EXECUTE FUNCTION a4b.reject_snapshot_change();


--
-- Name: outputs outputs_previous_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER outputs_previous_guard BEFORE INSERT ON a4b.outputs FOR EACH ROW EXECUTE FUNCTION a4b.check_previous_output();


--
-- Name: run_documents run_documents_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER run_documents_guard BEFORE INSERT OR DELETE OR UPDATE ON a4b.run_documents FOR EACH ROW EXECUTE FUNCTION a4b.guard_run_document();


--
-- Name: source_locations source_locations_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER source_locations_guard BEFORE INSERT OR DELETE OR UPDATE ON a4b.source_locations FOR EACH ROW EXECUTE FUNCTION a4b.guard_source_location();


--
-- Name: verification_checks verification_checks_immutable; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER verification_checks_immutable BEFORE DELETE OR UPDATE ON a4b.verification_checks FOR EACH ROW EXECUTE FUNCTION a4b.reject_snapshot_change();


--
-- Name: verification_checks verification_checks_insert_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER verification_checks_insert_guard BEFORE INSERT ON a4b.verification_checks FOR EACH ROW EXECUTE FUNCTION a4b.verification_history_guard();


--
-- Name: workflow_resources workflow_resources_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER workflow_resources_guard BEFORE INSERT ON a4b.workflow_resources FOR EACH ROW EXECUTE FUNCTION a4b.guard_workflow_resource();


--
-- Name: workflow_resources workflow_resources_immutable; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER workflow_resources_immutable BEFORE DELETE OR UPDATE ON a4b.workflow_resources FOR EACH ROW EXECUTE FUNCTION a4b.reject_snapshot_change();


--
-- Name: workflow_runs workflow_runs_guard; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER workflow_runs_guard BEFORE INSERT OR DELETE OR UPDATE ON a4b.workflow_runs FOR EACH ROW EXECUTE FUNCTION a4b.guard_workflow_run();


--
-- Name: workflow_versions workflow_versions_immutable; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER workflow_versions_immutable BEFORE DELETE OR UPDATE ON a4b.workflow_versions FOR EACH ROW EXECUTE FUNCTION a4b.reject_snapshot_change();


--
-- Name: workflows workflows_touch; Type: TRIGGER; Schema: a4b; Owner: postgres
--

CREATE TRIGGER workflows_touch BEFORE UPDATE ON a4b.workflows FOR EACH ROW EXECUTE FUNCTION a4b.touch_editable_row();


--
-- Name: approval_requests approval_requests_decider_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.approval_requests
    ADD CONSTRAINT approval_requests_decider_id_fkey FOREIGN KEY (decider_id) REFERENCES a4b.users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: approval_requests approval_requests_node_scope_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.approval_requests
    ADD CONSTRAINT approval_requests_node_scope_fk FOREIGN KEY (waiting_node_run_id, run_id) REFERENCES a4b.node_runs(id, run_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: approval_requests approval_requests_output_scope_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.approval_requests
    ADD CONSTRAINT approval_requests_output_scope_fk FOREIGN KEY (target_output_id, run_id) REFERENCES a4b.outputs(id, run_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: approval_requests approval_requests_reviewer_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.approval_requests
    ADD CONSTRAINT approval_requests_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES a4b.users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: approval_requests approval_requests_run_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.approval_requests
    ADD CONSTRAINT approval_requests_run_id_fkey FOREIGN KEY (run_id) REFERENCES a4b.workflow_runs(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: chunk_locations chunk_locations_chunk_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.chunk_locations
    ADD CONSTRAINT chunk_locations_chunk_fk FOREIGN KEY (chunk_id, document_version_id) REFERENCES a4b.document_chunks(id, document_version_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: chunk_locations chunk_locations_source_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.chunk_locations
    ADD CONSTRAINT chunk_locations_source_fk FOREIGN KEY (source_location_id, document_version_id) REFERENCES a4b.source_locations(id, document_version_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: design_sessions design_sessions_base_scope_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.design_sessions
    ADD CONSTRAINT design_sessions_base_scope_fk FOREIGN KEY (base_version_id, workflow_id) REFERENCES a4b.workflow_versions(id, workflow_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: design_sessions design_sessions_created_by_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.design_sessions
    ADD CONSTRAINT design_sessions_created_by_fkey FOREIGN KEY (created_by) REFERENCES a4b.users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: design_sessions design_sessions_workflow_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.design_sessions
    ADD CONSTRAINT design_sessions_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES a4b.workflows(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: document_chunks document_chunks_document_version_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.document_chunks
    ADD CONSTRAINT document_chunks_document_version_id_fkey FOREIGN KEY (document_version_id) REFERENCES a4b.document_versions(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: document_versions document_versions_document_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.document_versions
    ADD CONSTRAINT document_versions_document_id_fkey FOREIGN KEY (document_id) REFERENCES a4b.documents(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: document_versions document_versions_document_type_code_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.document_versions
    ADD CONSTRAINT document_versions_document_type_code_fkey FOREIGN KEY (document_type_code) REFERENCES a4b.document_types(code) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: documents documents_created_by_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.documents
    ADD CONSTRAINT documents_created_by_fkey FOREIGN KEY (created_by) REFERENCES a4b.users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: documents documents_document_type_code_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.documents
    ADD CONSTRAINT documents_document_type_code_fkey FOREIGN KEY (document_type_code) REFERENCES a4b.document_types(code) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: extraction_schema_versions extraction_schema_versions_document_type_code_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.extraction_schema_versions
    ADD CONSTRAINT extraction_schema_versions_document_type_code_fkey FOREIGN KEY (document_type_code) REFERENCES a4b.document_types(code) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: node_runs node_runs_assignee_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.node_runs
    ADD CONSTRAINT node_runs_assignee_id_fkey FOREIGN KEY (assignee_id) REFERENCES a4b.users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: node_runs node_runs_responded_by_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.node_runs
    ADD CONSTRAINT node_runs_responded_by_fkey FOREIGN KEY (responded_by) REFERENCES a4b.users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: node_runs node_runs_run_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.node_runs
    ADD CONSTRAINT node_runs_run_id_fkey FOREIGN KEY (run_id) REFERENCES a4b.workflow_runs(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: output_evidence output_evidence_chunk_location_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.output_evidence
    ADD CONSTRAINT output_evidence_chunk_location_fk FOREIGN KEY (chunk_id, source_location_id, document_version_id) REFERENCES a4b.chunk_locations(chunk_id, source_location_id, document_version_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: output_evidence output_evidence_chunk_scope_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.output_evidence
    ADD CONSTRAINT output_evidence_chunk_scope_fk FOREIGN KEY (chunk_id, document_version_id) REFERENCES a4b.document_chunks(id, document_version_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: output_evidence output_evidence_document_version_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.output_evidence
    ADD CONSTRAINT output_evidence_document_version_id_fkey FOREIGN KEY (document_version_id) REFERENCES a4b.document_versions(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: output_evidence output_evidence_output_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.output_evidence
    ADD CONSTRAINT output_evidence_output_id_fkey FOREIGN KEY (output_id) REFERENCES a4b.outputs(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: output_evidence output_evidence_run_document_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.output_evidence
    ADD CONSTRAINT output_evidence_run_document_fk FOREIGN KEY (run_document_id, document_version_id) REFERENCES a4b.run_documents(id, document_version_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: output_evidence output_evidence_source_location_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.output_evidence
    ADD CONSTRAINT output_evidence_source_location_fk FOREIGN KEY (source_location_id, document_version_id) REFERENCES a4b.source_locations(id, document_version_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: outputs outputs_created_by_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.outputs
    ADD CONSTRAINT outputs_created_by_fkey FOREIGN KEY (created_by) REFERENCES a4b.users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: outputs outputs_extraction_schema_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.outputs
    ADD CONSTRAINT outputs_extraction_schema_fk FOREIGN KEY (extraction_schema_version_id, schema_key) REFERENCES a4b.extraction_schema_versions(id, schema_key) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: outputs outputs_previous_scope_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.outputs
    ADD CONSTRAINT outputs_previous_scope_fk FOREIGN KEY (previous_output_id, run_id, output_key) REFERENCES a4b.outputs(id, run_id, output_key) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: outputs outputs_producer_scope_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.outputs
    ADD CONSTRAINT outputs_producer_scope_fk FOREIGN KEY (producer_node_run_id, run_id) REFERENCES a4b.node_runs(id, run_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: outputs outputs_run_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.outputs
    ADD CONSTRAINT outputs_run_id_fkey FOREIGN KEY (run_id) REFERENCES a4b.workflow_runs(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: outputs outputs_source_document_fk; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.outputs
    ADD CONSTRAINT outputs_source_document_fk FOREIGN KEY (source_run_document_id, run_id) REFERENCES a4b.run_documents(id, run_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: run_documents run_documents_document_version_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.run_documents
    ADD CONSTRAINT run_documents_document_version_id_fkey FOREIGN KEY (document_version_id) REFERENCES a4b.document_versions(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: run_documents run_documents_run_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.run_documents
    ADD CONSTRAINT run_documents_run_id_fkey FOREIGN KEY (run_id) REFERENCES a4b.workflow_runs(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: source_locations source_locations_document_version_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.source_locations
    ADD CONSTRAINT source_locations_document_version_id_fkey FOREIGN KEY (document_version_id) REFERENCES a4b.document_versions(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: verification_checks verification_checks_created_by_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.verification_checks
    ADD CONSTRAINT verification_checks_created_by_fkey FOREIGN KEY (created_by) REFERENCES a4b.users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: verification_checks verification_checks_design_session_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.verification_checks
    ADD CONSTRAINT verification_checks_design_session_id_fkey FOREIGN KEY (design_session_id) REFERENCES a4b.design_sessions(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: verification_checks verification_checks_retry_of_check_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.verification_checks
    ADD CONSTRAINT verification_checks_retry_of_check_id_fkey FOREIGN KEY (retry_of_check_id) REFERENCES a4b.verification_checks(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: workflow_resources workflow_resources_document_version_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_resources
    ADD CONSTRAINT workflow_resources_document_version_id_fkey FOREIGN KEY (document_version_id) REFERENCES a4b.document_versions(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: workflow_resources workflow_resources_workflow_version_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_resources
    ADD CONSTRAINT workflow_resources_workflow_version_id_fkey FOREIGN KEY (workflow_version_id) REFERENCES a4b.workflow_versions(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: workflow_runs workflow_runs_requested_by_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_runs
    ADD CONSTRAINT workflow_runs_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES a4b.users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: workflow_runs workflow_runs_workflow_version_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_runs
    ADD CONSTRAINT workflow_runs_workflow_version_id_fkey FOREIGN KEY (workflow_version_id) REFERENCES a4b.workflow_versions(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: workflow_versions workflow_versions_created_by_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_versions
    ADD CONSTRAINT workflow_versions_created_by_fkey FOREIGN KEY (created_by) REFERENCES a4b.users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: workflow_versions workflow_versions_workflow_id_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflow_versions
    ADD CONSTRAINT workflow_versions_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES a4b.workflows(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: workflows workflows_created_by_fkey; Type: FK CONSTRAINT; Schema: a4b; Owner: postgres
--

ALTER TABLE ONLY a4b.workflows
    ADD CONSTRAINT workflows_created_by_fkey FOREIGN KEY (created_by) REFERENCES a4b.users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: SCHEMA a4b; Type: ACL; Schema: -; Owner: postgres
--

GRANT USAGE ON SCHEMA a4b TO a4b_app;
GRANT USAGE ON SCHEMA a4b TO a4b_guard_owner;


--
-- Name: FUNCTION check_evidence_scope(); Type: ACL; Schema: a4b; Owner: a4b_guard_owner
--

REVOKE ALL ON FUNCTION a4b.check_evidence_scope() FROM PUBLIC;


--
-- Name: FUNCTION check_previous_output(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.check_previous_output() FROM PUBLIC;


--
-- Name: FUNCTION extraction_matches_schema(p_value jsonb, p_schema jsonb); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.extraction_matches_schema(p_value jsonb, p_schema jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.extraction_matches_schema(p_value jsonb, p_schema jsonb) TO a4b_app;


--
-- Name: FUNCTION guard_approval_request(); Type: ACL; Schema: a4b; Owner: a4b_guard_owner
--

REVOKE ALL ON FUNCTION a4b.guard_approval_request() FROM PUBLIC;


--
-- Name: FUNCTION guard_chunk_location(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.guard_chunk_location() FROM PUBLIC;


--
-- Name: FUNCTION guard_document(); Type: ACL; Schema: a4b; Owner: a4b_guard_owner
--

REVOKE ALL ON FUNCTION a4b.guard_document() FROM PUBLIC;


--
-- Name: FUNCTION guard_document_chunk(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.guard_document_chunk() FROM PUBLIC;


--
-- Name: FUNCTION guard_document_type(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.guard_document_type() FROM PUBLIC;


--
-- Name: FUNCTION guard_document_version(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.guard_document_version() FROM PUBLIC;


--
-- Name: FUNCTION guard_extraction_output(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.guard_extraction_output() FROM PUBLIC;


--
-- Name: FUNCTION guard_node_run(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.guard_node_run() FROM PUBLIC;


--
-- Name: FUNCTION guard_parser_chunk_segments(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.guard_parser_chunk_segments() FROM PUBLIC;


--
-- Name: FUNCTION guard_parser_contract_version(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.guard_parser_contract_version() FROM PUBLIC;


--
-- Name: FUNCTION guard_run_document(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.guard_run_document() FROM PUBLIC;


--
-- Name: FUNCTION guard_source_location(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.guard_source_location() FROM PUBLIC;


--
-- Name: FUNCTION guard_workflow_resource(); Type: ACL; Schema: a4b; Owner: a4b_guard_owner
--

REVOKE ALL ON FUNCTION a4b.guard_workflow_resource() FROM PUBLIC;


--
-- Name: FUNCTION guard_workflow_run(); Type: ACL; Schema: a4b; Owner: a4b_guard_owner
--

REVOKE ALL ON FUNCTION a4b.guard_workflow_run() FROM PUBLIC;


--
-- Name: FUNCTION json_pointer_exists(p_value jsonb, p_pointer text); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.json_pointer_exists(p_value jsonb, p_pointer text) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.json_pointer_exists(p_value jsonb, p_pointer text) TO a4b_app;
GRANT ALL ON FUNCTION a4b.json_pointer_exists(p_value jsonb, p_pointer text) TO a4b_guard_owner;


--
-- Name: FUNCTION parser_contract_metadata_valid(p_metadata jsonb, p_content_hash text, p_mime_type text, p_size_bytes bigint, p_processing_config jsonb); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.parser_contract_metadata_valid(p_metadata jsonb, p_content_hash text, p_mime_type text, p_size_bytes bigint, p_processing_config jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.parser_contract_metadata_valid(p_metadata jsonb, p_content_hash text, p_mime_type text, p_size_bytes bigint, p_processing_config jsonb) TO a4b_app;


--
-- Name: FUNCTION parser_error_valid(p_error jsonb); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.parser_error_valid(p_error jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.parser_error_valid(p_error jsonb) TO a4b_app;


--
-- Name: FUNCTION parser_json_integer_valid(p_value jsonb, p_min bigint, p_max bigint); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.parser_json_integer_valid(p_value jsonb, p_min bigint, p_max bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.parser_json_integer_valid(p_value jsonb, p_min bigint, p_max bigint) TO a4b_app;


--
-- Name: FUNCTION parser_page_numbers_valid(p_pages jsonb, p_page_count bigint, p_allow_empty boolean); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.parser_page_numbers_valid(p_pages jsonb, p_page_count bigint, p_allow_empty boolean) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.parser_page_numbers_valid(p_pages jsonb, p_page_count bigint, p_allow_empty boolean) TO a4b_app;


--
-- Name: FUNCTION parser_source_segments_valid(p_segments jsonb, p_content text, p_document_version_id uuid, p_canonical_length bigint); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.parser_source_segments_valid(p_segments jsonb, p_content text, p_document_version_id uuid, p_canonical_length bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.parser_source_segments_valid(p_segments jsonb, p_content text, p_document_version_id uuid, p_canonical_length bigint) TO a4b_app;


--
-- Name: FUNCTION reject_snapshot_change(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.reject_snapshot_change() FROM PUBLIC;


--
-- Name: FUNCTION search_document_chunks(p_actor_id uuid, p_run_id uuid, p_query extensions.vector, p_embedding_profile jsonb, p_limit integer); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.search_document_chunks(p_actor_id uuid, p_run_id uuid, p_query extensions.vector, p_embedding_profile jsonb, p_limit integer) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.search_document_chunks(p_actor_id uuid, p_run_id uuid, p_query extensions.vector, p_embedding_profile jsonb, p_limit integer) TO a4b_app;


--
-- Name: FUNCTION supported_extraction_schema(p_schema jsonb); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.supported_extraction_schema(p_schema jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.supported_extraction_schema(p_schema jsonb) TO a4b_app;


--
-- Name: FUNCTION touch_editable_row(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.touch_editable_row() FROM PUBLIC;


--
-- Name: FUNCTION unified_parser_keys_valid(p_object jsonb, p_keys text[]); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.unified_parser_keys_valid(p_object jsonb, p_keys text[]) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.unified_parser_keys_valid(p_object jsonb, p_keys text[]) TO a4b_app;


--
-- Name: FUNCTION unified_parser_location_valid(p_location jsonb, p_metadata jsonb, p_canonical_length bigint); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.unified_parser_location_valid(p_location jsonb, p_metadata jsonb, p_canonical_length bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.unified_parser_location_valid(p_location jsonb, p_metadata jsonb, p_canonical_length bigint) TO a4b_app;


--
-- Name: FUNCTION unified_parser_metadata_valid(p_metadata jsonb, p_content_hash text, p_mime_type text, p_size_bytes bigint, p_processing_config jsonb); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.unified_parser_metadata_valid(p_metadata jsonb, p_content_hash text, p_mime_type text, p_size_bytes bigint, p_processing_config jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.unified_parser_metadata_valid(p_metadata jsonb, p_content_hash text, p_mime_type text, p_size_bytes bigint, p_processing_config jsonb) TO a4b_app;


--
-- Name: FUNCTION unified_parser_text_valid(p_value jsonb, p_max integer); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.unified_parser_text_valid(p_value jsonb, p_max integer) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.unified_parser_text_valid(p_value jsonb, p_max integer) TO a4b_app;


--
-- Name: FUNCTION valid_embedding_profile(p_profile jsonb); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.valid_embedding_profile(p_profile jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.valid_embedding_profile(p_profile jsonb) TO a4b_app;


--
-- Name: FUNCTION verification_document_valid(p_document jsonb); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.verification_document_valid(p_document jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.verification_document_valid(p_document jsonb) TO a4b_app;


--
-- Name: FUNCTION verification_draft_valid(p_draft jsonb); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.verification_draft_valid(p_draft jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.verification_draft_valid(p_draft jsonb) TO a4b_app;


--
-- Name: FUNCTION verification_history_guard(); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.verification_history_guard() FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.verification_history_guard() TO a4b_app;


--
-- Name: FUNCTION verification_object_shape(p_value jsonb, p_required text[], p_allowed text[]); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.verification_object_shape(p_value jsonb, p_required text[], p_allowed text[]) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.verification_object_shape(p_value jsonb, p_required text[], p_allowed text[]) TO a4b_app;


--
-- Name: FUNCTION verification_request_valid(p_request jsonb); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.verification_request_valid(p_request jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.verification_request_valid(p_request jsonb) TO a4b_app;


--
-- Name: FUNCTION verification_result_valid(p_result jsonb, p_request_id text, p_mode text); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.verification_result_valid(p_result jsonb, p_request_id text, p_mode text) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.verification_result_valid(p_result jsonb, p_request_id text, p_mode text) TO a4b_app;


--
-- Name: FUNCTION verification_string_valid(p_value jsonb, p_min integer, p_max integer); Type: ACL; Schema: a4b; Owner: postgres
--

REVOKE ALL ON FUNCTION a4b.verification_string_valid(p_value jsonb, p_min integer, p_max integer) FROM PUBLIC;
GRANT ALL ON FUNCTION a4b.verification_string_valid(p_value jsonb, p_min integer, p_max integer) TO a4b_app;


--
-- Name: TABLE approval_requests; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.approval_requests TO a4b_app;
GRANT SELECT ON TABLE a4b.approval_requests TO a4b_guard_owner;


--
-- Name: TABLE chunk_locations; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.chunk_locations TO a4b_app;


--
-- Name: TABLE design_sessions; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.design_sessions TO a4b_app;


--
-- Name: TABLE document_chunks; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.document_chunks TO a4b_app;


--
-- Name: TABLE document_types; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT ON TABLE a4b.document_types TO a4b_app;
GRANT SELECT ON TABLE a4b.document_types TO a4b_guard_owner;


--
-- Name: COLUMN document_types.code; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT UPDATE(code) ON TABLE a4b.document_types TO a4b_guard_owner;


--
-- Name: TABLE document_versions; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.document_versions TO a4b_app;
GRANT SELECT ON TABLE a4b.document_versions TO a4b_guard_owner;


--
-- Name: TABLE documents; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.documents TO a4b_app;
GRANT SELECT ON TABLE a4b.documents TO a4b_guard_owner;


--
-- Name: COLUMN documents.id; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT UPDATE(id) ON TABLE a4b.documents TO a4b_guard_owner;


--
-- Name: TABLE extraction_schema_versions; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT ON TABLE a4b.extraction_schema_versions TO a4b_app;


--
-- Name: TABLE node_runs; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.node_runs TO a4b_app;
GRANT SELECT ON TABLE a4b.node_runs TO a4b_guard_owner;


--
-- Name: TABLE output_evidence; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT ON TABLE a4b.output_evidence TO a4b_app;


--
-- Name: TABLE outputs; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT ON TABLE a4b.outputs TO a4b_app;
GRANT SELECT ON TABLE a4b.outputs TO a4b_guard_owner;


--
-- Name: COLUMN outputs.id; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT UPDATE(id) ON TABLE a4b.outputs TO a4b_guard_owner;


--
-- Name: TABLE run_documents; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.run_documents TO a4b_app;
GRANT SELECT ON TABLE a4b.run_documents TO a4b_guard_owner;


--
-- Name: TABLE source_locations; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.source_locations TO a4b_app;


--
-- Name: TABLE users; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.users TO a4b_app;
GRANT SELECT ON TABLE a4b.users TO a4b_guard_owner;


--
-- Name: TABLE verification_checks; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT ON TABLE a4b.verification_checks TO a4b_app;


--
-- Name: TABLE workflow_resources; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT ON TABLE a4b.workflow_resources TO a4b_app;


--
-- Name: TABLE workflow_runs; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.workflow_runs TO a4b_app;
GRANT SELECT ON TABLE a4b.workflow_runs TO a4b_guard_owner;


--
-- Name: COLUMN workflow_runs.id; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT UPDATE(id) ON TABLE a4b.workflow_runs TO a4b_guard_owner;


--
-- Name: TABLE workflow_versions; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT ON TABLE a4b.workflow_versions TO a4b_app;
GRANT SELECT ON TABLE a4b.workflow_versions TO a4b_guard_owner;


--
-- Name: COLUMN workflow_versions.id; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT UPDATE(id) ON TABLE a4b.workflow_versions TO a4b_guard_owner;


--
-- Name: TABLE workflows; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT SELECT,INSERT,UPDATE ON TABLE a4b.workflows TO a4b_app;
GRANT SELECT ON TABLE a4b.workflows TO a4b_guard_owner;


--
-- Name: COLUMN workflows.id; Type: ACL; Schema: a4b; Owner: postgres
--

GRANT UPDATE(id) ON TABLE a4b.workflows TO a4b_guard_owner;


--
-- PostgreSQL database dump complete
--

\unrestrict tzI4RQbrgZDt22ahWIX37wtYsVRaUmM2sqtE7b0udlOe62QDDMa0bhmbZntgOED
