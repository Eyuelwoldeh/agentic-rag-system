-- Access list chosen when starting an OAuth connection, carried through to the callback.
ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS allowed text[];
