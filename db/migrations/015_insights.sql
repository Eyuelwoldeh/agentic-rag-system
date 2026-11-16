-- Business insights: metric definitions, scheduled reports, alerts.

-- Semantic layer: named, validated aggregates over one table of one data source.
CREATE TABLE IF NOT EXISTS metrics (
  id serial PRIMARY KEY,
  name text NOT NULL UNIQUE,
  description text NOT NULL DEFAULT '',
  source text NOT NULL,             -- data source id: 'uploads' or 'conn-<id>'
  table_name text NOT NULL,
  expression text NOT NULL,         -- e.g. sum(revenue) - sum(cost_of_goods)
  time_column text,
  filters text,                     -- optional SQL predicate, validated
  format text NOT NULL DEFAULT 'number' CHECK (format IN ('currency', 'number', 'percent')),
  owner_id int REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Scheduled reports: a question (re-asked with each person's own access) or a set of dashboard tiles.
CREATE TABLE IF NOT EXISTS reports (
  id serial PRIMARY KEY,
  owner_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title text NOT NULL,
  question text,
  tile_ids int[],
  cadence text NOT NULL CHECK (cadence IN ('daily', 'weekly', 'monthly')),
  weekday int CHECK (weekday BETWEEN 0 AND 6),   -- Monday = 0, for weekly
  day int CHECK (day BETWEEN 1 AND 28),           -- for monthly
  hour int NOT NULL DEFAULT 8 CHECK (hour BETWEEN 0 AND 23),  -- server local time
  recipients int[] NOT NULL DEFAULT '{}',          -- user ids, besides the owner
  enabled boolean NOT NULL DEFAULT true,
  last_run_at timestamptz,
  last_status text,
  last_error text,
  next_run_at timestamptz,
  created_at timestamptz DEFAULT now(),
  CHECK (question IS NOT NULL OR tile_ids IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS reports_due_idx ON reports (next_run_at) WHERE enabled;

-- Alerts: a metric (or an ad hoc aggregate) checked hourly or daily; notify on transition to triggered.
CREATE TABLE IF NOT EXISTS alerts (
  id serial PRIMARY KEY,
  owner_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name text NOT NULL,
  metric_id int REFERENCES metrics(id) ON DELETE CASCADE,
  source text,
  table_name text,
  expression text,
  time_column text,
  condition text NOT NULL CHECK (condition IN ('drop_vs_last_year', 'above', 'below')),
  threshold double precision NOT NULL,
  group_by text,
  cadence text NOT NULL DEFAULT 'daily' CHECK (cadence IN ('hourly', 'daily')),
  recipients int[] NOT NULL DEFAULT '{}',
  enabled boolean NOT NULL DEFAULT true,
  last_state text,                  -- 'ok' | 'triggered' | 'error'
  last_checked_at timestamptz,
  last_triggered_at timestamptz,
  created_at timestamptz DEFAULT now(),
  CHECK (metric_id IS NOT NULL OR (source IS NOT NULL AND table_name IS NOT NULL AND expression IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS alert_events (
  id serial PRIMARY KEY,
  alert_id int NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
  at timestamptz NOT NULL DEFAULT now(),
  state text NOT NULL,
  message text NOT NULL,
  detail jsonb
);
CREATE INDEX IF NOT EXISTS alert_events_alert_idx ON alert_events (alert_id, at DESC);
