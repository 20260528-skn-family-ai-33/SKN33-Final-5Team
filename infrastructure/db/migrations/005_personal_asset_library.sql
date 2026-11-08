-- Logical library namespaces and folders. Original builder_assets IDs and file paths stay unchanged.
-- Personal ownership is chosen from the authenticated workspace by server code.
-- Team access additionally requires server membership checks and migration 006.
CREATE TABLE a4b_builder.library_spaces(
 workspace_id text NOT NULL,
 seq bigint GENERATED ALWAYS AS IDENTITY,
 id text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('personal','team')),
 owner_id text NOT NULL,
 name text NOT NULL,
 created_at text NOT NULL,
 PRIMARY KEY(workspace_id,id),
 UNIQUE(workspace_id,kind,owner_id)
);
CREATE TABLE a4b_builder.library_folders(
 workspace_id text NOT NULL,
 seq bigint GENERATED ALWAYS AS IDENTITY,
 id text NOT NULL,
 space_id text NOT NULL,
 parent_id text,
 parent_key text NOT NULL,
 name text NOT NULL,
 name_key text NOT NULL,
 created_at text NOT NULL,
 updated_at text NOT NULL,
 PRIMARY KEY(workspace_id,id),
 UNIQUE(workspace_id,space_id,id),
 UNIQUE(workspace_id,space_id,parent_key,name_key),
 CHECK(parent_key=COALESCE(parent_id,'')),
 FOREIGN KEY(workspace_id,space_id) REFERENCES a4b_builder.library_spaces(workspace_id,id),
 FOREIGN KEY(workspace_id,space_id,parent_id) REFERENCES a4b_builder.library_folders(workspace_id,space_id,id)
);
CREATE INDEX library_folders_parent ON a4b_builder.library_folders(workspace_id,space_id,parent_id);
CREATE TABLE a4b_builder.library_asset_placements(
 workspace_id text NOT NULL,
 seq bigint GENERATED ALWAYS AS IDENTITY,
 asset_id text NOT NULL,
 space_id text NOT NULL,
 folder_id text,
 updated_at text NOT NULL,
 PRIMARY KEY(workspace_id,asset_id),
 FOREIGN KEY(workspace_id,asset_id) REFERENCES a4b_builder.builder_assets(workspace_id,id) ON DELETE CASCADE,
 FOREIGN KEY(workspace_id,space_id) REFERENCES a4b_builder.library_spaces(workspace_id,id),
 FOREIGN KEY(workspace_id,space_id,folder_id) REFERENCES a4b_builder.library_folders(workspace_id,space_id,id)
);
CREATE INDEX library_placements_folder ON a4b_builder.library_asset_placements(workspace_id,space_id,folder_id);
DO $library_rls$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['library_spaces','library_folders','library_asset_placements'] LOOP
  EXECUTE format('ALTER TABLE a4b_builder.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE a4b_builder.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY workspace_isolation ON a4b_builder.%I USING (workspace_id=current_setting(''a4b.workspace_id'',true)) WITH CHECK (workspace_id=current_setting(''a4b.workspace_id'',true))',t);
 END LOOP;
END
$library_rls$;
GRANT SELECT,INSERT,UPDATE,DELETE ON a4b_builder.library_spaces,a4b_builder.library_folders,a4b_builder.library_asset_placements TO a4b_app;
GRANT USAGE ON SEQUENCE a4b_builder.library_spaces_seq_seq,a4b_builder.library_folders_seq_seq,a4b_builder.library_asset_placements_seq_seq TO a4b_app;
