"""``glc`` command line interface."""

from __future__ import annotations

import sqlite3
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from goodlinks_cleaner import __version__, actions, db, reports
from goodlinks_cleaner import sync as sync_mod
from goodlinks_cleaner.api import GoodLinksClient, GoodLinksError
from goodlinks_cleaner.config import Settings
from goodlinks_cleaner.urls import host as host_of
from goodlinks_cleaner.urls import normalize_url

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


ApplyOpt = Annotated[bool, typer.Option("--apply", help="Actually write to GoodLinks. Without it, only report what would change.")]
LimitOpt = Annotated[int | None, typer.Option("--limit", help="Touch at most this many items (handy for a first run).")]


def _short(text: str | None, n: int = 60) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def print_changes(res: actions.Result, apply: bool, show: int = 40) -> None:
    t = Table(box=None)
    t.add_column("action"); t.add_column("title"); t.add_column("detail")
    if apply:
        t.add_column("ok")
    for ch in res.changes[:show]:
        row = [ch.action, _short(ch.title or ch.url), _short(ch.detail, 70)]
        if apply:
            row.append("[red]" + _short(ch.error, 40) + "[/red]" if ch.error else "[green]✓[/green]")
        t.add_row(*row)
    console.print(t)
    if len(res.changes) > show:
        console.print(f"… and {len(res.changes) - show:,} more")
    verb = "applied" if apply else "would apply"
    extra = f"; {res.failed} failed" if apply and res.failed else ""
    skipped = f"; {res.skipped:,} already done" if res.skipped else ""
    console.print(f"[bold]{verb} {res.done if apply else len(res.changes):,} change(s){extra}{skipped}[/bold]")
    if not apply and res.changes:
        console.print("[dim]dry run; add --apply to write these to GoodLinks[/dim]")


def _progress(label: str):
    status = console.status(label)
    status.start()

    def cb(i: int, n: int) -> None:
        status.update(f"{label} {i:,}/{n:,}")

    return status, cb


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


@app.command()
def reindex() -> None:
    """Recompute host and normalised URL for every cached link (after changing urls.py)."""
    conn = cache()
    rows = conn.execute("SELECT id, url FROM links").fetchall()
    conn.executemany("UPDATE links SET host=?, norm_url=? WHERE id=?",
                     [(host_of(r["url"]), normalize_url(r["url"]), r["id"]) for r in rows])
    conn.commit()
    console.print(f"reindexed {len(rows):,} links")


@app.command("tag-domains")
def tag_domains(
    min_count: Annotated[int, typer.Option("--min", help="Tag hosts with at least this many links.")] = 50,
    prefix: Annotated[str, typer.Option("--prefix", help="Tag prefix; a trailing slash makes a hierarchical tag.")] = "site/",
    hosts: Annotated[list[str] | None, typer.Option("--host", help="Only these hosts (repeatable); overrides --min.")] = None,
    unread_only: Annotated[bool, typer.Option("--unread-only")] = False,
    limit: LimitOpt = None,
    apply: ApplyOpt = False,
) -> None:
    """Tag every link with site/<host> for hosts that have many links."""
    conn = cache()
    c = client() if apply else None
    status, cb = _progress("tagging")
    try:
        res = actions.tag_domains(c, conn, min_count=min_count, prefix=prefix, hosts=hosts,
                                  unread_only=unread_only, limit=limit, apply=apply, progress=cb)
    finally:
        status.stop()
        if c:
            c.close()
    by_tag: dict[str, int] = {}
    for ch in res.changes:
        by_tag[ch.detail] = by_tag.get(ch.detail, 0) + 1
    t = Table(box=None); t.add_column("tag"); t.add_column("links", justify="right")
    for tag, n in sorted(by_tag.items(), key=lambda kv: -kv[1]):
        t.add_row(tag, f"{n:,}")
    console.print(t)
    total = sum(by_tag.values())
    if apply:
        failed = [ch for ch in res.changes if ch.error]
        console.print(f"[bold]tagged {res.done:,} link(s)[/bold]; {len(failed)} failed; {res.skipped:,} already tagged")
        for ch in failed[:20]:
            console.print(f"  [red]{_short(ch.url, 70)}: {ch.error}[/red]")
    else:
        console.print(f"[bold]would tag {total:,} link(s)[/bold]; {res.skipped:,} already tagged")
        console.print("[dim]dry run; add --apply to write these to GoodLinks[/dim]")


