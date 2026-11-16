-- Shared foundations for later features.

-- In-app notifications (alerts, scheduled reports, mentions, policy changes...).
CREATE TABLE IF NOT EXISTS notifications (
  id serial PRIMARY KEY,
  user_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind text NOT NULL,               -- e.g. 'alert', 'report', 'mention', 'policy_change', 'share'
  title text NOT NULL,
  body text,
  link text,                        -- in-app path, e.g. '/?c=12' or '/dashboard'
  read_at timestamptz,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notifications_user_idx ON notifications (user_id, created_at DESC);

-- Append-only audit trail of security-relevant actions.
CREATE TABLE IF NOT EXISTS audit_log (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  actor_id int REFERENCES users(id) ON DELETE SET NULL,
  actor_email text,                 -- kept even if the user is deleted
  action text NOT NULL,             -- e.g. 'user.login', 'connection.create', 'document.delete'
  target text,                      -- e.g. 'connection:5', 'document:12'
  detail jsonb,
  ip text
);
CREATE INDEX IF NOT EXISTS audit_log_at_idx ON audit_log (at DESC);
CREATE INDEX IF NOT EXISTS audit_log_action_idx ON audit_log (action);
