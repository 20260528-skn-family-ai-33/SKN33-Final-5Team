-- Central service memberships and in-app invitations. No email delivery or credentials.
CREATE TABLE a4b_builder.teams(
 id text PRIMARY KEY,name text NOT NULL,created_by text NOT NULL,created_at text NOT NULL
);
CREATE TABLE a4b_builder.team_members(
 team_id text NOT NULL REFERENCES a4b_builder.teams(id),user_id text NOT NULL,
 email text NOT NULL,name text NOT NULL,role text NOT NULL CHECK(role IN ('admin','editor','viewer')),
 joined_at text NOT NULL,PRIMARY KEY(team_id,user_id)
);
CREATE INDEX team_members_user ON a4b_builder.team_members(user_id);
CREATE TABLE a4b_builder.team_invitations(
 id text PRIMARY KEY,team_id text NOT NULL REFERENCES a4b_builder.teams(id),email text NOT NULL,
 role text NOT NULL CHECK(role IN ('admin','editor','viewer')),
 status text NOT NULL CHECK(status IN ('pending','accepted','declined','cancelled','expired')),
 invited_by text NOT NULL,inviter_name text NOT NULL,inviter_email text NOT NULL,
 created_at text NOT NULL,expires_at text NOT NULL,responded_at text
);
CREATE UNIQUE INDEX team_pending_invitation ON a4b_builder.team_invitations(team_id,email) WHERE status='pending';
CREATE INDEX team_invitations_email ON a4b_builder.team_invitations(email,status);

ALTER TABLE a4b_builder.teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE a4b_builder.teams FORCE ROW LEVEL SECURITY;
ALTER TABLE a4b_builder.team_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE a4b_builder.team_members FORCE ROW LEVEL SECURITY;
ALTER TABLE a4b_builder.team_invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE a4b_builder.team_invitations FORCE ROW LEVEL SECURITY;

-- Node first loads only its own membership/invitation, checks role, then sets the
-- authorized team for this transaction. No request body can set these settings.
CREATE POLICY team_members_actor ON a4b_builder.team_members
 USING (user_id=current_setting('a4b.user_id',true) OR team_id=current_setting('a4b.team_id',true))
 WITH CHECK (team_id=current_setting('a4b.team_id',true));
CREATE POLICY team_invitations_actor ON a4b_builder.team_invitations
 USING (email=current_setting('a4b.user_email',true) OR team_id=current_setting('a4b.team_id',true))
 WITH CHECK (team_id=current_setting('a4b.team_id',true));
CREATE POLICY teams_actor ON a4b_builder.teams
 USING (id=current_setting('a4b.team_id',true)
  OR EXISTS(SELECT 1 FROM a4b_builder.team_members m WHERE m.team_id=teams.id AND m.user_id=current_setting('a4b.user_id',true))
  OR EXISTS(SELECT 1 FROM a4b_builder.team_invitations i WHERE i.team_id=teams.id AND i.email=current_setting('a4b.user_email',true)
    AND i.status='pending' AND i.expires_at>to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')))
 WITH CHECK (id=current_setting('a4b.team_id',true));
GRANT SELECT,INSERT,UPDATE,DELETE ON a4b_builder.teams,a4b_builder.team_members,a4b_builder.team_invitations TO a4b_app;
