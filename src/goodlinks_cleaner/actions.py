"""Actions that change the GoodLinks library.

Every action takes ``apply``. With ``apply=False`` (the default everywhere in
the CLI) it only returns what it *would* do. With ``apply=True`` it writes to
the API, mirrors the change into the SQLite cache, and appends to the
``actions`` audit table.
"""

from __future__ import annotations

import random
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import httpx

from goodlinks_cleaner import db, reports
from goodlinks_cleaner.api import GoodLinksClient, GoodLinksError
from goodlinks_cleaner.urls import SHORTENER_HOSTS, host

Progress = Callable[[int, int], None]


@dataclass
class Change:
    link_id: str
    url: str
    title: str | None
    action: str
    detail: str
    done: bool = False
    error: str | None = None


@dataclass
class Result:
    changes: list[Change] = field(default_factory=list)
    skipped: int = 0

    @property
    def done(self) -> int:
        return sum(c.done for c in self.changes)

    @property
    def failed(self) -> int:
        return sum(c.error is not None for c in self.changes)


def _add_tags(client: GoodLinksClient, conn: sqlite3.Connection, row: sqlite3.Row,
              new_tags: list[str], change: Change) -> None:
    before = db.tags_of(row)
    try:
        updated = client.edit_link(row["id"], addedTags=new_tags)
    except GoodLinksError as e:
        change.error = str(e)
        return
    after = updated.get("tags") or sorted(set(before) | set(new_tags))
    db.set_tags(conn, row["id"], after)
    db.log_action(conn, row["id"], "add_tags", before, after)
    change.done = True


# -- tag-domains ---------------------------------------------------------------

def tag_domains(client: GoodLinksClient | None, conn: sqlite3.Connection, *, min_count: int = 50,
                prefix: str = "site/", hosts: Iterable[str] | None = None, unread_only: bool = False,
                limit: int | None = None, apply: bool = False, progress: Progress | None = None) -> Result:
    """Add ``<prefix><host>`` to every link on a host with at least min_count links."""
    res = Result()
    if hosts:
        chosen = list(hosts)
    else:
        # Shorteners are handled by `glc shorteners`; tagging them as sites is noise.
        chosen = [r.host for r in reports.domains(conn, min_count, unread_only)
                  if r.host and r.host not in SHORTENER_HOSTS]
    where = f"{reports.LIVE} AND host = ?" + (" AND read_at IS NULL" if unread_only else "")
    todo: list[tuple[sqlite3.Row, str]] = []
    for h in chosen:
        tag = f"{prefix}{h}"
        for row in conn.execute(f"SELECT * FROM links WHERE {where}", (h,)):
            if tag in db.tags_of(row):
                res.skipped += 1
                continue
            todo.append((row, tag))
    if limit is not None:
        todo = todo[:limit]
    for i, (row, tag) in enumerate(todo, 1):
        ch = Change(row["id"], row["url"], row["title"], "add_tags", tag)
        res.changes.append(ch)
        if apply and client is not None:
            _add_tags(client, conn, row, [tag], ch)
            if i % 50 == 0:
                conn.commit()
        if progress:
            progress(i, len(todo))
    conn.commit()
    return res


# -- dedupe --------------------------------------------------------------------

