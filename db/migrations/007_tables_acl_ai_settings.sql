-- Registry of queryable tables (uploaded spreadsheets and synced Drive sheets), each with an access list.
-- Access is enforced by Postgres itself: queries run as a per-user login role that is only GRANTed
-- SELECT on the tables that user may see (see app/tableacl.py).
CREATE TABLE IF NOT EXISTS data_tables (
  name text PRIMARY KEY,                 -- table name inside schema `data`
  title text NOT NULL,                   -- human label, e.g. "Q3 forecast.xlsx › Summary"
  source text NOT NULL DEFAULT 'upload', -- 'upload' or a connector kind (e.g. 'gdrive')
  connection_id int REFERENCES connections(id) ON DELETE CASCADE,
  external_id text,                      -- identity at the source, so re-syncs replace instead of duplicating
  uploaded_by int REFERENCES users(id) ON DELETE SET NULL,
  allowed text[],                        -- NULL = everyone signed in
  row_count int,
  updated_at timestamptz DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS data_tables_identity ON data_tables (connection_id, external_id) NULLS NOT DISTINCT
  WHERE external_id IS NOT NULL;

-- Tables that already exist (earlier CSV uploads) become public entries.
INSERT INTO data_tables (name, title)
  SELECT table_name, table_name FROM information_schema.tables WHERE table_schema = 'data'
  ON CONFLICT (name) DO NOTHING;

-- The shared reader role no longer reads spreadsheets directly; per-user roles do.
REVOKE SELECT ON ALL TABLES IN SCHEMA data FROM rag_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA data REVOKE SELECT ON TABLES FROM rag_reader;

-- Admin-editable settings (AI provider choice, models). `secret` holds encrypted API keys.
CREATE TABLE IF NOT EXISTS app_settings (
  key text PRIMARY KEY,
  value jsonb NOT NULL DEFAULT '{}',
  secret bytea,
  updated_at timestamptz DEFAULT now()
);
