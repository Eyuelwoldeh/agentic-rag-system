-- Queries behind answers, so follow-ups ("chart this", "now by month") can reuse them in later turns.
-- Only the SQL is stored; reusing it re-runs the query with the asker's current access.
CREATE TABLE IF NOT EXISTS query_results (
  id text PRIMARY KEY,           -- e.g. "R4f9a1c": what the assistant refers to
  email text,                    -- who ran it (results are only reusable by the same person)
  source text NOT NULL,
  sql text NOT NULL,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS query_results_email_idx ON query_results (email);
