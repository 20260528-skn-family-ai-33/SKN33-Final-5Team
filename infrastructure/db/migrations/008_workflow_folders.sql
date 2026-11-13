-- Organization never changes saved workflow versions or reservation bindings.
CREATE TABLE a4b_builder.workflow_organization(
 workspace_id text NOT NULL,seq bigint GENERATED ALWAYS AS IDENTITY,
 id text NOT NULL,revision integer NOT NULL,json text NOT NULL,
 PRIMARY KEY(workspace_id,id)
);
ALTER TABLE a4b_builder.workflow_organization ENABLE ROW LEVEL SECURITY;
ALTER TABLE a4b_builder.workflow_organization FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON a4b_builder.workflow_organization
 USING (workspace_id=current_setting('a4b.workspace_id',true))
 WITH CHECK (workspace_id=current_setting('a4b.workspace_id',true));
GRANT SELECT,INSERT,UPDATE,DELETE ON a4b_builder.workflow_organization TO a4b_app;
GRANT USAGE ON SEQUENCE a4b_builder.workflow_organization_seq_seq TO a4b_app;
