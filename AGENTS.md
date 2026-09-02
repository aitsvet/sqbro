# sqbro — daily cheat-sheet

A small web tool: a FastAPI backend serves a static `index.html` that lists `.db`/`.sqlite` files under a `--base` data dir, lets the user pick a table, and runs `SELECT * FROM <table> <freeform-suffix>`. OAuth provides auth against any OIDC-ish IdP through env vars. Deployed as a single Docker image with the data dir bind-mounted.

## Shape

| File | Role |
|---|---|
| `main.py` | the whole backend, ~360 LOC |
| `index.html` | vanilla-JS frontend, no build step |
| `Dockerfile` | `python:3.12-slim`, runs as uid 1000 |

## Standing rules

- **Security-conservative by default, with an env-var escape hatch.** Auth on every endpoint, read-only SQLite connections, path traversal blocked, server-side row caps, HMAC-signed OAuth state, full audit logging of DB access. Whenever a safe default could break someone's setup — HTTPS-only sessions behind an HTTP VPN, TLS verification against a self-signed IdP — expose an env var to opt out rather than hardcoding the strict behaviour.
- **The free-form `where_clause` is the product, not a hole.** Don't try to sandbox it beyond keeping the connection read-only.
- **Name config explicitly, not cleverly.** `OAUTH_REDIRECT_URI` next to an existing `OAUTH_REDIRECT_URL` was rejected; `OAUTH_CALLBACK_URL` won. If two names could be confused, pick the longer unambiguous one.
- **No feature creep** — pagination UI, query builders and similar go unbuilt unless asked.
- **Verify dependency and CVE claims against the live source** before pinning anything: PyPI JSON endpoints, NVD/GHSA. Cite what was checked.

Cross-cutting working rules (commits, autonomy, verification) live in the agent vault at `~/agents-setup/AGENTS.md`.
