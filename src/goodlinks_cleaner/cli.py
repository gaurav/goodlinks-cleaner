"""``glc`` command line interface."""

from __future__ import annotations

import sqlite3
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from goodlinks_cleaner import __version__, db, reports
from goodlinks_cleaner import sync as sync_mod
from goodlinks_cleaner.api import GoodLinksClient, GoodLinksError
from goodlinks_cleaner.config import Settings

app = typer.Typer(
    help="Clean up and triage a GoodLinks library via its local HTTP API.",
    no_args_is_help=True,
)
console = Console()
err = Console(stderr=True)


def client() -> GoodLinksClient:
    s = Settings.load()
    return GoodLinksClient(s.api_url, s.api_token)


def cache() -> sqlite3.Connection:
    s = Settings.load()
    conn = db.connect(s.db_path)
    if conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0:
        fail("the cache is empty; run `glc sync` first")
    return conn


def fail(msg: str) -> None:
    err.print(f"[red]error:[/red] {msg}")
    raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print the version."""
    console.print(__version__)


@app.command()
def ping() -> None:
    """Check that the GoodLinks API is reachable and the token works."""
    s = Settings.load()
    try:
        with GoodLinksClient(s.api_url, s.api_token) as c:
            lists = c.lists()
            tags = c.tags()
    except GoodLinksError as e:
        fail(str(e))
    console.print(f"[green]ok[/green] {s.api_url}: {len(lists)} lists, {len(tags)} tags")
    console.print(f"cache: {s.db_path}")


@app.command()
def sync(
    since: Annotated[bool, typer.Option("--since", help="Only fetch links added since the newest cached one (fast; misses edits and deletions).")] = False,
) -> None:
    """Copy the library into the local SQLite cache (~70 s for 70k links)."""
    s = Settings.load()
    conn = db.connect(s.db_path)
    try:
        with GoodLinksClient(s.api_url, s.api_token) as c:
            if since:
                result = sync_mod.incremental_sync(c, conn)
            else:
                with console.status("syncing...") as status:
                    result = sync_mod.full_sync(c, conn, progress=lambda n: status.update(f"syncing... {n:,} links"))
    except GoodLinksError as e:
        fail(str(e))
    console.print(f"synced {result['synced']:,} links; {result['vanished']:,} vanished since last full sync")
    console.print(f"cache: {s.db_path}")


@app.command()
def stats() -> None:
    """Summary counts: read/unread, word-count buckets, year added, duplicates."""
    conn = cache()
    st = reports.stats(conn)
    t = Table(show_header=False, box=None)
    t.add_column(style="bold"); t.add_column(justify="right")
    for k in ("total", "unread", "read", "starred", "highlighted", "tagged", "untitled", "distinct_hosts"):
        t.add_row(k.replace("_", " "), f"{st[k]:,}")
    t.add_row("duplicate groups / extra copies", f"{st['dupe_groups']:,} / {st['dupe_extra']:,}")
    t.add_row("last sync", str(st["last_sync"]))
    console.print(t)

    t = Table(title="unread by word count", box=None)
    t.add_column("bucket"); t.add_column("links", justify="right")
    for label, n in st["unread_by_words"]:
        t.add_row(label, f"{n:,}")
    console.print(t)

    t = Table(title="unread by year added", box=None)
    t.add_column("year"); t.add_column("links", justify="right")
    for y, n in st["unread_by_year"]:
        t.add_row(y, f"{n:,}")
    console.print(t)


@app.command()
def domains(
    min_count: Annotated[int, typer.Option("--min", help="Only hosts with at least this many links.")] = 20,
    unread_only: Annotated[bool, typer.Option("--unread-only")] = False,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """Hosts ranked by number of links, with median word count."""
    conn = cache()
    rows = reports.domains(conn, min_count, unread_only, limit)
    t = Table(box=None)
    for col, just in (("host", "left"), ("total", "right"), ("unread", "right"),
                      ("median words", "right"), ("no word count", "right")):
        t.add_column(col, justify=just)
    for r in rows:
        t.add_row(r.host, f"{r.total:,}", f"{r.unread:,}",
                  "-" if r.median_words is None else f"{r.median_words:,}", f"{r.no_words:,}")
    console.print(t)
    console.print(f"{len(rows)} hosts")


if __name__ == "__main__":
    app()
