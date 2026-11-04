CREATE TABLE IF NOT EXISTS users (
  id serial PRIMARY KEY,
  email text NOT NULL UNIQUE,
  password_hash text NOT NULL,
  role text NOT NULL CHECK (role IN ('admin', 'member')),
  created_at timestamptz DEFAULT now()
);

-- only a SHA-256 of the session token is stored, so a DB leak doesn't leak live sessions
CREATE TABLE IF NOT EXISTS sessions (
  token_hash text PRIMARY KEY,
  user_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at timestamptz NOT NULL
);

-- per-provider OAuth app credentials, entered by an admin; secret is encrypted
CREATE TABLE IF NOT EXISTS oauth_apps (
  provider text PRIMARY KEY,
  client_id text NOT NULL,
  client_secret bytea NOT NULL,
  updated_at timestamptz DEFAULT now()
);

-- in-flight OAuth handshakes: CSRF state bound to a user, plus PKCE verifier
CREATE TABLE IF NOT EXISTS oauth_states (
  state text PRIMARY KEY,
  user_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider text NOT NULL,
  verifier text NOT NULL,
  name text NOT NULL,
  config jsonb NOT NULL DEFAULT '{}',
  expires_at timestamptz NOT NULL
);

ALTER TABLE connections
  ADD COLUMN IF NOT EXISTS credentials bytea,          -- encrypted JSON (access/refresh tokens)
  ADD COLUMN IF NOT EXISTS account text,               -- e.g. Slack workspace name, for display
  ADD COLUMN IF NOT EXISTS created_by int REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS user_id int REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS uploaded_by int REFERENCES users(id) ON DELETE SET NULL;