def dedupe(client: GoodLinksClient | None, conn: sqlite3.Connection, *, limit: int | None = None,
           apply: bool = False, progress: Progress | None = None) -> Result:
    """For each group of links with the same normalised URL keep one, merge tags into it,
    and move the rest to GoodLinks' trash."""
    res = Result()
    groups = reports.dupe_groups(conn)
    if limit is not None:
        groups = groups[:limit]
    for gi, group in enumerate(groups, 1):
        keeper, losers = group[0], group[1:]
        keeper_tags = set(db.tags_of(keeper))
        merged = set(keeper_tags)
        for lo in losers:
            merged |= set(db.tags_of(lo))
        new_tags = sorted(merged - keeper_tags)
        if new_tags:
            ch = Change(keeper["id"], keeper["url"], keeper["title"], "add_tags",
                        f"merge tags from {len(losers)} duplicate(s): {', '.join(new_tags)}")
            res.changes.append(ch)
            if apply and client is not None:
                _add_tags(client, conn, keeper, new_tags, ch)
        if losers and apply and client is not None and keeper["read_at"] is None and any(lo["read_at"] for lo in losers):
            # A read copy exists: the keeper inherits the read state so the article
            # doesn't come back as unread.
            try:
                client.edit_link(keeper["id"], read=True)
                conn.execute("UPDATE links SET read_at=? WHERE id=?", (db.now_iso(), keeper["id"]))
                db.log_action(conn, keeper["id"], "mark_read", None, {"reason": "duplicate was read"})
            except GoodLinksError as e:
                res.changes.append(Change(keeper["id"], keeper["url"], keeper["title"], "mark_read", "inherit from duplicate", error=str(e)))
        for lo in losers:
            ch = Change(lo["id"], lo["url"], lo["title"], "delete", f"duplicate of {keeper['id']}")
            res.changes.append(ch)
        if apply and client is not None and losers:
            ids = [lo["id"] for lo in losers]
            try:
                client.delete_links(ids)
            except GoodLinksError as e:
                for ch in res.changes[-len(ids):]:
                    ch.error = str(e)
            else:
                db.mark_deleted(conn, ids)
                for lo in losers:
                    db.log_action(conn, lo["id"], "delete", {"url": lo["url"], "tags": db.tags_of(lo)},
                                  {"duplicate_of": keeper["id"]})
                for ch in res.changes[-len(ids):]:
                    ch.done = True
            if gi % 20 == 0:
                conn.commit()
        if progress:
            progress(gi, len(groups))
    conn.commit()
    return res


# -- empty: probe + tag ------------------------------------------------------------

_SHELL_RE = re.compile(r"^(loading|please enable javascript|javascript is required|just a moment|"
                       r"access denied|attention required|are you a robot|sign in|log in|subscribe)\b", re.IGNORECASE)


_NAV_LINE = re.compile(r"^\s*([-*•#>]|\d+\.)\s|\[https?://|^https?://\S+$", re.IGNORECASE)


def prose_of(plaintext: str) -> str:
    """Strip the title/URL header and navigation link lists GoodLinks' plaintext
    export carries, leaving roughly the article prose."""
    lines = plaintext.splitlines()[1:]  # first line is the title
    kept = [ln.strip() for ln in lines if ln.strip() and not _NAV_LINE.search(ln)]
    return "\n".join(kept).strip()


def probe_content(client: GoodLinksClient, conn: sqlite3.Connection, rows: list[sqlite3.Row], *,
                  auto_download: bool = False, limit: int | None = None,
                  progress: Progress | None = None) -> dict[str, int]:
    """Ask GoodLinks for the cached plaintext of each link and record what came back.

    With auto_download=True GoodLinks fetches articles it has never downloaded,
    which is how null wordCounts get filled in; follow with `glc sync`.
    """
    counts = {"cached": 0, "missing": 0, "empty": 0, "error": 0}
    todo = rows if limit is None else rows[:limit]
    for i, row in enumerate(todo, 1):
        try:
            text = client.content(row["id"], "plaintext", auto_download=auto_download)
        except GoodLinksError:
            counts["error"] += 1
            continue
        if text is None:
            status, chars, sample = "missing", None, None
        else:
            body = prose_of(text)
            chars = len(body)
            sample = body[:200]
            status = "empty" if chars < 200 or _SHELL_RE.match(body) else "cached"
        counts[status] += 1
        if auto_download and text is not None:
            # GoodLinks has just downloaded the article and recomputed wordCount;
            # pull the fresh row so the cache does not wait for the next full sync.
            try:
                db.upsert_links(conn, [client.get_link(row["id"])], db.now_iso())
            except GoodLinksError:
                pass
        conn.execute(
            "INSERT INTO content_probe (link_id, status, chars, sample, probed_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(link_id) DO UPDATE SET status=excluded.status, chars=excluded.chars, "
            "sample=excluded.sample, probed_at=excluded.probed_at",
            (row["id"], status, chars, sample, db.now_iso()),
        )
        if i % 50 == 0:
            conn.commit()
        if progress:
            progress(i, len(todo))
    conn.commit()
    return counts


