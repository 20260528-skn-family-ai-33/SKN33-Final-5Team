-- Follow-up for installations where 009 is already applied. Preserve RPC owners/grants.
-- Store a typed timestamp instead of pretending timestamptz text casts are immutable.
ALTER TABLE a4b.workflow_generation_requests ADD COLUMN monitor_at timestamptz;
CREATE OR REPLACE FUNCTION a4b.monitor_generation_time() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 NEW.monitor_at := coalesce(nullif(NEW.json->>'finished_at','')::timestamptz,
                            nullif(NEW.json->>'started_at','')::timestamptz);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION a4b.monitor_generation_time() FROM PUBLIC;
CREATE TRIGGER monitor_generation_time BEFORE INSERT OR UPDATE ON a4b.workflow_generation_requests
 FOR EACH ROW EXECUTE FUNCTION a4b.monitor_generation_time();
-- The migration role may not bypass FORCE RLS on RDS. Use the existing scoped owner policy.
GRANT UPDATE(monitor_at) ON a4b.workflow_generation_requests TO a4b_monitor_owner;
CREATE POLICY monitor_owner_backfill ON a4b.workflow_generation_requests
 FOR UPDATE TO a4b_monitor_owner USING (true) WITH CHECK (true);
SET LOCAL ROLE a4b_monitor_owner;
UPDATE a4b.workflow_generation_requests SET monitor_at=coalesce(
 nullif(json->>'finished_at','')::timestamptz,nullif(json->>'started_at','')::timestamptz);
RESET ROLE;
DROP POLICY monitor_owner_backfill ON a4b.workflow_generation_requests;
REVOKE UPDATE(monitor_at) ON a4b.workflow_generation_requests FROM a4b_monitor_owner;
DROP INDEX a4b.generation_monitor_started;
CREATE INDEX generation_monitor_time ON a4b.workflow_generation_requests(monitor_at);
DO $$ BEGIN
 IF to_regclass('a4b.workflow_runs') IS NOT NULL THEN
  CREATE INDEX IF NOT EXISTS execution_monitor_time ON a4b.workflow_runs((coalesce(finished_at,created_at)));
 END IF;
END $$;

