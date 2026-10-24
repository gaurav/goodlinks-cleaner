"""Read-only reports over the SQLite cache. Nothing here touches the API."""

from __future__ import annotations

import re
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


# -- duplicates --------------------------------------------------------------

def _row_score(r: sqlite3.Row) -> tuple:
    """Higher is a better keeper: anything with user signal wins, then oldest."""
    return (
        int(r["read_at"] is not None) + int(r["starred"]) + int(r["highlighted"]) + int(r["tags"] is not None),
        -len(r["url"]),  # prefer the URL without tracking junk
        r["added_at"] and -int(r["added_at"][:4]) or 0,  # older first (coarse)
    )


_TITLE_SEP = re.compile(r"\s+[|\-–—:·»]\s+")


def norm_title(title: str | None) -> str:
    """Lowercased, whitespace-collapsed title with a trailing ' | Site Name' removed.

    The suffix is dropped only when what remains is still a plausible title
    (10+ chars) and the suffix itself is short (a site name, not a subtitle).
    """
    t = re.sub(r"\s+", " ", (title or "").strip().lower()).rstrip(" .…")
    parts = _TITLE_SEP.split(t)
    if len(parts) > 1 and len(parts[-1]) <= 40 and len(" ".join(parts[:-1])) >= 10:
        t = " ".join(parts[:-1])
    return t


def _same_title(a: str, b: str) -> bool:
    """Equal, or one is a truncated form of the other (Pocket trimmed long titles)."""
    return a == b or (len(min(a, b, key=len)) >= 20 and (a.startswith(b) or b.startswith(a)))


def is_collapsed(group: list[sqlite3.Row]) -> bool:
    """True when links share a URL but carry different titles.

    This happens when a site died or moved and Pocket/GoodLinks stored the
    redirect target (a homepage or section page) while keeping each article's
    original title, e.g. 57 FiveThirtyEight pieces all at abcnews.com/politics.
    The title is the only trace of the article left, so these must not be
    deduplicated away.
    """
    titles = [norm_title(r["title"]) for r in group if r["title"]]
    distinct: list[str] = []
    for t in titles:
        if not any(_same_title(t, d) for d in distinct):
            distinct.append(t)
    return len(distinct) > 1


def _url_groups(conn: sqlite3.Connection) -> list[list[sqlite3.Row]]:
    rows = conn.execute(
        f"""SELECT * FROM links WHERE {LIVE} AND norm_url IN
            (SELECT norm_url FROM links WHERE {LIVE} GROUP BY norm_url HAVING COUNT(*)>1)
            ORDER BY norm_url, added_at"""
    ).fetchall()
    groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        groups.setdefault(r["norm_url"], []).append(r)
    out = []
    for g in groups.values():
        g.sort(key=_row_score, reverse=True)
        out.append(g)
    return out


def dupe_groups(conn: sqlite3.Connection) -> list[list[sqlite3.Row]]:
    """Groups of genuinely duplicate links (same URL, same title), keeper first."""
    return [g for g in _url_groups(conn) if not is_collapsed(g)]


def collapsed_groups(conn: sqlite3.Connection) -> list[list[sqlite3.Row]]:
    """Groups of links whose URL collapsed to a shared landing page (see is_collapsed)."""
    return [g for g in _url_groups(conn) if is_collapsed(g)]


# -- empty / short -------------------------------------------------------------

def empty_candidates(conn: sqlite3.Connection, max_words: int = 20,
                     unread_only: bool = True) -> list[sqlite3.Row]:
    where = f"{LIVE} AND (word_count IS NULL OR word_count <= ?)"
    if unread_only:
        where += " AND read_at IS NULL"
    return conn.execute(
        f"""SELECT l.*, p.status probe_status, p.chars probe_chars, p.sample probe_sample
            FROM links l LEFT JOIN content_probe p ON p.link_id = l.id
            WHERE {where} ORDER BY host, added_at""",
        (max_words,),
    ).fetchall()


def empty_by_host(conn: sqlite3.Connection, max_words: int = 20, unread_only: bool = True,
                  min_count: int = 1) -> list[sqlite3.Row]:
    where = f"{LIVE}" + (" AND read_at IS NULL" if unread_only else "")
    return conn.execute(
        f"""SELECT host, COUNT(*) total,
                   SUM(word_count IS NULL) no_words,
                   SUM(word_count IS NOT NULL AND word_count <= ?) tiny,
                   SUM(p.status = 'missing') probed_missing,
                   SUM(p.status = 'empty') probed_empty,
                   SUM(p.status = 'cached') probed_cached
            FROM links l LEFT JOIN content_probe p ON p.link_id = l.id
            WHERE {where}
            GROUP BY host HAVING (no_words + tiny) >= ?
            ORDER BY (no_words + tiny) DESC""",
        (max_words, min_count),
    ).fetchall()


def short_candidates(conn: sqlite3.Connection, min_words: int, max_words: int,
                     unread_only: bool = True) -> list[sqlite3.Row]:
    where = f"{LIVE} AND word_count BETWEEN ? AND ?"
    if unread_only:
        where += " AND read_at IS NULL"
    return conn.execute(f"SELECT * FROM links WHERE {where} ORDER BY word_count", (min_words, max_words)).fetchall()