def tag_rows(client: GoodLinksClient | None, conn: sqlite3.Connection, rows: list[sqlite3.Row], tag: str, *,
             limit: int | None = None, apply: bool = False, progress: Progress | None = None) -> Result:
    """Add one tag to each row (used by `empty` and `short`)."""
    res = Result()
    todo = [r for r in rows if tag not in db.tags_of(r)]
    res.skipped = len(rows) - len(todo)
    if limit is not None:
        todo = todo[:limit]
    for i, row in enumerate(todo, 1):
        ch = Change(row["id"], row["url"], row["title"], "add_tags", tag)
        res.changes.append(ch)
        if apply and client is not None:
            _add_tags(client, conn, row, [tag], ch)
            if i % 50 == 0:
                conn.commit()
        if progress:
            progress(i, len(todo))
    conn.commit()
    return res


# -- shorteners ------------------------------------------------------------------

@dataclass
class Resolved:
    row: sqlite3.Row
    final_url: str | None
    status: int | None
    error: str | None


def shortener_rows(conn: sqlite3.Connection, hosts: Iterable[str] | None = None) -> list[sqlite3.Row]:
    hs = list(hosts) if hosts else sorted(SHORTENER_HOSTS)
    q = ",".join("?" * len(hs))
    return conn.execute(
        f"SELECT * FROM links WHERE {reports.LIVE} AND host IN ({q}) ORDER BY host, added_at", hs
    ).fetchall()


_DNS_FAIL = re.compile(r"nodename nor servname|name or service not known|no address associated|"
                       r"getaddrinfo|temporary failure in name resolution|\[errno 8\]", re.IGNORECASE)


def resolve_url(http: httpx.Client, url: str) -> tuple[str | None, int | None, str | None]:
    """Follow redirects. Returns (final_url, status, error); error is 'dns' when the
    host no longer exists (pocket.co), otherwise the exception class name."""
    try:
        r = http.head(url, follow_redirects=True)
        if r.status_code in (403, 405) or r.status_code >= 500:
            r = http.get(url, follow_redirects=True)
    except httpx.ConnectError as e:
        return None, None, "dns" if _DNS_FAIL.search(str(e)) else type(e).__name__
    except httpx.HTTPError as e:
        return None, None, type(e).__name__
    return str(r.url), r.status_code, None


def _is_dead(status: int | None, error: str | None) -> bool:
    """Only permanent failures count as dead; 5xx, timeouts and odd connection
    errors are transient and are reported but left alone."""
    return error == "dns" or status in (404, 410)


