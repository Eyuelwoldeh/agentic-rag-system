-- Saved conversations, so reloading or switching devices doesn't lose them.
CREATE TABLE IF NOT EXISTS conversations (
  id serial PRIMARY KEY,
  user_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title text NOT NULL,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_user_idx ON conversations (user_id, updated_at DESC);

-- Each run (question + answer) belongs to a conversation and keeps what the page needs to redraw it:
-- sources, charts, the "how this was produced" steps, and the hidden query note used for follow-ups.
-- Deleting a conversation keeps its runs (for Activity and Usage) but detaches them.
ALTER TABLE runs
  ADD COLUMN IF NOT EXISTS conversation_id int REFERENCES conversations(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS display jsonb;
CREATE INDEX IF NOT EXISTS runs_conversation_idx ON runs (conversation_id, id);
