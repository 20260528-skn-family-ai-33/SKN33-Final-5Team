-- 001: 에이전트 빌더 업무 데이터 (A4B_STORAGE=postgres)
-- 앱 시작 시 자동 실행하지 않는다. scripts/apply-builder-migrations.py로 명시적으로 적용한다.
-- 적용 계정: 스키마 생성 권한이 있는 관리자. 앱은 a4b_app 역할의 멤버인 일반 계정으로 접속한다.
--
-- SQLite의 작업 공간 폴더(data/users/<회원ID>/agent-builder.sqlite) 하나가 workspace_id 하나다.
-- 모든 기본 키·고유 키·외래 키·인덱스는 workspace_id로 시작한다.
-- 앱 코드가 모든 SQL에 workspace_id 조건을 넣고, FORCE ROW LEVEL SECURITY가 한 번 더 막는다.
-- 정책은 연결마다 set_config('a4b.workspace_id', ...)로 정한 값과 같은 행만 보이고 쓸 수 있게 한다.
-- 설정이 없으면 current_setting(..., true)이 NULL이라 아무 행도 보이지 않는다(fail closed).
--
-- JSON 원문 열은 json 타입이다. jsonb는 키 순서를 바꾸는데, 표의 열 순서와 검증 지문이
-- 저장된 키 순서에 의존하므로 원문 텍스트를 그대로 보존해야 한다(->> 연산은 json에서도 된다).
-- 시각 열은 SQLite와 같은 ISO-8601 TEXT다. lease·예약 비교가 같은 문자열 비교로 동작한다.
-- seq는 SQLite rowid를 대신하는 삽입 순서 열이다.

CREATE SCHEMA IF NOT EXISTS a4b_builder;

