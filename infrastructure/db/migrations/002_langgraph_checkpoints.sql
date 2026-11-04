-- 002: Python LangGraph 체크포인트(PostgresSaver)의 작업 공간 격리
-- 적용 스크립트가 이 파일 직전에 PostgresSaver.setup()을 search_path=a4b_builder로 실행해
-- checkpoint_migrations·checkpoints·checkpoint_blobs·checkpoint_writes를 만든다.
-- 라이브러리 SQL은 고칠 수 없으므로 workspace_id는 연결의 a4b.workspace_id 값을 기본값으로 채우고,
-- 행 수준 보안이 조회·수정을 그 작업 공간으로 제한한다. 설정이 없으면 NOT NULL 위반으로 실패한다.
-- 세션·실행 ID(thread_id)는 UUID라 작업 공간끼리 기본 키가 겹치지 않는다.

DO $a4b_builder_checkpoints$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['checkpoints','checkpoint_blobs','checkpoint_writes']
    LOOP
        EXECUTE format('ALTER TABLE a4b_builder.%I ADD COLUMN workspace_id text NOT NULL DEFAULT current_setting(''a4b.workspace_id'', true)', t);
        EXECUTE format('CREATE INDEX %I ON a4b_builder.%I(workspace_id,thread_id)', t||'_workspace_thread', t);
        EXECUTE format('ALTER TABLE a4b_builder.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE a4b_builder.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY workspace_isolation ON a4b_builder.%I USING (workspace_id = current_setting(''a4b.workspace_id'', true)) WITH CHECK (workspace_id = current_setting(''a4b.workspace_id'', true))', t);
    END LOOP;
END
$a4b_builder_checkpoints$;

GRANT SELECT,INSERT,UPDATE,DELETE ON a4b_builder.checkpoints,a4b_builder.checkpoint_blobs,a4b_builder.checkpoint_writes TO a4b_app;
GRANT SELECT ON a4b_builder.checkpoint_migrations TO a4b_app;