def resolve_shorteners(client: GoodLinksClient | None, conn: sqlite3.Connection, rows: list[sqlite3.Row], *,
                       limit: int | None = None, apply: bool = False, delete_unresolvable: bool = False,
                       dead_tag: str = "status/dead-shortener", timeout: float = 15.0,
                       progress: Progress | None = None) -> tuple[Result, list[Resolved]]:
    """Follow each shortener redirect. Resolvable → upsert the destination with the
    original tags and addedAt, then trash the shortener. Unresolvable → tag (or trash)."""
    res = Result()
    resolved: list[Resolved] = []
    todo = rows if limit is None else rows[:limit]
    http = httpx.Client(timeout=timeout, headers={"User-Agent": "goodlinks-cleaner/0.1 (+https://github.com/gaurav/goodlinks-cleaner)"})
    for i, row in enumerate(todo, 1):
        final, status, error = resolve_url(http, row["url"])
        resolved.append(Resolved(row, final, status, error))
        ok = final is not None and status is not None and status < 400 and host(final) not in SHORTENER_HOSTS and final != row["url"]
        if ok:
            ch = Change(row["id"], row["url"], row["title"], "upsert", f"→ {final}")
            res.changes.append(ch)
            if apply and client is not None:
                try:
                    existing = client.get_link_by_url(final)
                    new = client.add_link(final, tags=db.tags_of(row) or None, read=row["read_at"] is not None,
                                          starred=bool(row["starred"]), addedAt=row["added_at"])
                    client.delete_links([row["id"]])
                except GoodLinksError as e:
                    ch.error = str(e)
                else:
                    db.upsert_links(conn, [new], db.now_iso())
                    db.mark_deleted(conn, [row["id"]])
                    db.log_action(conn, row["id"], "upsert", {"url": row["url"]},
                                  {"url": final, "new_id": new["id"], "merged_into_existing": existing is not None})
                    ch.done = True
        elif not _is_dead(status, error):
            # Transient or ambiguous (5xx, timeout, blocked by a bot wall): leave it.
            res.skipped += 1
        else:
            why = error or (f"HTTP {status}" if status else "no redirect")
            if delete_unresolvable:
                ch = Change(row["id"], row["url"], row["title"], "delete", f"unresolvable ({why})")
                res.changes.append(ch)
                if apply and client is not None:
                    try:
                        client.delete_links([row["id"]])
                    except GoodLinksError as e:
                        ch.error = str(e)
                    else:
                        db.mark_deleted(conn, [row["id"]])
                        db.log_action(conn, row["id"], "delete", {"url": row["url"]}, {"reason": why})
                        ch.done = True
            elif dead_tag not in db.tags_of(row):
                ch = Change(row["id"], row["url"], row["title"], "add_tags", f"{dead_tag} ({why})")
                res.changes.append(ch)
                if apply and client is not None:
                    _add_tags(client, conn, row, [dead_tag], ch)
            else:
                res.skipped += 1
        if i % 20 == 0:
            conn.commit()
        if progress:
            progress(i, len(todo))
    http.close()
    conn.commit()
    return res, resolved


# -- random pick -------------------------------------------------------------------

def random_pick(conn: sqlite3.Connection, *, count: int = 1, max_words: int | None = None,
                min_words: int | None = None, host_filter: str | None = None, tag: str | None = None,
                year: int | None = None, exclude_picked: bool = True) -> list[sqlite3.Row]:
    where = [reports.LIVE, "read_at IS NULL"]
    args: list = []
    if max_words is not None:
        where.append("word_count IS NOT NULL AND word_count <= ?"); args.append(max_words)
    if min_words is not None:
        where.append("word_count >= ?"); args.append(min_words)
    if host_filter:
        where.append("host = ?"); args.append(host(host_filter) or host_filter)
    if tag:
        where.append("tags LIKE ?"); args.append(f'%"{tag}"%')
    if year:
        where.append("substr(added_at,1,4) = ?"); args.append(str(year))
    if exclude_picked:
        where.append("id NOT IN (SELECT link_id FROM actions WHERE action='pick')")
    ids = [r[0] for r in conn.execute(f"SELECT id FROM links WHERE {' AND '.join(where)}", args)]
    chosen = random.sample(ids, min(count, len(ids)))
    if not chosen:
        return []
    q = ",".join("?" * len(chosen))
    return conn.execute(f"SELECT * FROM links WHERE id IN ({q})", chosen).fetchall()


def record_pick(client: GoodLinksClient | None, conn: sqlite3.Connection, row: sqlite3.Row, *,
                mark_read: bool, pick_tag: str | None) -> None:
    db.log_action(conn, row["id"], "pick", None, {"mark_read": mark_read, "tag": pick_tag})
    if client is not None and (mark_read or pick_tag):
        fields: dict = {}
        if mark_read:
            fields["read"] = True
        if pick_tag and pick_tag not in db.tags_of(row):
            fields["addedTags"] = [pick_tag]
        updated = client.edit_link(row["id"], **fields)
        if mark_read:
            conn.execute("UPDATE links SET read_at=? WHERE id=?", (updated.get("readAt") or db.now_iso(), row["id"]))
            db.log_action(conn, row["id"], "mark_read", None, {"reason": "picked"})
        if "addedTags" in fields:
            db.set_tags(conn, row["id"], updated.get("tags") or db.tags_of(row) + [pick_tag])
            db.log_action(conn, row["id"], "add_tags", db.tags_of(row), updated.get("tags"))
    conn.commit()
