-- Replacing a file keeps its identity (access, pinned charts) and records what changed.
-- The latest change summary sits on the row so search and the assistant can quote it without a join.
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS replaced_at timestamptz,
  ADD COLUMN IF NOT EXISTS change_note text;
ALTER TABLE data_tables
  ADD COLUMN IF NOT EXISTS replaced_at timestamptz,
  ADD COLUMN IF NOT EXISTS change_note text;

-- History: one row per replacement of a file (all sheets of a workbook share one row).
CREATE TABLE IF NOT EXISTS file_versions (
  id serial PRIMARY KEY,
  key text NOT NULL,                                  -- the file identity ("<uploader id>:<original filename>")
  kind text NOT NULL CHECK (kind IN ('document', 'sheet')),
  filename text NOT NULL,                             -- name of the new version
  replaced_by int REFERENCES users(id) ON DELETE SET NULL,
  change_note text,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS file_versions_key_idx ON file_versions (key);
