-- Admin-only member identity and stored request context. No new snapshots or raw JSON API.
CREATE FUNCTION a4b.monitor_member_emails(actor uuid, member_ids text[])
RETURNS TABLE(member_id text, email text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $$
BEGIN
 PERFORM a4b.monitor_require_admin(actor);
 IF coalesce(cardinality(member_ids),0)>25 THEN
  RAISE EXCEPTION 'Too many members' USING ERRCODE='22023';
 END IF;
 RETURN QUERY SELECT u.id::text,u.email::text FROM a4b.users u WHERE u.id::text=ANY(member_ids);
END $$;

CREATE FUNCTION a4b.monitor_request_context(actor uuid, k text, rid text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $$
DECLARE
 member text; session_key text; workflow_key text; request_text text;
 messages jsonb := '[]'::jsonb; turns jsonb := '[]'::jsonb;
 turn_count integer := 0; source text := 'unavailable';
 storage_limit integer := NULL; storage_truncated boolean := NULL;
BEGIN
 PERFORM a4b.monitor_require_admin(actor);
 IF k='generation' THEN
  SELECT g.workspace_id,g.session_id,g.json->>'request_text',
    CASE WHEN g.json->>'request_text_truncated' IN ('true','false')
     THEN (g.json->>'request_text_truncated')::boolean ELSE NULL END
   INTO member,session_key,request_text,storage_truncated FROM a4b.workflow_generation_requests g
   WHERE g.workspace_id||':'||g.id=rid;
  storage_limit := 4000;
 ELSIF k='execution' AND to_regclass('a4b.workflow_runs') IS NOT NULL THEN
  SELECT r.requested_by::text,r.input_json->>'request',v.workflow_id::text
   INTO member,request_text,workflow_key
   FROM a4b.workflow_runs r JOIN a4b.workflow_versions v ON v.id=r.workflow_version_id
   WHERE r.id::text=rid;
  -- Resolve the exact executed version, never the most recent workflow conversation.
  IF member IS NOT NULL AND to_regclass('a4b_builder.agent_versions') IS NOT NULL THEN
   SELECT av.json->>'session_id' INTO session_key
    FROM a4b.workflow_runs r JOIN a4b.workflow_versions v ON v.id=r.workflow_version_id
    JOIN a4b_builder.agent_versions av ON av.workspace_id=r.requested_by::text
     AND av.agent_id=v.workflow_id::text AND av.version=v.version_no
    WHERE r.id::text=rid;
  END IF;
 ELSIF k='document' THEN
  RETURN jsonb_build_object('prompt',NULL,'conversation','[]'::jsonb,'conversation_source','unavailable',
   'conversation_truncated',false,'prompt_truncated',false);
 ELSE
  RAISE EXCEPTION 'Invalid context kind' USING ERRCODE='22023';
 END IF;
 IF member IS NULL THEN RAISE EXCEPTION 'Record not found' USING ERRCODE='P0002'; END IF;
 IF session_key IS NOT NULL AND to_regclass('a4b.design_sessions') IS NOT NULL THEN
  SELECT ds.messages_json INTO messages FROM a4b.design_sessions ds
   WHERE ds.id::text=session_key AND ds.created_by::text=member
    AND (workflow_key IS NULL OR ds.workflow_id::text=workflow_key);
  IF FOUND THEN source := 'session_latest'; END IF;
 END IF;
 -- Project only user/assistant text, never tool/system messages, attachments or metadata.
 WITH valid AS (
  SELECT e.value,e.ordinality FROM jsonb_array_elements(
   CASE WHEN jsonb_typeof(messages)='array' THEN messages ELSE '[]'::jsonb END)
   WITH ORDINALITY e WHERE e.value->>'role' IN ('user','assistant')
    AND jsonb_typeof(e.value->'content')='string'
 ), recent AS (SELECT * FROM valid ORDER BY ordinality DESC LIMIT 40)
 SELECT (SELECT count(*)::integer FROM valid),coalesce(jsonb_agg(jsonb_build_object(
  'role',value->>'role','content',left(value->>'content',8001)) ORDER BY ordinality),'[]'::jsonb)
 INTO turn_count,turns FROM recent;
 RETURN jsonb_build_object('prompt',left(request_text,8001),'prompt_truncated',coalesce(storage_truncated,false) OR coalesce(length(request_text)>8000,false),
  'prompt_storage_limit',storage_limit,'prompt_storage_truncated',storage_truncated,
  'conversation',turns,'conversation_source',source,'conversation_truncated',turn_count>40);
END $$;

-- Preserve the application's existing workspace policy; only the guarded function owner
-- can resolve a version's session mapping across workspaces.
DO $$ BEGIN
 IF to_regclass('a4b.design_sessions') IS NOT NULL THEN
  GRANT SELECT(id,created_by,workflow_id,messages_json) ON a4b.design_sessions TO a4b_monitor_owner;
 END IF;
 IF to_regclass('a4b_builder.agent_versions') IS NOT NULL THEN
  GRANT USAGE ON SCHEMA a4b_builder TO a4b_monitor_owner;
  GRANT SELECT(workspace_id,agent_id,version,json) ON a4b_builder.agent_versions TO a4b_monitor_owner;
  CREATE POLICY monitor_context_version_read ON a4b_builder.agent_versions
   FOR SELECT TO a4b_monitor_owner USING(true);
 END IF;
END $$;
REVOKE ALL ON FUNCTION a4b.monitor_member_emails(uuid,text[]),a4b.monitor_request_context(uuid,text,text) FROM PUBLIC;
GRANT CREATE ON SCHEMA a4b TO a4b_monitor_owner;
ALTER FUNCTION a4b.monitor_member_emails(uuid,text[]) OWNER TO a4b_monitor_owner;
ALTER FUNCTION a4b.monitor_request_context(uuid,text,text) OWNER TO a4b_monitor_owner;
REVOKE CREATE ON SCHEMA a4b FROM a4b_monitor_owner;
GRANT EXECUTE ON FUNCTION a4b.monitor_member_emails(uuid,text[]),a4b.monitor_request_context(uuid,text,text) TO a4b_app;
