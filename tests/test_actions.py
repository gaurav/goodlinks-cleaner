import httpx

from goodlinks_cleaner import actions, db, sync
from goodlinks_cleaner.api import GoodLinksClient


def test_is_dead_only_for_permanent_failures():
    assert actions._is_dead(404, None)
    assert actions._is_dead(410, None)
    assert actions._is_dead(None, "dns")
    assert not actions._is_dead(504, None)
    assert not actions._is_dead(None, "ReadTimeout")
    assert not actions._is_dead(None, "ConnectError")
    assert not actions._is_dead(200, None)


def test_resolve_url_follows_redirects_and_classifies_dns_failure():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "t.co":
            return httpx.Response(301, headers={"location": "https://example.org/article"})
        if req.url.host == "example.org":
            return httpx.Response(200)
        if req.url.host == "pocket.co":
            raise httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known")
        raise httpx.ConnectError("connection refused")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert actions.resolve_url(http, "https://t.co/abc") == ("https://example.org/article", 200, None)
    assert actions.resolve_url(http, "https://pocket.co/x") == (None, None, "dns")
    assert actions.resolve_url(http, "https://refused.example/") == (None, None, "ConnectError")


def test_full_sync_marks_vanished_ids_deleted(conn):
    def handler(req: httpx.Request) -> httpx.Response:
        # The API now only knows two of the fixture's eleven links.
        data = [
            {"id": "id001", "url": "https://www.economist.com/a/1?utm_source=x", "title": "Same article",
             "starred": False, "highlighted": False, "addedAt": "2020-01-02T00:00:00Z",
             "modifiedAt": "2020-03-01T00:00:00Z", "wordCount": 777},
            {"id": "id999", "url": "https://new.example/", "title": "New", "starred": False,
             "highlighted": False, "addedAt": "2026-01-01T00:00:00Z", "modifiedAt": "2026-01-01T00:00:00Z"},
        ]
        return httpx.Response(200, json={"data": data, "hasMore": False})

    client = GoodLinksClient("http://gl/api/v1", "tok")
    client._client = httpx.Client(base_url="http://gl/api/v1", transport=httpx.MockTransport(handler))
    result = sync.full_sync(client, conn)
    assert result == {"synced": 2, "vanished": 10}
    live = {r[0] for r in conn.execute("SELECT id FROM links WHERE deleted_at IS NULL")}
    assert live == {"id001", "id999"}
    assert conn.execute("SELECT word_count FROM links WHERE id='id001'").fetchone()[0] == 777
    assert conn.execute("SELECT COUNT(*) FROM links WHERE deleted_at IS NOT NULL").fetchone()[0] == 10


def test_random_pick_excludes_previous_picks(conn):
    # Only two unread links have 300 words or fewer; pick one, then the other must come up.
    first = actions.random_pick(conn, count=1, max_words=300)[0]
    actions.record_pick(None, conn, first, mark_read=False, pick_tag=None)
    second = actions.random_pick(conn, count=5, max_words=300)
    assert {r["id"] for r in second} == {"id007", "id009"} - {first["id"]}
    assert actions.random_pick(conn, count=1, max_words=300, exclude_picked=False)
    db.log_action(conn, second[0]["id"], "pick")
    assert actions.random_pick(conn, count=1, max_words=300) == []
