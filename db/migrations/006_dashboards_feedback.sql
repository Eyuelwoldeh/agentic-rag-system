-- Pinned charts. They store the query, not the data: viewing re-runs it with the viewer's access.
CREATE TABLE IF NOT EXISTS dashboard_tiles (
  id serial PRIMARY KEY,
  owner_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title text NOT NULL,
  spec jsonb NOT NULL,            -- {type, x, y}
  source text NOT NULL,           -- data source id: 'uploads' or 'conn-<id>'
  sql text NOT NULL,
  shared boolean NOT NULL DEFAULT false,
  position int NOT NULL DEFAULT 0,
  last_result jsonb,              -- cache; served only to viewers who still have access to `source`
  refreshed_at timestamptz,
  error text,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dashboard_tiles_owner_idx ON dashboard_tiles (owner_id);

-- Thumbs up/down on answers, and critic token usage for honest cost reporting.
ALTER TABLE runs
  ADD COLUMN IF NOT EXISTS feedback smallint CHECK (feedback IN (-1, 1)),
  ADD COLUMN IF NOT EXISTS feedback_note text,
  ADD COLUMN IF NOT EXISTS model text;
