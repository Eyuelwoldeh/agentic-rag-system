# Agentic RAG

A company knowledge and analytics assistant. Ask questions across your **documents**, **connected apps** (Slack, Drive, Notion, GitHub, websites), **live data** (BigQuery, Snowflake, Postgres, CSVs) and **any MCP-enabled tool**. Answers are cited and fact-checked, numbers become charts you can pin to dashboards, and everyone only sees what they're allowed to see.

```
Next.js ──SSE──▶ FastAPI ──▶ Claude or ChatGPT tool loop (automatic failover between them)
                                ├─ search_documents     → pgvector + full-text (hybrid), filtered by the viewer's access
                                ├─ list_data_sources / describe_tables / run_sql → uploads, BigQuery, Snowflake, Postgres (read-only)
                                ├─ analyze_spreadsheet  → calculated findings + charts (works with no AI too)
                                ├─ create_chart         → chart events → "Pin to dashboard"
                                ├─ MCP servers          → live tools in other apps (admin-allowlisted)
                                └─ web_search, code_execution (Anthropic server tools)
                             ──▶ Fact-check critic ─ fail → rewrite (max 3) ─▶ runs table → Activity & Usage
```

**What business users get**

- **Chat** with suggested questions based on your actual data, a getting-started checklist, cited sources, a "Verified" badge, and thumbs up/down.
- **Spreadsheet insights**: upload Excel (every tab) or CSV, or sync Google Sheets and Excel files from Drive, then click **Analyze**. Trends, leaders, fastest growers, dips hidden inside totals, related measures, outliers and data gaps are *calculated*, not guessed. The AI writes the story and searches your docs and Slack for what explains the changes.
- **Charts** from any data question, with a table view and CSV export on every chart.
- **Dashboards**: pin a chart, and it re-runs its query on view (cached 15 min). No AI cost to refresh. Share with the team; each viewer still only sees data they can access.
- **Usage** (admins): usage, verified-answer rate, estimated cost, and **knowledge gaps**, the questions the assistant couldn't answer well, i.e. the docs you should write next.

## Run it

