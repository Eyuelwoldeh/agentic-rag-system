-- Idempotent: safe to re-run against an existing database.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
  id serial PRIMARY KEY,
  name text NOT NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
  id serial PRIMARY KEY,
  document_id int REFERENCES documents(id) ON DELETE CASCADE,
  page int,
  content text NOT NULL,
  embedding vector(384) NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);

-- observability: one row per agent run
CREATE TABLE IF NOT EXISTS runs (
  id serial PRIMARY KEY,
  question text,
  answer text,
  events jsonb,
  input_tokens int,
  output_tokens int,
  latency_ms int,
  attempts int,
  critic jsonb,
  created_at timestamptz DEFAULT now()
);

-- user-uploaded CSVs land here; the SQL tool can only read this schema
CREATE SCHEMA IF NOT EXISTS data;
DO $$ BEGIN
  CREATE ROLE rag_reader LOGIN PASSWORD 'rag_reader';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT USAGE ON SCHEMA data TO rag_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA data TO rag_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA data GRANT SELECT ON TABLES TO rag_reader;
ALTER ROLE rag_reader SET search_path = data;
ALTER ROLE rag_reader SET statement_timeout = '5s';
ALTER ROLE rag_reader SET default_transaction_read_only = on;
