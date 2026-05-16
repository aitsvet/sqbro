"""Tiny OAuth2 IdP for e2e tests. Echoes state, returns canned token + profile."""

from fastapi import FastAPI, Form
from fastapi.responses import RedirectResponse

app = FastAPI(title="mock-idp")


@app.get("/auth")
async def auth(redirect_uri: str, state: str, client_id: str = "", response_type: str = "", scope: str = ""):
    return RedirectResponse(f"{redirect_uri}?code=test-code&state={state}")


@app.post("/token")
async def token(code: str = Form(...), grant_type: str = Form(...), redirect_uri: str = Form(...)):
    return {"access_token": "test-access-token", "token_type": "Bearer"}


@app.get("/userinfo")
async def userinfo():
    return {"name": "test-user", "email": "test@example.test"}
