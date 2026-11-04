-- Access control. NULL = everyone signed in; otherwise a list of lowercase emails allowed to see it.
-- A document is visible only if BOTH its own list and its connection's list allow the user.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS allowed text[];
ALTER TABLE connections ADD COLUMN IF NOT EXISTS allowed text[];
CREATE INDEX IF NOT EXISTS documents_allowed_idx ON documents USING gin (allowed);
