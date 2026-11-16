-- Collaboration: shared conversations and comments with @mentions.

-- A conversation shared read-only with one person (user_id) or with everyone (user_id NULL).
CREATE TABLE IF NOT EXISTS conversation_shares (
  id serial PRIMARY KEY,
  conversation_id int NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id int REFERENCES users(id) ON DELETE CASCADE,
  shared_by int REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS conversation_shares_uniq ON conversation_shares (conversation_id, coalesce(user_id, 0));
CREATE INDEX IF NOT EXISTS conversation_shares_user_idx ON conversation_shares (user_id);

-- Comments on answers (runs) and dashboard tiles. Deleted comments are kept (soft delete) for the record.
CREATE TABLE IF NOT EXISTS comments (
  id serial PRIMARY KEY,
  target_type text NOT NULL CHECK (target_type IN ('run', 'tile')),
  target_id int NOT NULL,
  author_id int REFERENCES users(id) ON DELETE SET NULL,
  body text NOT NULL,
  created_at timestamptz DEFAULT now(),
  edited_at timestamptz,
  deleted boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS comments_target_idx ON comments (target_type, target_id, id);