CREATE TABLE a4b_builder.schema_migrations(
    version integer PRIMARY KEY,
    name text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE a4b_builder.registered_tools(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    id text NOT NULL,
    version integer NOT NULL,
    module_path text NOT NULL,
    schema_json json NOT NULL,
    PRIMARY KEY(workspace_id,id,version)
);

CREATE TABLE a4b_builder.builder_sessions(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    id text NOT NULL,
    json json NOT NULL,
    revision integer NOT NULL DEFAULT 0,
    PRIMARY KEY(workspace_id,id)
);

CREATE TABLE a4b_builder.builder_assets(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    id text NOT NULL,
    json json NOT NULL,
    rows_json json NOT NULL,
    PRIMARY KEY(workspace_id,id)
);

CREATE TABLE a4b_builder.agent_versions(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    agent_id text NOT NULL,
    version integer NOT NULL,
    json json NOT NULL,
    PRIMARY KEY(workspace_id,agent_id,version)
);
CREATE INDEX agent_versions_order ON a4b_builder.agent_versions(workspace_id,seq);

CREATE TABLE a4b_builder.agent_tests(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    id text NOT NULL,
    agent_id text NOT NULL,
    json json NOT NULL,
    PRIMARY KEY(workspace_id,id)
);
CREATE INDEX agent_tests_agent ON a4b_builder.agent_tests(workspace_id,agent_id,seq);

CREATE TABLE a4b_builder.builder_run_leases(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    run_id text NOT NULL,
    token text NOT NULL,
    expires_at text NOT NULL,
    pid integer NOT NULL,
    active integer NOT NULL,
    PRIMARY KEY(workspace_id,run_id)
);

CREATE TABLE a4b_builder.builder_data_sources(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    agent_id text NOT NULL,
    id text NOT NULL,
    asset_id text NOT NULL,
    version integer NOT NULL,
    updated_at text NOT NULL,
    PRIMARY KEY(workspace_id,agent_id)
);

CREATE TABLE a4b_builder.builder_schedules(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    agent_id text NOT NULL,
    json json NOT NULL,
    next_at text,
    active integer NOT NULL DEFAULT 0,
    PRIMARY KEY(workspace_id,agent_id)
);
CREATE INDEX builder_schedules_due ON a4b_builder.builder_schedules(workspace_id,active,next_at);

CREATE TABLE a4b_builder.builder_schedule_jobs(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    id text NOT NULL,
    agent_id text NOT NULL,
    slot_key text NOT NULL,
    state text NOT NULL,
    lease_until text,
    lease_token text,
    json json NOT NULL,
    PRIMARY KEY(workspace_id,id),
    UNIQUE(workspace_id,agent_id,slot_key)
);
CREATE INDEX builder_schedule_jobs_claim ON a4b_builder.builder_schedule_jobs(workspace_id,state,seq);
CREATE INDEX builder_schedule_jobs_agent ON a4b_builder.builder_schedule_jobs(workspace_id,agent_id,seq);

CREATE TABLE a4b_builder.reply_review_decisions(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    run_id text NOT NULL,
    review_id text NOT NULL,
    json json NOT NULL,
    PRIMARY KEY(workspace_id,run_id,review_id),
    FOREIGN KEY(workspace_id,run_id) REFERENCES a4b_builder.agent_tests(workspace_id,id)
);

CREATE TABLE a4b_builder.document_reviews(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    asset_id text NOT NULL,
    revision integer NOT NULL,
    json json NOT NULL,
    PRIMARY KEY(workspace_id,asset_id),
    FOREIGN KEY(workspace_id,asset_id) REFERENCES a4b_builder.builder_assets(workspace_id,id)
);

CREATE TABLE a4b_builder.document_review_events(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    asset_id text NOT NULL,
    revision integer NOT NULL,
    request_id text NOT NULL,
    json json NOT NULL,
    PRIMARY KEY(workspace_id,asset_id,revision),
    UNIQUE(workspace_id,asset_id,request_id),
    FOREIGN KEY(workspace_id,asset_id) REFERENCES a4b_builder.builder_assets(workspace_id,id)
);

CREATE TABLE a4b_builder.builder_tool_gap_jobs(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    id text NOT NULL,
    session_id text NOT NULL,
    decision_id text NOT NULL,
    input_sha256 text NOT NULL,
    json json NOT NULL,
    PRIMARY KEY(workspace_id,id),
    UNIQUE(workspace_id,decision_id)
);
CREATE INDEX builder_tool_gap_jobs_session ON a4b_builder.builder_tool_gap_jobs(workspace_id,session_id);

CREATE TABLE a4b_builder.reusable_nodes(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    id text NOT NULL,
    version integer NOT NULL,
    json json NOT NULL,
    PRIMARY KEY(workspace_id,id,version)
);

CREATE TABLE a4b_builder.builder_verifications(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    id text NOT NULL,
    session_id text NOT NULL,
    input_json json,
    result_json json NOT NULL,
    PRIMARY KEY(workspace_id,id)
);

CREATE TABLE a4b_builder.preprocessing_batches(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    id text NOT NULL,
    source_path text NOT NULL,
    contract_version text NOT NULL,
    provenance text NOT NULL,
    human_review text NOT NULL,
    summary_json json NOT NULL,
    PRIMARY KEY(workspace_id,id)
);

CREATE TABLE a4b_builder.preprocessing_examples(
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    batch_id text NOT NULL,
    example_id text NOT NULL,
    scenario_group text NOT NULL,
    split text NOT NULL,
    content_hash text NOT NULL,
    raw_json json NOT NULL,
    clean_json json NOT NULL,
    target_json json NOT NULL,
    adapted_json json NOT NULL,
    compatibility text NOT NULL,
    issues_json json NOT NULL,
    PRIMARY KEY(workspace_id,batch_id,example_id),
    UNIQUE(workspace_id,batch_id,content_hash),
    FOREIGN KEY(workspace_id,batch_id) REFERENCES a4b_builder.preprocessing_batches(workspace_id,id)
);
CREATE INDEX preprocessing_split ON a4b_builder.preprocessing_examples(workspace_id,batch_id,split,compatibility);

-- Node 어시스턴트 런타임의 LangGraph 체크포인트(Backend/api/src/graph-checkpointer.ts).
CREATE TABLE a4b_builder.agent_checkpoints(
    workspace_id text NOT NULL,
    thread_id text NOT NULL,
    payload bytea NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(workspace_id,thread_id)
);

-- 작업 공간 행 수준 보안. FORCE는 테이블 소유자에게도 정책을 적용한다.
DO $a4b_builder_rls$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['registered_tools','builder_sessions','builder_assets','agent_versions','agent_tests',
        'builder_run_leases','builder_data_sources','builder_schedules','builder_schedule_jobs','reply_review_decisions',
        'document_reviews','document_review_events','builder_tool_gap_jobs','reusable_nodes','builder_verifications',
        'preprocessing_batches','preprocessing_examples','agent_checkpoints']
    LOOP
        EXECUTE format('ALTER TABLE a4b_builder.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE a4b_builder.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY workspace_isolation ON a4b_builder.%I USING (workspace_id = current_setting(''a4b.workspace_id'', true)) WITH CHECK (workspace_id = current_setting(''a4b.workspace_id'', true))', t);
    END LOOP;
END
$a4b_builder_rls$;

-- 앱 역할 권한. DDL·TRUNCATE·소유권은 주지 않는다.
REVOKE ALL ON SCHEMA a4b_builder FROM PUBLIC;
GRANT USAGE ON SCHEMA a4b_builder TO a4b_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA a4b_builder TO a4b_app;
REVOKE INSERT,UPDATE,DELETE ON a4b_builder.schema_migrations FROM a4b_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA a4b_builder TO a4b_app;
