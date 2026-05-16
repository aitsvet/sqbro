import base64
import glob
import logging
import os
import secrets
import sqlite3
import time
from contextlib import closing
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
log = logging.getLogger("sqbro")

_data_dir = os.getenv("DATA_DIR")
if not _data_dir:
    raise SystemExit("DATA_DIR environment variable is required")
base = os.path.realpath(_data_dir)
if not os.path.isdir(base):
    raise SystemExit(f"Data directory does not exist: {base}")

app = FastAPI(title="SQLite Browser")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        user = request.session.get("user_name") if "session" in request.scope else None
        log.exception(
            "request_failed method=%s path=%s user=%s duration_ms=%d",
            request.method,
            request.url.path,
            user,
            duration_ms,
        )
        raise
    duration_ms = int((time.monotonic() - start) * 1000)
    user = request.session.get("user_name") if "session" in request.scope else None
    log.info(
        "request method=%s path=%s status=%d user=%s duration_ms=%d",
        request.method,
        request.url.path,
        status,
        user,
        duration_ms,
    )
    return response


SECRET_KEY = os.getenv("SESSION_SECRET")
if not SECRET_KEY:
    raise ValueError("SESSION_SECRET environment variable is required")
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "true").lower() != "false"
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    https_only=SESSION_HTTPS_ONLY,
    same_site="lax",
)

OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID")
OAUTH_SECRET = os.getenv("OAUTH_SECRET")
OAUTH_AUTH_URL = os.getenv("OAUTH_AUTH_URL")
OAUTH_TOKEN_URL = os.getenv("OAUTH_TOKEN_URL")
OAUTH_PROFILE_URL = os.getenv("OAUTH_PROFILE_URL")
OAUTH_REDIRECT_URL = os.getenv("OAUTH_REDIRECT_URL")
OAUTH_CALLBACK_URL = os.getenv("OAUTH_CALLBACK_URL")
OAUTH_SCOPE = os.getenv("OAUTH_SCOPE", "openid profile")
OAUTH_PROFILE_NAME_FIELD = os.getenv("OAUTH_PROFILE_NAME_FIELD", "name")
OAUTH_CA_BUNDLE = os.getenv("OAUTH_CA_BUNDLE")
OAUTH_TLS_VERIFY = os.getenv("OAUTH_TLS_VERIFY", "true").lower() != "false"
OAUTH_VERIFY = OAUTH_CA_BUNDLE if OAUTH_CA_BUNDLE else OAUTH_TLS_VERIFY

if not all(
    [
        OAUTH_CLIENT_ID,
        OAUTH_SECRET,
        OAUTH_AUTH_URL,
        OAUTH_TOKEN_URL,
        OAUTH_PROFILE_URL,
        OAUTH_REDIRECT_URL,
        OAUTH_CALLBACK_URL,
    ]
):
    raise ValueError("Missing required OAuth environment variables")


def require_api_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")


OAUTH_STATE_TTL = 600  # seconds; OAuth flow window
state_signer = URLSafeTimedSerializer(SECRET_KEY, salt="oauth-state")


def make_state() -> str:
    return state_signer.dumps(secrets.token_urlsafe(16))


def verify_state(state: str) -> bool:
    try:
        state_signer.loads(state, max_age=OAUTH_STATE_TTL)
        return True
    except (BadSignature, SignatureExpired):
        return False


async def require_auth(request: Request):
    if not request.session.get("authenticated"):
        params = {
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "scope": OAUTH_SCOPE,
            "redirect_uri": OAUTH_CALLBACK_URL,
            "state": make_state(),
        }

        auth_url = f"{OAUTH_AUTH_URL}?{urlencode(params)}"
        log.info("oauth_login_start path=%s", request.url.path)
        return RedirectResponse(url=auth_url)
    return None


