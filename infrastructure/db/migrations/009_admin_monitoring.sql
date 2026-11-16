-- Admin-only RPCs expose diagnostic projections, never request bodies or file contents.
-- RDS administrators need not have BYPASSRLS. Use a dedicated NOLOGIN function
-- owner with explicit role-scoped policies; never grant this role to the app.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='a4b_monitor_owner') THEN
  CREATE ROLE a4b_monitor_owner NOLOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
 IF pg_has_role('a4b_app','a4b_monitor_owner','MEMBER') THEN
  RAISE EXCEPTION 'Monitoring owner must not be granted to the app';
 END IF;
 EXECUTE format('GRANT a4b_monitor_owner TO %I',current_user);
END $$;
GRANT USAGE,CREATE ON SCHEMA a4b TO a4b_monitor_owner;
CREATE TABLE a4b.monitor_triage (
 kind text NOT NULL CHECK(kind IN ('generation','execution','document')),
 record_id text NOT NULL,
 status text NOT NULL CHECK(status IN ('unreviewed','investigating','resolved')),
 note text NOT NULL CHECK(length(note)<=2000),
 revision integer NOT NULL DEFAULT 1,
 updated_by uuid NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(kind,record_id)
);
CREATE TABLE a4b.monitor_triage_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 kind text NOT NULL,record_id text NOT NULL,status text NOT NULL,note text NOT NULL,
 revision integer NOT NULL,actor_id uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE a4b.monitor_triage ENABLE ROW LEVEL SECURITY;
ALTER TABLE a4b.monitor_triage FORCE ROW LEVEL SECURITY;
ALTER TABLE a4b.monitor_triage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE a4b.monitor_triage_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON a4b.monitor_triage,a4b.monitor_triage_events FROM PUBLIC,a4b_app;
CREATE INDEX monitor_triage_events_record ON a4b.monitor_triage_events(kind,record_id,id DESC);
CREATE INDEX generation_monitor_started ON a4b.workflow_generation_requests((json->>'started_at'));

CREATE FUNCTION a4b.monitor_require_admin(actor uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM a4b.users WHERE id=actor AND role='admin' AND status='active') THEN
  RAISE EXCEPTION 'Administrator required' USING ERRCODE='42501';
 END IF;
END $$;

CREATE FUNCTION a4b.monitor_records(actor uuid, since_at timestamptz, until_at timestamptz, detail_id text DEFAULT NULL)
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
  'started_at',g.json->>'started_at','finished_at',g.json->>'finished_at','workflow_id',g.json->>'workflow_id',
  'diagnostics',g.json->'diagnostics','events',CASE WHEN detail_id IS NULL THEN '[]'::jsonb ELSE
   coalesce((SELECT jsonb_agg(jsonb_build_object('at',e->>'at','stage',e->>'stage','node',e->>'node','status',e->>'status','reason_code',e->>'reason_code'))
    FROM (SELECT value e FROM jsonb_array_elements(coalesce(g.json->'events','[]'::json)::jsonb) LIMIT 100) ev),'[]'::jsonb) END)
 FROM a4b.workflow_generation_requests g
 WHERE (g.json->>'started_at')::timestamptz>=since_at AND (g.json->>'started_at')::timestamptz<until_at
 AND (detail_id IS NULL OR g.workspace_id||':'||g.id=detail_id);
 -- Business-record tables exist in production; generic builder installations may omit them.
 IF to_regclass('a4b.workflow_runs') IS NOT NULL THEN
 RETURN QUERY
 SELECT jsonb_build_object('kind','execution','id',r.id::text,'member_id',r.requested_by::text,
  'status',r.status,'stage','execution','node',n.node_key,'reason_code',coalesce(r.state_json->>'error_type','execution_failed'),
  'reason',left(r.state_json->>'error',1000),'error_type',r.state_json->>'error_type','started_at',r.created_at,
  'finished_at',r.finished_at,'workflow_id',v.workflow_id,'diagnostics',r.state_json->'diagnostics',
  'events',CASE WHEN detail_id IS NULL THEN '[]'::jsonb ELSE coalesce((SELECT jsonb_agg(jsonb_build_object(
   'at',nr.finished_at,'node',nr.node_key,'status',nr.status,'attempt',nr.attempt_no))
   FROM (SELECT * FROM a4b.node_runs WHERE run_id=r.id ORDER BY started_at,attempt_no LIMIT 100) nr),'[]'::jsonb) END)
 FROM a4b.workflow_runs r JOIN a4b.workflow_versions v ON v.id=r.workflow_version_id
 LEFT JOIN LATERAL (SELECT node_key FROM a4b.node_runs WHERE run_id=r.id AND status='failed' ORDER BY finished_at DESC LIMIT 1) n ON true
 WHERE r.created_at>=since_at AND r.created_at<until_at AND (detail_id IS NULL OR r.id::text=detail_id);
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