@app.command()
def dupes(
    limit: LimitOpt = None,
    apply: ApplyOpt = False,
    show: Annotated[int, typer.Option("--show")] = 40,
) -> None:
    """Find links with the same normalised URL; keep one, merge tags, trash the rest."""
    conn = cache()
    c = client() if apply else None
    status, cb = _progress("deduplicating")
    try:
        res = actions.dedupe(c, conn, limit=limit, apply=apply, progress=cb)
    finally:
        status.stop()
        if c:
            c.close()
    print_changes(res, apply, show)


@app.command()
def collapsed(
    tag: Annotated[str, typer.Option("--tag")] = "status/collapsed",
    show: Annotated[int, typer.Option("--show")] = 30,
    limit: LimitOpt = None,
    apply: ApplyOpt = False,
) -> None:
    """Links whose URL now points at a shared landing page (site died, Pocket kept the redirect).

    Only the title survives, so these are reported and tagged, never deduplicated.
    Recovering the articles means searching the title in the Wayback Machine.
    """
    conn = cache()
    groups = reports.collapsed_groups(conn)
    t = Table(box=None)
    t.add_column("links", justify="right"); t.add_column("landing url"); t.add_column("example titles")
    for g in sorted(groups, key=len, reverse=True)[:show]:
        titles = sorted({_short(r["title"], 45) for r in g if r["title"]})[:2]
        t.add_row(str(len(g)), _short(g[0]["norm_url"], 60), " / ".join(titles))
    console.print(t)
    rows = [r for g in groups for r in g]
    console.print(f"{len(rows):,} link(s) in {len(groups):,} collapsed group(s)")
    c = client() if apply else None
    status, cb = _progress("tagging")
    try:
        res = actions.tag_rows(c, conn, rows, tag, limit=limit, apply=apply, progress=cb)
    finally:
        status.stop()
        if c:
            c.close()
    if apply:
        print_changes(res, apply, show=10)
    else:
        console.print(f"[dim]--apply tags them {tag} ({res.skipped:,} already tagged)[/dim]")


@app.command()
def empty(
    max_words: Annotated[int, typer.Option("--max-words", help="Word counts at or below this count as empty.")] = 20,
    probe: Annotated[bool, typer.Option("--probe", help="Ask GoodLinks for each candidate's cached text and classify it.")] = False,
    by_host: Annotated[bool, typer.Option("--by-host/--list", help="Summarise per host (default) or list links.")] = True,
    include_read: Annotated[bool, typer.Option("--include-read")] = False,
    tag: Annotated[str, typer.Option("--tag")] = "status/empty",
    limit: LimitOpt = None,
    apply: ApplyOpt = False,
) -> None:
    """Links with no or almost no article text (SPAs, paywalls, dead pages)."""
    conn = cache()
    rows = reports.empty_candidates(conn, max_words, unread_only=not include_read)
    if probe:
        todo = [r for r in rows if r["probe_status"] is None]
        if limit is not None:
            # A limited probe should sample across hosts, not just the first host alphabetically.
            import random as _random

            _random.shuffle(todo)
        with client() as c:
            status, cb = _progress("probing content")
            try:
                counts = actions.probe_content(c, conn, todo, limit=limit, progress=cb)
            finally:
                status.stop()
        console.print(f"probed: {counts}")
        rows = reports.empty_candidates(conn, max_words, unread_only=not include_read)
    if by_host and not apply:
        hosts = reports.empty_by_host(conn, max_words, unread_only=not include_read, min_count=2)
        t = Table(box=None)
        for col in ("host", "links", "no words", "≤max", "probed missing", "probed empty", "probed cached"):
            t.add_column(col, justify="right" if col != "host" else "left")
        for r in hosts[:60]:
            t.add_row(r["host"], f"{r['total']:,}", f"{r['no_words']:,}", f"{r['tiny']:,}",
                      f"{r['probed_missing'] or 0:,}", f"{r['probed_empty'] or 0:,}", f"{r['probed_cached'] or 0:,}")
        console.print(t)
        console.print(f"{len(rows):,} candidate link(s) across {len(hosts):,} hosts with ≥2; use --list to see links, --apply to tag them {tag}")
        return
    if not apply:
        t = Table(box=None); t.add_column("words", justify="right"); t.add_column("probe"); t.add_column("title"); t.add_column("url")
        for r in rows[: limit or 60]:
            t.add_row("-" if r["word_count"] is None else str(r["word_count"]), r["probe_status"] or "",
                      _short(r["title"], 50), _short(r["url"], 70))
        console.print(t)
        console.print(f"{len(rows):,} candidate link(s)")
        return
    # When probing happened, only tag what the probe confirmed; otherwise tag by word count.
    if probe:
        rows = [r for r in rows if r["probe_status"] in ("missing", "empty")]
    with client() as c:
        status, cb = _progress("tagging")
        try:
            res = actions.tag_rows(c, conn, rows, tag, limit=limit, apply=True, progress=cb)
        finally:
            status.stop()
    print_changes(res, True)


