-- Scoped schedule bindings and run-only immutable library input snapshots.
-- Apply explicitly; the application never creates PostgreSQL schema at runtime.
CREATE TABLE a4b_builder.builder_library_sources(
 workspace_id text NOT NULL,seq bigint GENERATED ALWAYS AS IDENTITY,
 agent_id text NOT NULL,revision integer NOT NULL,json text NOT NULL,updated_at text NOT NULL,
 PRIMARY KEY(workspace_id,agent_id)
);
CREATE TABLE a4b_builder.builder_source_snapshots(
 workspace_id text NOT NULL,seq bigint GENERATED ALWAYS AS IDENTITY,
 run_id text NOT NULL,agent_id text NOT NULL,source_key text NOT NULL,json text NOT NULL,
 state text NOT NULL,updated_at text NOT NULL,PRIMARY KEY(workspace_id,run_id)
);
CREATE TABLE a4b_builder.builder_source_processed(
 workspace_id text NOT NULL,seq bigint GENERATED ALWAYS AS IDENTITY,
 agent_id text NOT NULL,revision integer NOT NULL,source_key text NOT NULL,job_id text NOT NULL,
 state text NOT NULL,updated_at text NOT NULL,PRIMARY KEY(workspace_id,agent_id,revision,source_key)
);
CREATE INDEX builder_source_processed_job ON a4b_builder.builder_source_processed(workspace_id,job_id);
DO $schedule_source_rls$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['builder_library_sources','builder_source_snapshots','builder_source_processed'] LOOP
  EXECUTE format('ALTER TABLE a4b_builder.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE a4b_builder.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY workspace_isolation ON a4b_builder.%I USING (workspace_id=current_setting(''a4b.workspace_id'',true)) WITH CHECK (workspace_id=current_setting(''a4b.workspace_id'',true))',t);
 END LOOP;
END
$schedule_source_rls$;
GRANT SELECT,INSERT,UPDATE,DELETE ON a4b_builder.builder_library_sources,a4b_builder.builder_source_snapshots,a4b_builder.builder_source_processed TO a4b_app;
GRANT USAGE ON SEQUENCE a4b_builder.builder_library_sources_seq_seq,a4b_builder.builder_source_snapshots_seq_seq,a4b_builder.builder_source_processed_seq_seq TO a4b_app;