CREATE FUNCTION a4b.monitor_triage_read(actor uuid) RETURNS SETOF a4b.monitor_triage
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $$
BEGIN PERFORM a4b.monitor_require_admin(actor); RETURN QUERY SELECT * FROM a4b.monitor_triage; END $$;
CREATE FUNCTION a4b.monitor_triage_history(actor uuid,k text,rid text) RETURNS SETOF a4b.monitor_triage_events
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $$
BEGIN PERFORM a4b.monitor_require_admin(actor);
 RETURN QUERY SELECT * FROM a4b.monitor_triage_events WHERE kind=k AND record_id=rid ORDER BY id DESC LIMIT 30;
END $$;
CREATE FUNCTION a4b.monitor_triage_save(actor uuid,k text,rid text,s text,n text,expected integer)
RETURNS a4b.monitor_triage LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $$
DECLARE saved a4b.monitor_triage;
BEGIN
 PERFORM a4b.monitor_require_admin(actor);
 IF k NOT IN ('generation','execution','document') OR s NOT IN ('unreviewed','investigating','resolved') OR n IS NULL OR length(n)>2000 OR expected<0 THEN
  RAISE EXCEPTION 'Invalid triage' USING ERRCODE='22023';
 END IF;
 IF NOT ((k='generation' AND EXISTS(SELECT 1 FROM a4b.workflow_generation_requests WHERE workspace_id||':'||id=rid))
  OR (k='execution' AND EXISTS(SELECT 1 FROM a4b.workflow_runs WHERE id::text=rid))
  OR (k='document' AND EXISTS(SELECT 1 FROM a4b.document_versions WHERE id::text=rid))) THEN
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
REVOKE ALL ON FUNCTION a4b.monitor_require_admin(uuid),a4b.monitor_records(uuid,timestamptz,timestamptz,text),
 a4b.monitor_triage_read(uuid),a4b.monitor_triage_history(uuid,text,text),a4b.monitor_triage_save(uuid,text,text,text,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION a4b.monitor_records(uuid,timestamptz,timestamptz,text),a4b.monitor_triage_read(uuid),
 a4b.monitor_triage_history(uuid,text,text),a4b.monitor_triage_save(uuid,text,text,text,text,integer) TO a4b_app;

-- The role is reachable only through the guarded RPCs, not ordinary app SQL.
GRANT SELECT ON a4b.workflow_generation_requests TO a4b_monitor_owner;
CREATE POLICY monitor_owner_read ON a4b.workflow_generation_requests
 FOR SELECT TO a4b_monitor_owner USING (true);
GRANT SELECT,INSERT,UPDATE ON a4b.monitor_triage TO a4b_monitor_owner;
GRANT SELECT,INSERT ON a4b.monitor_triage_events TO a4b_monitor_owner;
GRANT USAGE ON SEQUENCE a4b.monitor_triage_events_id_seq TO a4b_monitor_owner;
CREATE POLICY monitor_owner_access ON a4b.monitor_triage TO a4b_monitor_owner USING (true) WITH CHECK (true);
CREATE POLICY monitor_owner_access ON a4b.monitor_triage_events TO a4b_monitor_owner USING (true) WITH CHECK (true);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['users','workflow_runs','workflow_versions','node_runs','document_versions','documents'] LOOP
  IF to_regclass('a4b.'||t) IS NOT NULL THEN
   EXECUTE format('GRANT SELECT ON a4b.%I TO a4b_monitor_owner',t);
  END IF;
 END LOOP;
END $$;
ALTER FUNCTION a4b.monitor_require_admin(uuid) OWNER TO a4b_monitor_owner;
ALTER FUNCTION a4b.monitor_records(uuid,timestamptz,timestamptz,text) OWNER TO a4b_monitor_owner;
ALTER FUNCTION a4b.monitor_triage_read(uuid) OWNER TO a4b_monitor_owner;
ALTER FUNCTION a4b.monitor_triage_history(uuid,text,text) OWNER TO a4b_monitor_owner;
ALTER FUNCTION a4b.monitor_triage_save(uuid,text,text,text,text,integer) OWNER TO a4b_monitor_owner;
REVOKE CREATE ON SCHEMA a4b FROM a4b_monitor_owner;