@app.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        log.warning("oauth_callback_error error=%s description=%s", error, error_description)
        detail = f"OAuth error: {error}"
        if error_description:
            detail += f" — {error_description}"
        raise HTTPException(status_code=400, detail=detail)

    if not code or not state:
        log.warning("oauth_callback_missing_params has_code=%s has_state=%s", bool(code), bool(state))
        raise HTTPException(status_code=400, detail="Missing code or state parameter")

    if not verify_state(state):
        log.warning("oauth_callback_bad_state")
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")

    credentials = f"{OAUTH_CLIENT_ID}:{OAUTH_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    async with httpx.AsyncClient(timeout=10.0, verify=OAUTH_VERIFY) as client:
        token_response = await client.post(
            OAUTH_TOKEN_URL,
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"code": code, "grant_type": "authorization_code", "redirect_uri": OAUTH_CALLBACK_URL},
        )

        if token_response.status_code != 200:
            raise HTTPException(
                status_code=token_response.status_code, detail=f"Token exchange failed: {token_response.text}"
            )

        token_data = token_response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            raise HTTPException(status_code=500, detail="No access token in response")

        profile_response = await client.get(OAUTH_PROFILE_URL, headers={"Authorization": f"Bearer {access_token}"})

        if profile_response.status_code != 200:
            raise HTTPException(
                status_code=profile_response.status_code, detail=f"Profile fetch failed: {profile_response.text}"
            )

        profile = profile_response.json()
        user_name = profile.get(OAUTH_PROFILE_NAME_FIELD)

    request.session["authenticated"] = True
    request.session["user_name"] = user_name

    log.info("oauth_login_success user=%s", user_name)
    return RedirectResponse(url=OAUTH_REDIRECT_URL)


@app.post("/oauth/logout")
async def oauth_logout(request: Request):
    user = request.session.get("user_name")
    request.session.clear()
    log.info("oauth_logout user=%s", user)
    return RedirectResponse(url=OAUTH_REDIRECT_URL, status_code=303)


MAX_RECORDS = int(os.getenv("MAX_RECORDS", "1000"))


def resolve_db_path(db_path: str) -> str:
    full = os.path.realpath(os.path.join(base, db_path))
    if full != base and not full.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="Invalid db_path")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Database file not found")
    return full


def connect_ro(db_path: str) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@app.get("/")
async def read_index(request: Request):
    auth_redirect = await require_auth(request)
    if auth_redirect:
        return auth_redirect
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))


@app.get("/api/me")
async def get_me(request: Request):
    require_api_auth(request)
    return {"name": request.session.get("user_name")}


@app.get("/api/databases")
async def get_databases(request: Request):
    require_api_auth(request)
    try:
        db_files = glob.glob("**/*.db", root_dir=base, recursive=True) + glob.glob(
            "**/*.sqlite", root_dir=base, recursive=True
        )
        return {"databases": [f for f in db_files if os.path.isfile(os.path.join(base, f))]}
    except Exception as e:
        log.exception("list_databases_failed")
        raise HTTPException(status_code=500, detail="Internal error") from e


@app.post("/api/tables")
async def get_tables(request: Request, db_path: str = Form(...)):
    require_api_auth(request)
    resolved = resolve_db_path(db_path)
    user = request.session.get("user_name")
    log.info("list_tables user=%s db=%s", user, db_path)

    try:
        with closing(connect_ro(resolved)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [table[0] for table in cursor.fetchall()]
            table_info = []
            for table in tables:
                # table name comes from sqlite_master and is identifier-quoted; safe
                cursor.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}")  # noqa: S608  # nosec B608
                row_count = cursor.fetchone()[0]
                table_info.append({"name": table, "row_count": row_count})
            return {"tables": table_info}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("list_tables_failed user=%s db=%s", user, db_path)
        raise HTTPException(status_code=500, detail="Internal error") from e


@app.post("/api/records")
async def get_records(
    request: Request, db_path: str = Form(...), table_name: str = Form(...), where_clause: str = Form("")
):
    require_api_auth(request)
    resolved = resolve_db_path(db_path)
    user = request.session.get("user_name")
    log.info(
        "query_records user=%s db=%s table=%s where=%r",
        user,
        db_path,
        table_name,
        where_clause,
    )

    try:
        with closing(connect_ro(resolved)) as conn:
            cursor = conn.cursor()

            # table_name is identifier-quoted; where_clause is intentional user SQL suffix
            query = f"SELECT * FROM {quote_ident(table_name)}"  # noqa: S608  # nosec B608
            if where_clause.strip():
                query += f" {where_clause}"

            cursor.execute(query)
            records = cursor.fetchmany(MAX_RECORDS + 1)
            truncated = len(records) > MAX_RECORDS
            if truncated:
                records = records[:MAX_RECORDS]
            columns = [description[0] for description in cursor.description]

            return {
                "columns": columns,
                "records": records,
                "count": len(records),
                "truncated": truncated,
                "limit": MAX_RECORDS,
            }
    except HTTPException:
        raise
    except sqlite3.Error as e:
        log.warning("query_records_sqlite_error user=%s db=%s table=%s error=%s", user, db_path, table_name, e)
        raise HTTPException(status_code=400, detail=f"SQL error: {e}") from e
    except Exception as e:
        log.exception("query_records_failed user=%s db=%s table=%s", user, db_path, table_name)
        raise HTTPException(status_code=500, detail="Internal error") from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=59620)  # noqa: S104 — containerized service