CREATE OR REPLACE FUNCTION a4b.monitor_records(actor uuid, since_at timestamptz, until_at timestamptz, detail_id text DEFAULT NULL)
RETURNS SETOF jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $$
BEGIN
 PERFORM a4b.monitor_require_admin(actor);
 IF until_at<=since_at OR until_at-since_at>interval '91 days' THEN
  RAISE EXCEPTION 'Invalid monitoring range' USING ERRCODE='22023';
 END IF;
 RETURN QUERY
 SELECT jsonb_build_object('kind','generation','id',g.workspace_id||':'||g.id,'request_id',g.id,'member_id',g.workspace_id,
  'status',g.json->>'status','stage',g.json->>'stage','node',g.json->>'node',
  'reason_code',g.json->>'reason_code','reason',left(g.json->>'reason',1000),'error_type',g.json->>'error_type',
  'occurred_at',g.monitor_at,'started_at',g.json->>'started_at','finished_at',g.json->>'finished_at','workflow_id',g.json->>'workflow_id',
  'diagnostics',g.json->'diagnostics','events',CASE WHEN detail_id IS NULL THEN '[]'::jsonb ELSE
   coalesce((SELECT jsonb_agg(jsonb_build_object('at',e->>'at','stage',e->>'stage','node',e->>'node','status',e->>'status','reason_code',e->>'reason_code'))
    FROM (SELECT value e FROM jsonb_array_elements(coalesce(g.json->'events','[]'::json)::jsonb) LIMIT 100) ev),'[]'::jsonb) END)
 FROM a4b.workflow_generation_requests g
 WHERE g.monitor_at>=since_at AND g.monitor_at<until_at
 AND (detail_id IS NULL OR g.workspace_id||':'||g.id=detail_id);
 -- Business-record tables exist in production; generic builder installations may omit them.
 IF to_regclass('a4b.workflow_runs') IS NOT NULL THEN
 RETURN QUERY
 SELECT jsonb_build_object('kind','execution','id',r.id::text,'member_id',r.requested_by::text,
  'status',r.status,'stage','execution','node',n.node_key,'reason_code',coalesce(r.state_json->>'error_type','execution_failed'),
  'reason',left(r.state_json->>'error',1000),'error_type',r.state_json->>'error_type','started_at',r.created_at,
  'occurred_at',coalesce(r.finished_at,r.created_at),'finished_at',r.finished_at,'workflow_id',v.workflow_id,'diagnostics',r.state_json->'diagnostics',
  'events',CASE WHEN detail_id IS NULL THEN '[]'::jsonb ELSE coalesce((SELECT jsonb_agg(jsonb_build_object(
   'at',nr.finished_at,'node',nr.node_key,'status',nr.status,'attempt',nr.attempt_no))
   FROM (SELECT * FROM a4b.node_runs WHERE run_id=r.id ORDER BY started_at,attempt_no LIMIT 100) nr),'[]'::jsonb) END)
 FROM a4b.workflow_runs r JOIN a4b.workflow_versions v ON v.id=r.workflow_version_id
 LEFT JOIN LATERAL (SELECT node_key FROM a4b.node_runs WHERE run_id=r.id AND status='failed' ORDER BY finished_at DESC LIMIT 1) n ON true
 WHERE coalesce(r.finished_at,r.created_at)>=since_at AND coalesce(r.finished_at,r.created_at)<until_at AND (detail_id IS NULL OR r.id::text=detail_id);
 END IF;
 IF to_regclass('a4b.document_versions') IS NOT NULL THEN
 RETURN QUERY
 SELECT jsonb_build_object('kind','document','id',dv.id::text,'member_id',d.created_by::text,
  'status',dv.status,'stage','document_processing','node','parser','reason_code',coalesce(dv.error_json->>'code',dv.parser_metadata_json#>>'{processing,error,code}'),
  'reason',left(coalesce(dv.error_json->>'message',dv.parser_metadata_json#>>'{processing,error,message}'),1000),'error_type',NULL,'started_at',dv.created_at,
  'finished_at',NULL,'workflow_id',NULL,'diagnostics',NULL,'events','[]'::jsonb)
 FROM a4b.document_versions dv JOIN a4b.documents d ON d.id=dv.document_id
 WHERE dv.created_at>=since_at AND dv.created_at<until_at AND (detail_id IS NULL OR dv.id::text=detail_id);
 END IF;
END $$;

CREATE OR REPLACE FUNCTION a4b.monitor_triage_save(actor uuid,k text,rid text,s text,n text,expected integer)
RETURNS a4b.monitor_triage LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $$
DECLARE saved a4b.monitor_triage; record_exists boolean := false;
BEGIN
 PERFORM a4b.monitor_require_admin(actor);
 IF k NOT IN ('generation','execution','document') OR s NOT IN ('unreviewed','investigating','resolved') OR n IS NULL OR length(n)>2000 OR expected<0 THEN
  RAISE EXCEPTION 'Invalid triage' USING ERRCODE='22023';
 END IF;
 IF k='generation' THEN
  SELECT EXISTS(SELECT 1 FROM a4b.workflow_generation_requests WHERE workspace_id||':'||id=rid) INTO record_exists;
 ELSIF k='execution' AND to_regclass('a4b.workflow_runs') IS NOT NULL THEN
  SELECT EXISTS(SELECT 1 FROM a4b.workflow_runs WHERE id::text=rid) INTO record_exists;
 ELSIF k='document' AND to_regclass('a4b.document_versions') IS NOT NULL THEN
  SELECT EXISTS(SELECT 1 FROM a4b.document_versions WHERE id::text=rid) INTO record_exists;
 END IF;
 IF NOT record_exists THEN
  RAISE EXCEPTION 'Record not found' USING ERRCODE='P0002';
 END IF;
 INSERT INTO a4b.monitor_triage AS t(kind,record_id,status,note,updated_by)
 SELECT k,rid,s,n,actor WHERE expected=0
 ON CONFLICT DO NOTHING RETURNING * INTO saved;
 IF saved IS NULL THEN
  UPDATE a4b.monitor_triage SET status=s,note=n,revision=revision+1,updated_by=actor,updated_at=now()
   WHERE kind=k AND record_id=rid AND revision=expected RETURNING * INTO saved;
 END IF;
 IF saved IS NULL THEN RAISE EXCEPTION 'Triage changed' USING ERRCODE='40001'; END IF;
 INSERT INTO a4b.monitor_triage_events(kind,record_id,status,note,revision,actor_id)
 VALUES(k,rid,s,n,saved.revision,actor);
 RETURN saved;
END $$;
