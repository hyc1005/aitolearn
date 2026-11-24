import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

ROOT = Path(os.getenv("APP_DATA_DIR", "var")).resolve()


@contextmanager
def db():
    ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROOT / "aitolearn.sqlite3", timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("""CREATE TABLE IF NOT EXISTS records (
            kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(kind,id))""")
        with conn:
            yield conn
    finally:
        conn.close()


def put(conn, kind, key, data):
    conn.execute("INSERT INTO records VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data",
                 (kind, key, json.dumps(data, ensure_ascii=False)))
    if kind == "material" and data.get("state") in {"published", "replaced"}:
        from .search import record_publication
        record_publication(conn, data)


def get(conn, kind, key):
    row = conn.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
    return json.loads(row["data"]) if row else None


def all_of(conn, kind):
    return [json.loads(r["data"]) for r in conn.execute("SELECT data FROM records WHERE kind=? ORDER BY id", (kind,))]


def new_id():
    return uuid4().hex


def save_file(raw, suffix):
    directory = ROOT / "files"
    directory.mkdir(parents=True, exist_ok=True)
    file = directory / (new_id() + suffix)
    file.write_bytes(raw)
    return str(file)


if __name__ == "__main__":
    with db():
        pass
