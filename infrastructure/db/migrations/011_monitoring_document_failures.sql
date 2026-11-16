-- Separate observation table preserves immutable document-version snapshots.
CREATE TABLE a4b.monitor_document_failures(document_version_id uuid PRIMARY KEY,failed_at timestamptz NOT NULL);
ALTER TABLE a4b.monitor_document_failures ENABLE ROW LEVEL SECURITY;
ALTER TABLE a4b.monitor_document_failures FORCE ROW LEVEL SECURITY;
REVOKE ALL ON a4b.monitor_document_failures FROM PUBLIC,a4b_app;
GRANT SELECT,INSERT ON a4b.monitor_document_failures TO a4b_monitor_owner;
CREATE POLICY monitor_document_owner ON a4b.monitor_document_failures TO a4b_monitor_owner USING(true) WITH CHECK(true);
CREATE FUNCTION a4b.monitor_document_failure() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $$
BEGIN
 IF NEW.status='failed' THEN
  IF TG_OP='INSERT' OR OLD.status IS DISTINCT FROM 'failed' THEN
   INSERT INTO a4b.monitor_document_failures VALUES(NEW.id,clock_timestamp()) ON CONFLICT DO NOTHING;
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION a4b.monitor_document_failure() FROM PUBLIC;
GRANT CREATE ON SCHEMA a4b TO a4b_monitor_owner;
ALTER FUNCTION a4b.monitor_document_failure() OWNER TO a4b_monitor_owner;
REVOKE CREATE ON SCHEMA a4b FROM a4b_monitor_owner;
DO $$ BEGIN
 IF to_regclass('a4b.document_versions') IS NOT NULL THEN
  CREATE TRIGGER monitor_document_failure AFTER INSERT OR UPDATE OF status ON a4b.document_versions
   FOR EACH ROW EXECUTE FUNCTION a4b.monitor_document_failure();
 END IF;
END $$;
-- Historical failure times were not recorded: retain created_at fallback, never invent a failure time.
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
  'occurred_at',coalesce(f.failed_at,dv.created_at),'finished_at',f.failed_at,'workflow_id',NULL,'diagnostics',NULL,'events','[]'::jsonb)
 FROM a4b.document_versions dv JOIN a4b.documents d ON d.id=dv.document_id
 LEFT JOIN a4b.monitor_document_failures f ON f.document_version_id=dv.id
 WHERE coalesce(f.failed_at,dv.created_at)>=since_at AND coalesce(f.failed_at,dv.created_at)<until_at AND (detail_id IS NULL OR dv.id::text=detail_id);
 END IF;
END $$;

