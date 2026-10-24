"""Read-only reports over the SQLite cache. Nothing here touches the API."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

WORD_BUCKETS: list[tuple[str, int | None, int | None]] = [
    ("no word count", None, None),
    ("1-20 (likely empty)", 1, 20),
    ("21-300", 21, 300),
    ("301-1000", 301, 1000),
    ("1001-3000", 1001, 3000),
    ("3001+", 3001, None),
]

LIVE = "deleted_at IS NULL"


def _scalar(conn: sqlite3.Connection, sql: str, *args) -> int:
    return conn.execute(sql, args).fetchone()[0]


def stats(conn: sqlite3.Connection) -> dict:
    out: dict = {}
    out["total"] = _scalar(conn, f"SELECT COUNT(*) FROM links WHERE {LIVE}")
    out["unread"] = _scalar(conn, f"SELECT COUNT(*) FROM links WHERE {LIVE} AND read_at IS NULL")
    out["read"] = out["total"] - out["unread"]
    out["starred"] = _scalar(conn, f"SELECT COUNT(*) FROM links WHERE {LIVE} AND starred=1")
    out["highlighted"] = _scalar(conn, f"SELECT COUNT(*) FROM links WHERE {LIVE} AND highlighted=1")
    out["tagged"] = _scalar(conn, f"SELECT COUNT(*) FROM links WHERE {LIVE} AND tags IS NOT NULL")
    out["untitled"] = _scalar(conn, f"SELECT COUNT(*) FROM links WHERE {LIVE} AND (title IS NULL OR title='')")
    out["deleted_seen"] = _scalar(conn, "SELECT COUNT(*) FROM links WHERE deleted_at IS NOT NULL")
    out["last_sync"] = conn.execute("SELECT MAX(synced_at) FROM links").fetchone()[0]

    buckets = []
    for label, lo, hi in WORD_BUCKETS:
        if lo is None:
            cond = "word_count IS NULL"
        elif hi is None:
            cond = f"word_count >= {lo}"
        else:
            cond = f"word_count BETWEEN {lo} AND {hi}"
        buckets.append((label, _scalar(conn, f"SELECT COUNT(*) FROM links WHERE {LIVE} AND read_at IS NULL AND {cond}")))
    out["unread_by_words"] = buckets

    out["unread_by_year"] = conn.execute(
        f"SELECT substr(added_at,1,4) y, COUNT(*) FROM links WHERE {LIVE} AND read_at IS NULL "
        "GROUP BY y ORDER BY y"
    ).fetchall()
    out["dupe_groups"] = _scalar(conn, f"SELECT COUNT(*) FROM (SELECT norm_url FROM links WHERE {LIVE} GROUP BY norm_url HAVING COUNT(*)>1)")
    out["dupe_extra"] = _scalar(conn, f"SELECT COALESCE(SUM(c-1),0) FROM (SELECT COUNT(*) c FROM links WHERE {LIVE} GROUP BY norm_url HAVING c>1)")
    out["distinct_hosts"] = _scalar(conn, f"SELECT COUNT(DISTINCT host) FROM links WHERE {LIVE} AND read_at IS NULL")
    return out


@dataclass
class DomainRow:
    host: str
    total: int
    unread: int
    median_words: int | None
    no_words: int


def domains(conn: sqlite3.Connection, min_count: int = 1, unread_only: bool = False,
            limit: int | None = None) -> list[DomainRow]:
    where = f"{LIVE}" + (" AND read_at IS NULL" if unread_only else "")
    rows = conn.execute(
        f"""
        SELECT host, COUNT(*) total,
               SUM(read_at IS NULL) unread,
               SUM(word_count IS NULL) no_words
        FROM links WHERE {where}
        GROUP BY host HAVING total >= ? ORDER BY total DESC
        """ + (f" LIMIT {int(limit)}" if limit else ""),
        (min_count,),
    ).fetchall()
    out = []
    for r in rows:
        med = conn.execute(
            f"""SELECT word_count FROM links WHERE {where} AND host=? AND word_count IS NOT NULL
                ORDER BY word_count LIMIT 1 OFFSET
                (SELECT COUNT(*) FROM links WHERE {where} AND host=? AND word_count IS NOT NULL)/2""",
            (r["host"], r["host"]),
        ).fetchone()
        out.append(DomainRow(r["host"], r["total"], r["unread"], med[0] if med else None, r["no_words"]))
    return out