@app.command("fetch-missing")
def fetch_missing(
    limit: LimitOpt = 200,
    include_read: Annotated[bool, typer.Option("--include-read")] = False,
) -> None:
    """Ask GoodLinks to download article text for links that have no word count.

    This is the one command whose side effect is a download by GoodLinks itself;
    run `glc sync` afterwards to see which word counts got filled in.
    """
    conn = cache()
    rows = [r for r in reports.empty_candidates(conn, 0, unread_only=not include_read)
            if r["word_count"] is None and r["probe_status"] is None]
    import random as _random

    _random.shuffle(rows)  # sample across hosts rather than alphabetically
    console.print(f"{len(rows):,} links without word count and not yet probed; fetching up to {limit}")
    with client() as c:
        status, cb = _progress("downloading via GoodLinks")
        try:
            counts = actions.probe_content(c, conn, rows, auto_download=True, limit=limit, progress=cb)
        finally:
            status.stop()
    console.print(f"result: {counts}")
    console.print("[dim]run `glc sync` to refresh word counts[/dim]")


@app.command()
def shorteners(
    resolve: Annotated[bool, typer.Option("--resolve", help="Follow redirects over the network.")] = False,
    hosts: Annotated[list[str] | None, typer.Option("--host", help="Only these shortener hosts (repeatable).")] = None,
    delete_unresolvable: Annotated[bool, typer.Option("--delete-unresolvable", help="Trash shorteners that no longer resolve instead of tagging them.")] = False,
    limit: LimitOpt = None,
    apply: ApplyOpt = False,
) -> None:
    """Links on redirect shorteners (pocket.co, t.co, bit.ly…). Resolve them to their destination."""
    conn = cache()
    rows = actions.shortener_rows(conn, hosts)
    by_host: dict[str, int] = {}
    for r in rows:
        by_host[r["host"]] = by_host.get(r["host"], 0) + 1
    t = Table(box=None); t.add_column("host"); t.add_column("links", justify="right")
    for h, n in sorted(by_host.items(), key=lambda kv: -kv[1]):
        t.add_row(h, f"{n:,}")
    console.print(t)
    console.print(f"{len(rows):,} shortener link(s)")
    if not resolve:
        console.print("[dim]add --resolve to follow the redirects (network); --apply to rewrite/tag[/dim]")
        return
    c = client() if apply else None
    status, cb = _progress("resolving")
    try:
        res, resolved = actions.resolve_shorteners(c, conn, rows, limit=limit, apply=apply,
                                                   delete_unresolvable=delete_unresolvable, progress=cb)
    finally:
        status.stop()
        if c:
            c.close()
    ok = sum(1 for r in resolved if r.final_url and r.status and r.status < 400 and r.final_url != r.row["url"])
    console.print(f"resolved {ok:,} of {len(resolved):,}")
    print_changes(res, apply)


