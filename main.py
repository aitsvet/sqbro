from fastapi import FastAPI, HTTPException, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
import sqlite3
import glob
import os
import sys
import secrets
import base64
import httpx
from contextlib import closing
from urllib.parse import urlencode

load_dotenv()

if len(sys.argv) < 2:
    raise SystemExit("Usage: main.py <data-dir>")
base = os.path.realpath(sys.argv[1])
if not os.path.isdir(base):
    raise SystemExit(f"Data directory does not exist: {base}")

app = FastAPI(title="SQLite Browser")

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

if not all([OAUTH_CLIENT_ID, OAUTH_SECRET, OAUTH_AUTH_URL, OAUTH_TOKEN_URL, OAUTH_PROFILE_URL, OAUTH_REDIRECT_URL, OAUTH_CALLBACK_URL]):
    raise ValueError("Missing required OAuth environment variables")

def require_api_auth(request: Request) -> None:
    if "access_token" not in request.session:
        raise HTTPException(status_code=401, detail="Not authenticated")

async def require_auth(request: Request):
    if "access_token" not in request.session:
        state = secrets.token_urlsafe(32)
        request.session["oauth_state"] = state

        params = {
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "scope": OAUTH_SCOPE,
            "redirect_uri": OAUTH_CALLBACK_URL,
            "state": state
        }

        auth_url = f"{OAUTH_AUTH_URL}?{urlencode(params)}"
        return RedirectResponse(url=auth_url)
    return None

@app.get("/oauth/callback")
async def oauth_callback(request: Request, code: str = None, state: str = None, error: str = None):
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")
    
    stored_state = request.session.get("oauth_state")
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    credentials = f"{OAUTH_CLIENT_ID}:{OAUTH_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    
    async with httpx.AsyncClient(timeout=10.0, verify=OAUTH_VERIFY) as client:
        token_response = await client.post(
            OAUTH_TOKEN_URL,
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": OAUTH_CALLBACK_URL
            }
        )
        
        if token_response.status_code != 200:
            raise HTTPException(
                status_code=token_response.status_code,
                detail=f"Token exchange failed: {token_response.text}"
            )
        
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        
        if not access_token:
            raise HTTPException(status_code=500, detail="No access token in response")

        profile_response = await client.get(
            OAUTH_PROFILE_URL,
            headers={"Authorization": f"Bearer {access_token}"}
        )

        if profile_response.status_code != 200:
            raise HTTPException(
                status_code=profile_response.status_code,
                detail=f"Profile fetch failed: {profile_response.text}"
            )

        profile = profile_response.json()
        user_name = profile.get(OAUTH_PROFILE_NAME_FIELD)

    request.session["access_token"] = access_token
    request.session["user_name"] = user_name
    request.session.pop("oauth_state", None)
    
    return RedirectResponse(url=OAUTH_REDIRECT_URL)

@app.get("/oauth/logout")
async def oauth_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url=OAUTH_REDIRECT_URL)

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
    return FileResponse("index.html")

@app.get("/api/me")
async def get_me(request: Request):
    require_api_auth(request)
    return {"name": request.session.get("user_name")}

@app.get("/api/databases")
async def get_databases(request: Request):
    require_api_auth(request)
    try:
        db_files = glob.glob("**/*.db", root_dir=base, recursive=True) + glob.glob("**/*.sqlite", root_dir=base, recursive=True)
        return {"databases": [f for f in db_files if os.path.isfile(os.path.join(base, f))]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tables")
async def get_tables(request: Request, db_path: str = Form(...)):
    require_api_auth(request)
    db_path = resolve_db_path(db_path)

    try:
        with closing(connect_ro(db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [table[0] for table in cursor.fetchall()]
            table_info = []
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}")
                row_count = cursor.fetchone()[0]
                table_info.append({"name": table, "row_count": row_count})
            return {"tables": table_info}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/records")
async def get_records(
    request: Request,
    db_path: str = Form(...),
    table_name: str = Form(...),
    where_clause: str = Form("")
):
    require_api_auth(request)
    if not table_name.replace('_', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid table name")

    db_path = resolve_db_path(db_path)

    try:
        with closing(connect_ro(db_path)) as conn:
            cursor = conn.cursor()

            query = f"SELECT * FROM {quote_ident(table_name)}"
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=59620)