Needs Docker, [uv](https://docs.astral.sh/uv/), Node 20+ and an Anthropic API key.

```bash
cp .env.example .env          # add ANTHROPIC_API_KEY and a generated SECRETS_KEY
docker compose up -d          # Postgres + pgvector on :5433 (schema migrates on backend start)

cd backend && uv sync
uv run uvicorn app.main:app --reload --port 8000

cd frontend && npm install && npm run dev   # http://localhost:3000
```

Try it with the sample files in `backend/evals/sample/` (upload them in the UI).

## AI providers

Admins pick **Claude** or **ChatGPT** in **Settings → AI model** and paste API keys there. Keys are verified before saving, stored encrypted and never shown again; the model list comes from the provider. The main provider is used first. If it's out of credits or its key fails, the other takes over automatically, and with neither available the app still answers with search results and calculated spreadsheet insights. Web search, code execution and MCP apps are Claude-only.

## Accounts & security

- **First run:** open the app and create the admin account. Admins add teammates in **Settings → Team** (a one-time temporary password is shown once).
- **Roles:** admins manage connectors, sign-in apps and users. Members can chat, upload, delete their own uploads, and see their own runs.
- **Sessions:** HttpOnly, SameSite=Lax cookies (Secure automatically when `PUBLIC_URL` is https). Only a SHA-256 of each session token is stored. Changing your password signs out your other sessions.
- **CSRF:** state-changing requests must carry an `X-Requested-With` header, which forces a CORS preflight that only `FRONTEND_URL` passes.
- **Passwords:** scrypt hashes, a 10-character minimum, and sign-in throttling (8 failures per 15 minutes per email+IP).
- **Secrets at rest:** connector tokens and OAuth client secrets are Fernet-encrypted with `SECRETS_KEY`. They are write-only: the API never returns them.
- **Spreadsheet access is enforced by Postgres:** each user queries as their own database login role, granted `SELECT` only on the sheets they may see (private uploads, Drive sharing, connection access lists), inside a read-only transaction as a single prepared statement. Even SQL run inside functions (`query_to_xml`) can't reach someone else's sheet. Production note: the app's DB user needs `CREATEROLE`.
- **Crawler:** refuses private, loopback and link-local addresses, and re-checks every redirect hop.
- **Single sign-on (Settings → Single sign-on):** any OpenID Connect provider (Okta, Microsoft Entra ID, Google Workspace). Authorization code + PKCE, a single-use state bound to the browser, a nonce, and ID tokens verified against the provider's JWKS (iss, aud, exp, nonce, `email_verified`). People from the allowed email domains get an account on first sign-in with the default role. "Require SSO" refuses password sign-in for members; admins keep it as a break-glass. Redirect URL: `PUBLIC_URL/api/sso/callback`.
- **SCIM 2.0 provisioning** at `PUBLIC_URL/scim/v2` (Users and Groups, the subset Okta and Entra use). An admin generates the bearer token in Settings; it's shown once and stored as a hash. Deactivating a user deletes their sessions and blocks sign-in immediately.
- **Groups (Settings → Groups, or from SCIM):** access lists can name `group:<name>` as well as emails. Every access check compares the list with the viewer's email plus their groups. Deleting a group removes it from access lists, which never makes content public.
- **Audit log (/audit, admins):** sign-ins and failures, people, roles, groups, SCIM, connections and access, uploads, replacements and deletions, settings changes and dashboard sharing, with the client IP. Filter by action, person and dates; export to CSV.

## Connectors

Manage them at **/connectors** (admins). Each connection is indexed into the shared knowledge base and re-synced every `SYNC_INTERVAL_MINUTES`, picking up only what changed.

There are two ways to connect, both from the UI, with no `.env` editing:

1. **"Continue with …" (OAuth).** An admin registers an OAuth app once per provider under **Settings → Sign-in apps**; the page shows the exact redirect URL and scopes to use. After that, connecting is one click. The flow uses a single-use `state` bound to the signed-in user plus PKCE, and Google tokens are refreshed automatically.
2. **Paste a token.** Good for getting started. The token is verified against the provider (who-am-I call) before it's saved encrypted.

| Connector | What's indexed | Paste option |
|---|---|---|
| Slack | One doc per channel per day, threads inlined. Invite the bot to channels. | Bot token (`xoxb-…`) |
| Google Drive | Docs, Sheets (first tab), Slides, PDFs, text | Service-account JSON key (share folders with its email) |
| Notion | Pages shared with the integration | Internal integration secret |
| GitHub | Markdown docs, plus issues and PRs with comments | Fine-grained PAT (read-only Contents + Issues) |
| Website | Same-site pages under a start URL | Nothing needed |
| Microsoft 365 | OneDrive / SharePoint Word, PowerPoint, PDF, text; Excel → tables; per-file sharing | OAuth only (delegated Files.Read.All, Sites.Read.All) |
| Confluence | Pages in chosen spaces, with links | Atlassian API token (+ account email) |
| Jira | One doc per issue: description, status, assignee, comments | Atlassian API token (+ account email) |
| QuickBooks Online | Tables: monthly P&L (24 months), invoices, expenses | OAuth only (Accounting scope) |
| Xero | Tables: invoices, monthly P&L (12 months) | OAuth only (read-only granular scopes) |
| Stripe | Tables: charges, invoices, subscriptions (amounts in major units) | Restricted read-only key |
| Shopify | Tables: orders, order lines, products/variants | Custom-app Admin API token (read_orders, read_products) |
| Square | Tables: locations, payments by location (last 2 years) | Access token |
| HubSpot | Tables: deals, companies, contacts (name/email/company only) | Private app token (CRM read scopes) |
| Salesforce | Tables: opportunities, accounts (SOQL) | OAuth only |
| BigQuery | Live queries (not copied) | Service-account JSON (Data Viewer + Job User) |
| Snowflake | Live queries (not copied) | Programmatic access token for a read-only role |
| PostgreSQL | Live queries (not copied) | Connection URL of a read-only user |
| MCP server | Live tools in another app | Optional bearer token |

Notes: Slack requires an **https** redirect URL, so for local OAuth use a tunnel (e.g. ngrok) and set `PUBLIC_URL` to it; pasting a bot token works locally. GitHub OAuth apps have no read-only scope for private repos (`repo`); a pasted fine-grained token can be strictly read-only.

Business systems (QuickBooks to Salesforce) are re-exported in full on each sync and replace their tables, capped at 200k rows per table.

Adding a connector = one module in `backend/app/connectors/` exposing `LABEL`, `CATEGORY`, `CONFIG`, `OAUTH`, `PASTE`, `whoami(creds)` and `fetch(config, since, creds)`, plus one line in `REGISTRY`.

**Permissions.** Every document and connection can carry an access list (emails and `group:<name>` entries). Search, SQL, dashboards and MCP tools all apply it, so a document is visible only when both its own list and its connection's list allow the viewer. Lists come from:
- **the connection**: "Who can see results from this source?" when connecting; editable any time and effective immediately;
- **the source itself**: Slack private channels → their members; Google Drive → the file's sharing (link/domain-shared = everyone);
- **uploads**: "Only me" or "Everyone".

People are matched by email, so app accounts should use the same email as Slack and Google. Google *groups* are matched by the group's email address only.

**Live data (BigQuery, Snowflake, Postgres).** Nothing is copied. The agent sees schemas and runs one read-only statement at a time:
- Postgres runs a prepared statement (so it can't be chained) inside a read-only transaction with a 15-second timeout.
- BigQuery dry-runs every query and refuses it above the per-query GB cap, which is also enforced server-side via `maximumBytesBilled`.
- Snowflake runs single statements via the SQL API under the role you give it.

Always connect with a read-only account.

**MCP servers.** Paste the server URL (and token, if needed). The connection is verified with an MCP `initialize` handshake. List the allowed tools to switch everything else off, and prefer read-only tools.

**Known limits:** items deleted at the source stay indexed until the connection is removed and re-added; Notion and GitHub don't expose per-page/per-repo readers, so use the connection's access list for those.

## Test and evaluate

```bash
cd backend
uv run python -m pytest tests        # auth, permissions, OAuth, warehouses, MCP, dashboards, retrieval (throwaway rag_test DB, no API calls)
uv run python -m evals.run_evals     # 7 graded questions over the sample corpus (costs API credits)
```

Change a prompt, the model, retrieval or the critic, then re-run the evals to see whether accuracy moved.

## Config (`.env`)

`AGENT_MODEL` (default `claude-opus-5-5`), `CRITIC_MODEL` (default `claude-haiku-5-5`), `AGENT_EFFORT` (`low|medium|high`, default `medium`).
Refused requests fall back to another model automatically (`fallbacks: "default"`). If Claude is unreachable (out of credits, bad key) or `AGENT_MODE=search` is set, the app falls back to **search-only mode**: it returns the top cited passages without writing an answer.
