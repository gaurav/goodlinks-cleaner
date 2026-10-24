# Working on goodlinks-cleaner

Python 3.12, managed with `uv`. Run things with `uv run glc …`, tests with `uv run pytest`,
lint with `uv run ruff check src tests`. The CLI is Typer; output is Rich tables.

## Shape of the code

`sync → SQLite cache → report → act`. `api.py` is the only module that talks to GoodLinks.
`reports.py` is read-only over the cache. `actions.py` holds everything that writes; each
action takes `apply: bool`, returns a `Result` of `Change`s, mirrors successful writes into
the cache and appends to the `actions` audit table. CLI commands in `cli.py` are thin.

Conventions that must hold for every new command:
- Dry run by default; `--apply` writes; `--limit N` caps how many items are touched.
- Deletes go to GoodLinks' trash via the API (never anything irreversible).
- Log every write to `actions` with before/after so it can be undone later.
- Do not add an LLM dependency here; classification work is a separate, opt-in PR.

## GoodLinks API gotchas (verified against GoodLinks 3.2, Oct 2026)

- Settings → API shows the address as `http://localhost:9428`; the versioned base is
  `/api/v1` and `config.py` appends it. Reads `GOODLINKS_API_ADDRESS` and
  `GOODLINKS_API_TOKEN` from `.env`. Never read the user's `.env` aloud; it holds the token.
- `limit` maxes at 1000 per page; paging 70k links takes about three minutes.
- No `modifiedAfter` filter. Incremental sync (`--since`) uses `addedAfter` and so misses
  edits and deletions; a full sync marks vanished ids as `deleted_at` instead of removing rows.
- `sort=shortest` puts null `wordCount` first. Pair it with `wordCountMin=1`.
- `GET /links/{id}/content?autoDownload=false` returns 404 when nothing is cached.
  `autoDownload=true` makes GoodLinks download the article (used by `glc fetch-missing`).
- The `goodlinks` CLI emits JSON with raw control characters in some titles, which breaks
  `jq` and strict `json.loads`. We call the HTTP API directly and still parse with
  `strict=False` in case the API shares the bug.
- `goodlinks help link search` prints the top-level help; use `goodlinks link search --help`.
- The app's data container is sandboxed (`Operation not permitted`); there is no SQLite
  shortcut around the API.

## Things learned from the data (so nobody re-derives them)

- Pocket stored redirect *targets* for dead sites while keeping the original titles, so
  dozens of different articles share one URL (57 FiveThirtyEight pieces at
  `abcnews.com/politics`, 182 at `avclub.com/`). `reports.is_collapsed` separates these
  from true duplicates by comparing titles; `glc collapsed` reports them. Deduplicating
  them would delete the only remaining trace of the article.
- `pocket.co` no longer resolves (Pocket shut down in 2025); `t.co` still does.
- Tracking-parameter blocklist in `urls.py` came from a census of query parameter names in
  this library; YouTube's `v` and Facebook's `u` are meaningful and must be kept.
- Median cached word count per host is a paywall detector: economist.com 114,
  washingtonpost.com 84, facebook.com 7.
- A 5xx, timeout or bot-wall when resolving a shortener is transient; only 404/410 and DNS
  failure are treated as dead.

## Testing against the real library

`tests/` use an in-memory fixture (`tests/conftest.py`). For a live check, run the dry run,
then the same command with `--limit 5 --apply`, then confirm with the official CLI, e.g.
`goodlinks link search --tag site/economist.com --limit 5` or `goodlinks link id <id>`
(a trashed link returns Not Found).