@app.command()
def short(
    max_words: Annotated[int, typer.Option("--max-words")] = 300,
    min_words: Annotated[int, typer.Option("--min-words", help="Lower bound, so 'empty' pages are not called short.")] = 21,
    tag: Annotated[str, typer.Option("--tag")] = "length/short",
    include_read: Annotated[bool, typer.Option("--include-read")] = False,
    limit: LimitOpt = None,
    apply: ApplyOpt = False,
) -> None:
    """Genuinely short articles; optionally tag them so they are easy to find in GoodLinks."""
    conn = cache()
    rows = reports.short_candidates(conn, min_words, max_words, unread_only=not include_read)
    c = client() if apply else None
    status, cb = _progress("tagging")
    try:
        res = actions.tag_rows(c, conn, rows, tag, limit=limit, apply=apply, progress=cb)
    finally:
        status.stop()
        if c:
            c.close()
    console.print(f"{len(rows):,} unread link(s) with {min_words}–{max_words} words")
    print_changes(res, apply, show=15)


@app.command()
def random(
    count: Annotated[int, typer.Option("--count", "-n")] = 1,
    max_words: Annotated[int | None, typer.Option("--max-words")] = None,
    min_words: Annotated[int | None, typer.Option("--min-words")] = None,
    host: Annotated[str | None, typer.Option("--host")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    year: Annotated[int | None, typer.Option("--year", help="Year the link was added.")] = None,
    mark_read: Annotated[bool, typer.Option("--mark-read", help="Mark the pick as read in GoodLinks.")] = False,
    pick_tag: Annotated[str | None, typer.Option("--pick-tag", help="Tag to add to picks (e.g. picked). Default: none.")] = None,
    open_: Annotated[bool, typer.Option("--open/--no-open", help="Open the link in the default browser.")] = True,
    deeplink: Annotated[bool, typer.Option("--deeplink", help="Open in GoodLinks instead of the browser.")] = False,
    repeat: Annotated[bool, typer.Option("--allow-repeat", help="Allow links picked before.")] = False,
) -> None:
    """Pick a random unread link and open it (PocketRocket-style)."""
    import subprocess

    conn = cache()
    rows = actions.random_pick(conn, count=count, max_words=max_words, min_words=min_words,
                               host_filter=host, tag=tag, year=year, exclude_picked=not repeat)
    if not rows:
        fail("nothing matches those filters")
    c = client() if (mark_read or pick_tag) else None
    try:
        for r in rows:
            words = "-" if r["word_count"] is None else f"{r['word_count']:,} words"
            console.print(f"[bold]{r['title'] or '(untitled)'}[/bold]  [dim]{words} · added {r['added_at'][:10]}[/dim]")
            if r["summary"]:
                console.print(f"  {_short(r['summary'], 200)}")
            console.print(f"  {r['url']}")
            try:
                actions.record_pick(c, conn, r, mark_read=mark_read, pick_tag=pick_tag)
            except GoodLinksError as e:
                err.print(f"[red]could not update GoodLinks:[/red] {e}")
            if open_:
                target = f"goodlinks://x-callback-url/open?id={r['id']}" if deeplink else r["url"]
                subprocess.run(["open", target], check=False)
    finally:
        if c:
            c.close()


@app.command()
def export(
    fmt: Annotated[str, typer.Option("--format", help="jsonl or csv")] = "jsonl",
    out: Annotated[str | None, typer.Option("--out", help="File path; default stdout.")] = None,
    include_deleted: Annotated[bool, typer.Option("--include-deleted")] = False,
) -> None:
    """Dump the cached links for duckdb, pandas or a spreadsheet."""
    import csv
    import json
    import sys

    conn = cache()
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    rows = conn.execute(f"SELECT * FROM links {where} ORDER BY added_at").fetchall()
    def dump(fh) -> None:
        if fmt == "csv":
            w = csv.writer(fh)
            w.writerow(rows[0].keys() if rows else [])
            for r in rows:
                w.writerow(list(r))
        else:
            for r in rows:
                fh.write(json.dumps(dict(r)) + "\n")

    if out:
        with open(out, "w", newline="") as fh:
            dump(fh)
    else:
        dump(sys.stdout)
    if out:
        console.print(f"wrote {len(rows):,} links to {out}")


if __name__ == "__main__":
    app()
