-- Previews: keep the full extracted text of every document, and the original bytes of uploaded files.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content text;

-- Original uploads. `key` is "<uploader id>:<filename>": the same identity documents.external_id and
-- data_tables.external_id (before "::<sheet>") already use, so access checks reuse the existing rules.
CREATE TABLE IF NOT EXISTS upload_files (
  key text PRIMARY KEY,
  filename text NOT NULL,
  mime text NOT NULL,
  size int NOT NULL,
  data bytea NOT NULL,
  uploaded_by int REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz DEFAULT now()
);
