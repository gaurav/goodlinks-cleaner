# goodlinks-cleaner

Command-line tools for cleaning up and triaging a large [GoodLinks](https://goodlinks.app/)
library through its local HTTP API. Written for a library of ~70,000 links imported from
Pocket, of which ~59,000 are unread.

Everything is mechanical: no LLMs are involved. Every command that changes the library is
a **dry run by default** and only writes when you pass `--apply`. Deletions go to GoodLinks'
trash, which is recoverable in the app.

## Setup

1. In GoodLinks, open Settings → API, turn on API access, and note the address and token.
2. Install [uv](https://docs.astral.sh/uv/), then:

   ```sh
   cp env.default .env      # paste the address and token into .env
   uv sync
   uv run glc ping           # "ok http://localhost:9428/api/v1: 6 lists, 267 tags"
   uv run glc sync           # copies the library into data/goodlinks.sqlite (~3 min for 70k links)
   ```

`glc sync` is the only command that reads the whole library. Reports run against the local
SQLite cache and are instant; actions write to GoodLinks and update the cache as they go.
Re-run `glc sync` after making changes in the GoodLinks app itself. `glc sync --since` only
fetches newly added links (the API cannot report edits or deletions, so do a full sync now
and then).

## Commands

Run `uv run glc --help`, or `uv run glc <command> --help` for options. All actions accept
`--limit N` to try a handful first and `--apply` to write.

### Understanding the library

| Command | What it shows |
|---|---|
| `glc stats` | Read/unread, word-count buckets, year added, duplicate counts |
| `glc domains --min 100` | Hosts ranked by links, with median word count (a tiny median means a paywall) |
| `glc export --format csv --out data/links.csv` | Dump the cache for duckdb, pandas or a spreadsheet |

### Cleaning up

| Command | What it does |
|---|---|
| `glc dupes` | Same article saved several times with different tracking parameters. Keeps the copy with the most signal (read, starred, tagged, highlighted), merges the others' tags into it, trashes the rest. |
| `glc collapsed` | Links whose URL now points at a shared landing page (a site died and Pocket stored the redirect target, keeping the title). Reported and tagged `status/collapsed`, never deduplicated, since the title is all that is left. |
| `glc empty` | Links with no or almost no article text, grouped by host so single-page apps and paywalls stand out. `--probe` asks GoodLinks for each cached text and classifies it; `--apply` tags them `status/empty`. |
| `glc fetch-missing` | Asks GoodLinks to download text for links it never fetched, so "no word count" becomes a real number. In practice only ~4% succeed: most of the 8,500 unread links without a word count are on dead or paywalled sites, so a `missing` result after this is a strong dead-link signal. |
| `glc shorteners` | Links on redirect services (pocket.co, t.co, bit.ly …). `--resolve` follows them; `--apply` replaces each with its destination (same tags, same date) and trashes the shortener. pocket.co no longer resolves at all; those get `status/dead-shortener`, or are trashed with `--delete-unresolvable`. |

### Making the backlog tractable

| Command | What it does |
|---|---|
| `glc tag-domains --min 50` | Tags every link from a big host `site/<host>`, so GoodLinks shows one `site` tag tree (all the Economist pieces in one place). |
| `glc short --max-words 300` | Genuinely short articles (word count between `--min-words` and `--max-words`); `--apply` tags them `length/short`. |
| `glc random` | PocketRocket-style: pick a random unread link and open it in the browser. Filters: `--max-words`, `--host`, `--tag`, `--year`. `--mark-read` marks it read, `--pick-tag picked` tags it, `--deeplink` opens it in GoodLinks instead. Picks are remembered so the same link is not offered twice (`--allow-repeat` to override). |

### Suggested order

```sh
uv run glc sync
uv run glc dupes --limit 20            # look, then
uv run glc dupes --apply
uv run glc shorteners --resolve --apply
uv run glc collapsed --apply
uv run glc empty --probe               # classify, then
uv run glc empty --probe --apply
uv run glc tag-domains --min 50 --apply
uv run glc short --apply
uv run glc random --max-words 800
```

## Audit log and recovery

Every write is appended to the `actions` table in `data/goodlinks.sqlite` with the state
before and after. Deleted links are in GoodLinks' trash. To see what has been done:

```sh
sqlite3 data/goodlinks.sqlite "SELECT action, COUNT(*) FROM actions GROUP BY action"
```

## Querying the cache directly

The cache is plain SQLite; `duckdb` reads it too:

```sh
duckdb -c "INSTALL sqlite; LOAD sqlite; ATTACH 'data/goodlinks.sqlite' AS gl (TYPE sqlite);
  SELECT host, count(*) n, median(word_count) FROM gl.links
  WHERE read_at IS NULL AND deleted_at IS NULL GROUP BY host ORDER BY n DESC LIMIT 20"
```

## Roadmap

- `glc check`: dead-link checker with Wayback Machine lookup (tag `status/dead` / `status/archived`).
- Clean up Pocket-era tags (`ifttt`, `safari-reading-list`, truncated feed names).
- Year and media tags (`year/2016`, `media/video`).
- Scheduled delivery of `glc random` picks by email.
- Optional, cheap-model classification using the cached article text.
