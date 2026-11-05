-- Generation attempts are separate from execution runs and from dialogue checkpoints.
-- Generic PostgreSQL installations may not yet use the other a4b business tables.
CREATE SCHEMA IF NOT EXISTS a4b;
GRANT USAGE ON SCHEMA a4b TO a4b_app;
CREATE TABLE a4b.workflow_generation_requests (
    workspace_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    id text NOT NULL,
    session_id text NOT NULL,
    json json NOT NULL,
    PRIMARY KEY (workspace_id, id),
    CONSTRAINT generation_request_identity CHECK (COALESCE(json->>'request_id'=id AND json->>'session_id'=session_id,false)),
    CONSTRAINT generation_request_status CHECK (COALESCE(json->>'status' IN
        ('running','waiting_input','waiting_confirmation','blocked','failed','succeeded'),false)),
    CONSTRAINT generation_request_stage CHECK (COALESCE(json->>'stage' IN ('request_analysis','design','validation','save'),false))
);
CREATE INDEX generation_requests_session ON a4b.workflow_generation_requests(workspace_id,session_id,seq DESC);
ALTER TABLE a4b.workflow_generation_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE a4b.workflow_generation_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON a4b.workflow_generation_requests
    USING (workspace_id=current_setting('a4b.workspace_id',true))
    WITH CHECK (workspace_id=current_setting('a4b.workspace_id',true));
GRANT SELECT,INSERT,UPDATE,DELETE ON a4b.workflow_generation_requests TO a4b_app;
GRANT USAGE ON SEQUENCE a4b.workflow_generation_requests_seq_seq TO a4b_app;
