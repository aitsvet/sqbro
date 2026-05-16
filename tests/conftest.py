"""Pytest fixtures: wait for sqbro, complete OAuth flow, build the AST reference DB."""

import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import pytest

# Make seed.py importable when running from this directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed import collect

SQBRO_URL = os.getenv("SQBRO_URL", "http://sqbro:59620")
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "/project"))


def _wait_for_sqbro(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            httpx.get(url, timeout=2.0)
            return
        except httpx.RequestError as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"sqbro not reachable after {timeout}s: {last_err}")


@pytest.fixture(scope="session")
def sqbro_url() -> str:
    _wait_for_sqbro(SQBRO_URL)
    return SQBRO_URL


@pytest.fixture(scope="session")
def authed_client(sqbro_url: str):
    """An httpx Client that has completed the OAuth flow via redirect-following."""
    with httpx.Client(base_url=sqbro_url, follow_redirects=True, timeout=10.0) as client:
        r = client.get("/")
        assert r.status_code == 200, f"OAuth flow failed: {r.status_code} {r.text[:300]}"
        yield client


@pytest.fixture(scope="session")
def reference_db() -> sqlite3.Connection:
    """Ground-truth DB built by re-running the same seed logic against the same sources."""
    conn = sqlite3.connect(":memory:")
    collect(PROJECT_ROOT, conn)
    return conn
