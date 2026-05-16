"""E2E: assert API answers equal the AST reference, computed independently in-test."""

import httpx
import pytest

SEED_DB = "code.db"


def test_unauth_api_returns_401(sqbro_url):
    with httpx.Client(base_url=sqbro_url, follow_redirects=False) as c:
        r = c.get("/api/databases")
    assert r.status_code == 401


def test_me_after_oauth_flow(authed_client):
    r = authed_client.get("/api/me")
    assert r.status_code == 200
    assert r.json()["name"], "expected a non-empty user name after OAuth flow"


def test_databases_includes_seed(authed_client):
    r = authed_client.get("/api/databases")
    assert r.status_code == 200
    assert SEED_DB in r.json()["databases"]


def test_tables_match_reference(authed_client, reference_db):
    r = authed_client.post("/api/tables", data={"db_path": SEED_DB})
    assert r.status_code == 200
    api_tables = {t["name"]: t["row_count"] for t in r.json()["tables"]}

    ref_tables: dict[str, int] = {}
    for (name,) in reference_db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        # table names come from sqlite_master, identifier-quoted; safe
        count = reference_db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]  # noqa: S608
        ref_tables[name] = count

    assert api_tables == ref_tables


def _reference_table_names(conn) -> list[str]:
    return [name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


@pytest.fixture(scope="session")
def table_names(reference_db) -> list[str]:
    return _reference_table_names(reference_db)


def test_every_table_matches_reference_rows(authed_client, reference_db, table_names):
    for table in table_names:
        api = authed_client.post(
            "/api/records",
            data={"db_path": SEED_DB, "table_name": table, "where_clause": "ORDER BY rowid"},
        )
        assert api.status_code == 200, f"{table}: {api.status_code} {api.text}"
        api_cols = api.json()["columns"]
        api_rows = [tuple(row) for row in api.json()["records"]]

        # table name from the parametrize list (test-controlled), identifier-quoted
        cur = reference_db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')  # noqa: S608
        ref_cols = [d[0] for d in cur.description]
        ref_rows = cur.fetchall()

        assert api_cols == ref_cols, f"{table}: columns differ"
        assert api_rows == ref_rows, f"{table}: rows differ ({len(api_rows)} vs {len(ref_rows)})"
        assert api.json()["truncated"] is False, f"{table}: results were truncated"
