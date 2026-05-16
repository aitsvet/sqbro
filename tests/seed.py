"""Build the e2e test DB by parsing project source for definitions and call edges."""

import ast
import sqlite3
import sys
from pathlib import Path

SOURCES = [
    "main.py",
    "tests/mock-idp/main.py",
    "tests/seed.py",
]

SCHEMA = """
CREATE TABLE modules (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE
);
CREATE TABLE definitions (
    id INTEGER PRIMARY KEY,
    module_id INTEGER NOT NULL REFERENCES modules(id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    lineno INTEGER NOT NULL
);
CREATE TABLE calls (
    caller_id INTEGER NOT NULL REFERENCES definitions(id),
    callee_name TEXT NOT NULL
);
CREATE INDEX idx_def_name ON definitions(name);
CREATE INDEX idx_calls_caller ON calls(caller_id);
CREATE INDEX idx_calls_callee ON calls(callee_name);
"""

KIND_BY_NODE = {
    ast.FunctionDef: "function",
    ast.AsyncFunctionDef: "async_function",
    ast.ClassDef: "class",
}


def callee_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def collect(root: Path, conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(SCHEMA)
    for rel in SOURCES:
        src_path = root / rel
        tree = ast.parse(src_path.read_text(), filename=str(src_path))
        cur.execute("INSERT INTO modules (path) VALUES (?)", (rel,))
        module_id = cur.lastrowid
        for node in tree.body:
            if not isinstance(node, tuple(KIND_BY_NODE)):
                continue
            cur.execute(
                "INSERT INTO definitions (module_id, name, kind, lineno) VALUES (?, ?, ?, ?)",
                (module_id, node.name, KIND_BY_NODE[type(node)], node.lineno),
            )
            def_id = cur.lastrowid
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    name = callee_name(sub.func)
                    if name:
                        cur.execute(
                            "INSERT INTO calls (caller_id, callee_name) VALUES (?, ?)",
                            (def_id, name),
                        )
    conn.commit()


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: seed.py <project-root> <db-path>")
    root = Path(sys.argv[1]).resolve()
    db_path = Path(sys.argv[2]).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        collect(root, conn)
    finally:
        conn.close()
    print(f"Seeded {db_path}")


if __name__ == "__main__":
    main()
