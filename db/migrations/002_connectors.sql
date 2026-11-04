CREATE TABLE IF NOT EXISTS connections (
  id serial PRIMARY KEY,
  kind text NOT NULL,
  name text NOT NULL,
  config jsonb NOT NULL DEFAULT '{}',
  enabled boolean NOT NULL DEFAULT true,
  last_synced_at timestamptz,
  status text,
  error text,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'upload',
  ADD COLUMN IF NOT EXISTS external_id text,
  ADD COLUMN IF NOT EXISTS url text,
  ADD COLUMN IF NOT EXISTS updated_at timestamptz,
  ADD COLUMN IF NOT EXISTS connection_id int REFERENCES connections(id) ON DELETE CASCADE;

UPDATE documents SET external_id = name WHERE external_id IS NULL;
ALTER TABLE documents ALTER COLUMN external_id SET NOT NULL;
DO $$ BEGIN
  ALTER TABLE documents ADD CONSTRAINT documents_conn_ext_key UNIQUE NULLS NOT DISTINCT (connection_id, external_id);
EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL; END $$;
