"""SQLite cache of the GoodLinks library plus an audit log of our writes.

Reports read only from this cache; actions write to the API and then update
the cache row and append to ``actions`` so every change is traceable.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from goodlinks_cleaner.urls import host, normalize_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    id            TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    host          TEXT NOT NULL,
    norm_url      TEXT NOT NULL,
    title         TEXT,
    summary       TEXT,
    author        TEXT,
    tags          TEXT,          -- JSON array or NULL
    word_count    INTEGER,
    starred       INTEGER NOT NULL DEFAULT 0,
    highlighted   INTEGER NOT NULL DEFAULT 0,
    added_at      TEXT NOT NULL,
    modified_at   TEXT NOT NULL,
    read_at       TEXT,
    synced_at     TEXT NOT NULL,
    deleted_at    TEXT           -- set when a full sync no longer sees the id
);
CREATE INDEX IF NOT EXISTS links_host ON links(host);
CREATE INDEX IF NOT EXISTS links_norm_url ON links(norm_url);
CREATE INDEX IF NOT EXISTS links_read_at ON links(read_at);
CREATE INDEX IF NOT EXISTS links_word_count ON links(word_count);

CREATE TABLE IF NOT EXISTS content_probe (
    link_id    TEXT PRIMARY KEY REFERENCES links(id),
    status     TEXT NOT NULL,    -- cached | missing | empty | error (GoodLinks timed out)
    chars      INTEGER,
    sample     TEXT,             -- first ~200 chars, for eyeballing SPA shells
    probed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS url_checks (
    link_id      TEXT PRIMARY KEY REFERENCES links(id),
    http_status  INTEGER,
    final_url    TEXT,
    error        TEXT,
    wayback_url  TEXT,
    checked_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id  TEXT,
    action   TEXT NOT NULL,      -- add_tags | remove_tags | mark_read | delete | upsert | pick
    before   TEXT,               -- JSON
    after    TEXT,               -- JSON
    ts       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS actions_link ON actions(link_id);
CREATE INDEX IF NOT EXISTS actions_action ON actions(action);
"""


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def link_row(link: dict[str, Any], synced_at: str) -> tuple:
    tags = link.get("tags")
    return (
        link["id"], link["url"], host(link["url"]), normalize_url(link["url"]),
        link.get("title"), link.get("summary"), link.get("author"),
        json.dumps(tags) if tags else None,
        link.get("wordCount"),
        int(bool(link.get("starred"))), int(bool(link.get("highlighted"))),
        link["addedAt"], link["modifiedAt"], link.get("readAt"),
        synced_at,
    )


UPSERT_LINK = """
INSERT INTO links (id, url, host, norm_url, title, summary, author, tags, word_count,
                   starred, highlighted, added_at, modified_at, read_at, synced_at, deleted_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
ON CONFLICT(id) DO UPDATE SET
    url=excluded.url, host=excluded.host, norm_url=excluded.norm_url,
    title=excluded.title, summary=excluded.summary, author=excluded.author,
    tags=excluded.tags, word_count=excluded.word_count, starred=excluded.starred,
    highlighted=excluded.highlighted, added_at=excluded.added_at,
    modified_at=excluded.modified_at, read_at=excluded.read_at,
    synced_at=excluded.synced_at, deleted_at=NULL
"""


def upsert_links(conn: sqlite3.Connection, links: list[dict[str, Any]], synced_at: str) -> None:
    conn.executemany(UPSERT_LINK, [link_row(x, synced_at) for x in links])


def mark_deleted(conn: sqlite3.Connection, link_ids: list[str], ts: str | None = None) -> None:
    ts = ts or now_iso()
    conn.executemany("UPDATE links SET deleted_at=? WHERE id=?", [(ts, i) for i in link_ids])


def tags_of(row: sqlite3.Row | dict) -> list[str]:
    raw = row["tags"]
    return json.loads(raw) if raw else []


def set_tags(conn: sqlite3.Connection, link_id: str, tags: list[str]) -> None:
    conn.execute("UPDATE links SET tags=? WHERE id=?", (json.dumps(tags) if tags else None, link_id))


def log_action(conn: sqlite3.Connection, link_id: str | None, action: str,
               before: Any = None, after: Any = None) -> None:
    conn.execute(
        "INSERT INTO actions (link_id, action, before, after, ts) VALUES (?,?,?,?,?)",
        (link_id, action, json.dumps(before) if before is not None else None,
         json.dumps(after) if after is not None else None, now_iso()),
    )
