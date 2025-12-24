from fastapi import FastAPI, HTTPException, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
import sqlite3
import glob
import os
import sys
import secrets
import base64
import httpx
from urllib.parse import urlencode

load_dotenv()

base = sys.argv[1]
app = FastAPI(title="SQLite Browser")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET_KEY = os.getenv("SESSION_SECRET", secrets.token_urlsafe(32))
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID")
OAUTH_SECRET = os.getenv("OAUTH_SECRET")
OAUTH_AUTH_URL = os.getenv("OAUTH_AUTH_URL")
OAUTH_TOKEN_URL = os.getenv("OAUTH_TOKEN_URL")
OAUTH_PROFILE_URL = os.getenv("OAUTH_PROFILE_URL")
OAUTH_REDIRECT_URL = os.getenv("OAUTH_REDIRECT_URL")
OAUTH_SCOPE = os.getenv("OAUTH_SCOPE", "openid profile")

if not all([OAUTH_CLIENT_ID, OAUTH_SECRET, OAUTH_AUTH_URL, OAUTH_TOKEN_URL, OAUTH_PROFILE_URL, OAUTH_REDIRECT_URL]):
    raise ValueError("Missing required OAuth environment variables")

def get_base_url(request: Request) -> str:
    scheme = request.url.scheme
    host = request.headers.get("host", request.url.hostname)
    return f"{scheme}://{host}"

async def require_auth(request: Request):
    if "access_token" not in request.session:
        state = secrets.token_urlsafe(32)
        request.session["oauth_state"] = state
        base_url = get_base_url(request)
        redirect_uri = f"{base_url}/oauth/callback"
        
        params = {
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "scope": OAUTH_SCOPE,
            "redirect_uri": redirect_uri,
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
    
    base_url = get_base_url(request)
    redirect_uri = f"{base_url}/oauth/callback"
    
    credentials = f"{OAUTH_CLIENT_ID}:{OAUTH_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    
    async with httpx.AsyncClient(verify=False) as client:
        token_response = await client.post(
            OAUTH_TOKEN_URL,
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri
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
    
    request.session["access_token"] = access_token
    request.session.pop("oauth_state", None)
    
    return RedirectResponse(url=OAUTH_REDIRECT_URL)

@app.get("/oauth/logout")
async def oauth_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url=OAUTH_REDIRECT_URL)

@app.get("/")
async def read_index(request: Request):
    auth_redirect = await require_auth(request)
    if auth_redirect:
        return auth_redirect
    return FileResponse("index.html")

@app.get("/api/databases")
async def get_databases(request: Request):
    auth_redirect = await require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        db_files = glob.glob("**/*.db", root_dir=base, recursive=True) + glob.glob("**/*.sqlite", root_dir=base, recursive=True)
        return {"databases": [f for f in db_files if os.path.isfile(os.path.join(base, f))]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tables")
async def get_tables(request: Request, db_path: str = Form(...)):
    auth_redirect = await require_auth(request)
    if auth_redirect:
        return auth_redirect
    db_path = os.path.join(base, db_path)
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Database file not found")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [table[0] for table in cursor.fetchall()]
        table_info = []
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
            row_count = cursor.fetchone()[0]
            table_info.append({"name": table, "row_count": row_count})
        conn.close()
        return {"tables": table_info}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/records")
async def get_records(
    request: Request,
    db_path: str = Form(...),
    table_name: str = Form(...),
    where_clause: str = Form("")
):
    auth_redirect = await require_auth(request)
    if auth_redirect:
        return auth_redirect
    db_path = os.path.join(base, db_path)
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Database file not found")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        if not table_name.replace('_', '').isalnum():
            raise HTTPException(status_code=400, detail="Invalid table name")
        
        query = f"SELECT * FROM {table_name}"
        params = []
        
        if where_clause.strip():
            query += f" {where_clause}"
        
        cursor.execute(query)
        records = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        conn.close()
        
        return {
            "columns": columns,
            "records": records,
            "count": len(records)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=59620)