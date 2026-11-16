-- Enterprise identity: SSO (OIDC), SCIM provisioning, groups. SSO and SCIM settings live in app_settings
-- (keys 'sso' and 'scim'); the SSO client secret is encrypted there, the SCIM token is stored as a SHA-256.

-- Deactivated users (SCIM active=false) can't sign in; current_user rejects them.
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS disabled boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS display_name text,
  ADD COLUMN IF NOT EXISTS external_id text;   -- the identity provider's id for this user (SCIM externalId)

-- Groups: access lists may contain 'group:<lowercased name>' next to emails.
CREATE TABLE IF NOT EXISTS groups (
  id serial PRIMARY KEY,
  name text NOT NULL,
  external_id text,                            -- set when the group is managed by SCIM
  created_at timestamptz DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS groups_name_idx ON groups (lower(name));

CREATE TABLE IF NOT EXISTS group_members (
  group_id int NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  user_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, user_id)
);
CREATE INDEX IF NOT EXISTS group_members_user_idx ON group_members (user_id);

-- In-flight SSO sign-ins: single-use state with the PKCE verifier and the nonce expected in the ID token.
CREATE TABLE IF NOT EXISTS sso_states (
  state text PRIMARY KEY,
  verifier text NOT NULL,
  nonce text NOT NULL,
  expires_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_log_actor_idx ON audit_log (lower(actor_email));
