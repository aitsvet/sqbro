# sqbro

A minimal, self-hosted SQLite browser with OAuth2 authentication. Point it at a directory of `.db` / `.sqlite` files and browse them through a clean web UI — no credentials stored, no write access granted.

## Features

- Browse all `.db` / `.sqlite` files under a configured data directory
- Inspect tables with row counts; query rows with a freeform SQL suffix (`WHERE`, `ORDER BY`, `LIMIT`, …)
- Results capped server-side (default 1000 rows); truncation shown clearly with guidance to narrow the query
- OAuth2 authorization code flow — identity from your IdP, no passwords stored in sqbro
- Structured request logging with user identity and latency
- Single Docker image, non-root, read-only SQLite connections

## Quick start

```bash
docker run \
  -e SESSION_SECRET=change-me \
  -e OAUTH_CLIENT_ID=... \
  -e OAUTH_SECRET=... \
  -e OAUTH_AUTH_URL=https://idp.example.com/auth \
  -e OAUTH_TOKEN_URL=https://idp.example.com/token \
  -e OAUTH_PROFILE_URL=https://idp.example.com/userinfo \
  -e OAUTH_REDIRECT_URL=http://localhost:59620/ \
  -e OAUTH_CALLBACK_URL=http://localhost:59620/oauth/callback \
  -v /your/data:/data \
  -p 59620:59620 \
  sqbro
```

Open `http://localhost:59620/`. You will be redirected to your IdP and land back authenticated.

## Configuration

All configuration is via environment variables.

### Required

| Variable | Description |
|---|---|
| `SESSION_SECRET` | Secret key for signing session cookies (use a long random string) |
| `OAUTH_CLIENT_ID` | OAuth2 client ID registered with your IdP |
| `OAUTH_SECRET` | OAuth2 client secret |
| `OAUTH_AUTH_URL` | IdP authorization endpoint |
| `OAUTH_TOKEN_URL` | IdP token endpoint |
| `OAUTH_PROFILE_URL` | IdP userinfo / profile endpoint |
| `OAUTH_REDIRECT_URL` | URL users land on after login and logout (typically `http://host/`) |
| `OAUTH_CALLBACK_URL` | OAuth2 redirect URI registered with your IdP (typically `http://host/oauth/callback`) |

### Optional

| Variable | Default | Description |
|---|---|---|
| `DATA_DIR` | `/data` | Directory that sqbro scans for `.db` / `.sqlite` files |
| `SESSION_HTTPS_ONLY` | `true` | Set `false` to allow session cookies over HTTP (e.g. behind a VPN without TLS) |
| `OAUTH_SCOPE` | `openid profile` | OAuth2 scopes to request |
| `OAUTH_PROFILE_NAME_FIELD` | `name` | JSON field in the profile response to use as the display name |
| `OAUTH_CA_BUNDLE` | — | Path to a CA bundle for verifying the IdP's TLS certificate |
| `OAUTH_TLS_VERIFY` | `true` | Set `false` to disable IdP TLS verification (not recommended) |
| `MAX_RECORDS` | `1000` | Row cap per query; truncated results include the limit in the response |
| `LOG_LEVEL` | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## Running locally (without Docker)

```bash
pip install -r requirements.txt
DATA_DIR=/your/data SESSION_SECRET=dev-secret \
  OAUTH_CLIENT_ID=... OAUTH_SECRET=... \
  OAUTH_AUTH_URL=... OAUTH_TOKEN_URL=... OAUTH_PROFILE_URL=... \
  OAUTH_REDIRECT_URL=http://localhost:59620/ \
  OAUTH_CALLBACK_URL=http://localhost:59620/oauth/callback \
  python main.py
```

Or with uvicorn directly:

```bash
DATA_DIR=/your/data SESSION_SECRET=... uvicorn main:app --host 0.0.0.0 --port 59620
```

## API

All API endpoints require authentication (session cookie set by the OAuth flow). Unauthenticated requests receive `401`.

| Method | Path | Body (form) | Description |
|---|---|---|---|
| `GET` | `/` | — | Main UI; redirects to IdP if not authenticated |
| `GET` | `/api/me` | — | `{"name": "…"}` — display name from IdP profile |
| `GET` | `/api/databases` | — | `{"databases": ["foo.db", …]}` — relative paths under `DATA_DIR` |
| `POST` | `/api/tables` | `db_path` | `{"tables": [{"name": "…", "row_count": N}, …]}` |
| `POST` | `/api/records` | `db_path`, `table_name`, `where_clause` | `{"columns": […], "records": […], "count": N, "truncated": bool, "limit": N}` |
| `POST` | `/oauth/logout` | — | Clears the session and redirects to `OAUTH_REDIRECT_URL` |

`where_clause` is appended verbatim to `SELECT * FROM <table>`. The connection is read-only so only read operations are possible. Examples: `WHERE id > 100`, `ORDER BY created_at DESC LIMIT 50`, `WHERE name LIKE '%smith%' LIMIT 20 OFFSET 40`.

## Security

- **OAuth2 state** is HMAC-signed with a 10-minute TTL (itsdangerous); no server-side state storage, no session fixation.
- **Session cookies** are signed (not encrypted). The cookie contains only `authenticated: true` and the display name — no access token.
- **Path traversal** is prevented by `realpath`-resolving every db path against `DATA_DIR`.
- **SQL injection** is prevented by identifier-quoting all table names and opening SQLite in read-only URI mode.
- **Security headers** are set on every response: CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`.
- **CSRF** on logout is prevented by requiring `POST` with `SameSite=lax` cookies.
- The container runs as UID/GID 1000 (non-root).

## Development

### Linting

Bootstrap a dev venv and run all linters (ruff, bandit, pip-audit):

```bash
bash scripts/lint.sh
```

Auto-fix ruff issues:

```bash
bash scripts/lint.sh fix
```

The dev venv is created at `.venv-dev/` on first run. Tools are sourced from `requirements-dev.txt`.

### E2E tests

Tests run inside Docker Compose. Three containers share a private network: `sqbro`, a mock OAuth IdP, and the test runner.

The test DB (`code.db`) is seeded at runtime by parsing the project's own Python source files with the `ast` module — producing `modules`, `definitions`, and `calls` tables. Tests then query both the API and an in-memory reference built from the same logic, asserting row-for-row equality.

```bash
docker compose up --build --abort-on-container-exit --exit-code-from tests
```

Expected output: `5 passed`.

### Project layout

```
main.py                  FastAPI application (single file)
index.html               Single-page frontend (vanilla JS, no build step)
requirements.txt         Runtime dependencies (~= compatible-release pins)
requirements-dev.txt     Linting tools (ruff, bandit, pip-audit)
Dockerfile               Production image (python:3.12-slim, non-root)
docker-compose.yml       E2E test orchestration
scripts/lint.sh          Linter bootstrap + runner
pyproject.toml           Ruff, bandit, and pytest config
tests/
  seed.py                AST-based DB seeder (also the reference for e2e tests)
  conftest.py            pytest fixtures (OAuth flow, reference DB)
  test_api.py            E2e test suite (5 tests)
  mock-idp/main.py       Minimal OAuth2 IdP for tests
  Dockerfile             Test runner image
  mock-idp/Dockerfile    Mock IdP image
```

## License

MIT — see [LICENSE](LICENSE).
