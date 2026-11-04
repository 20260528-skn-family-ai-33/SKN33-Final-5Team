-- A receipt is issued only after the entire source volume has been imported.
CREATE TABLE a4b_builder.sqlite_imports (
    id uuid PRIMARY KEY,
    workspace_count integer NOT NULL CHECK (workspace_count >= 0),
    completed_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT ON a4b_builder.sqlite_imports TO a4b_app;
