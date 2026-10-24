"""Pull the library from the API into the SQLite cache."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from goodlinks_cleaner import db
from goodlinks_cleaner.api import GoodLinksClient

Progress = Callable[[int], None]


def full_sync(client: GoodLinksClient, conn: sqlite3.Connection,
              progress: Progress | None = None) -> dict[str, int]:
    """Re-read every link. Ids no longer returned are marked deleted."""
    ts = db.now_iso()
    seen: set[str] = set()
    batch: list[dict] = []
    n = 0
    for link in client.iter_list("all"):
        batch.append(link)
        seen.add(link["id"])
        if len(batch) >= 1000:
            db.upsert_links(conn, batch, ts)
            conn.commit()
            n += len(batch)
            batch.clear()
            if progress:
                progress(n)
    if batch:
        db.upsert_links(conn, batch, ts)
        n += len(batch)
    known = {r[0] for r in conn.execute("SELECT id FROM links WHERE deleted_at IS NULL")}
    vanished = sorted(known - seen)
    db.mark_deleted(conn, vanished, ts)
    conn.commit()
    if progress:
        progress(n)
    return {"synced": n, "vanished": len(vanished)}


def incremental_sync(client: GoodLinksClient, conn: sqlite3.Connection) -> dict[str, int]:
    """Fetch links added since the newest cached one. Cannot see edits or deletions
    to older links (the API has no modifiedAfter filter)."""
    row = conn.execute("SELECT MAX(added_at) FROM links").fetchone()
    newest = row[0] if row and row[0] else None
    if newest is None:
        return full_sync(client, conn)
    ts = db.now_iso()
    batch = list(client.iter_search(addedAfter=newest))
    db.upsert_links(conn, batch, ts)
    conn.commit()
    return {"synced": len(batch), "vanished": 0}
