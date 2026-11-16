-- Platform: background job queue, plus indexes for retention and spend limits.
-- Worker heartbeat, retention and limits settings live in app_settings ('worker_heartbeat', 'retention', 'limits').

CREATE TABLE IF NOT EXISTS jobs (
  id bigserial PRIMARY KEY,
  kind text NOT NULL,                       -- e.g. 'connector.sync', 'retention.purge'
  payload jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
  attempts int NOT NULL DEFAULT 0,
  max_attempts int NOT NULL DEFAULT 3,
  run_after timestamptz NOT NULL DEFAULT now(),
  locked_at timestamptz,
  locked_by text,
  error text,
  dedupe_key text,                          -- at most one queued/running job per key
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS jobs_ready_idx ON jobs (run_after, id) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_dedupe_recent_idx ON jobs (dedupe_key, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_dedupe_active_idx ON jobs (dedupe_key) WHERE status IN ('queued', 'running');

-- Daily token budget looks up today's runs per person; retention deletes by age.
CREATE INDEX IF NOT EXISTS runs_user_created_idx ON runs (user_id, created_at);
CREATE INDEX IF NOT EXISTS runs_created_idx ON runs (created_at);
CREATE INDEX IF NOT EXISTS notifications_created_idx ON notifications (created_at);
