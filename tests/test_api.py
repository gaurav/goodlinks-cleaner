import json

import httpx
import pytest

from goodlinks_cleaner.api import AuthError, GoodLinksClient, parse_json


def test_parse_json_tolerates_control_chars():
    assert parse_json('{"title": "a\tb\x01c"}')["title"] == "a\tb\x01c"


def test_client_requires_token():
    with pytest.raises(AuthError):
        GoodLinksClient("http://x/api/v1", "")


def make_client(handler) -> GoodLinksClient:
    c = GoodLinksClient("http://gl/api/v1", "tok")
    c._client = httpx.Client(base_url="http://gl/api/v1", transport=httpx.MockTransport(handler),
                             headers={"Authorization": "Bearer tok"})
    return c


def test_iter_list_pages_until_has_more_false():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(dict(req.url.params))
        offset = int(req.url.params["offset"])
        if offset == 0:
            return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}], "hasMore": True})
        return httpx.Response(200, json={"data": [{"id": "c"}], "hasMore": False})

    c = make_client(handler)
    assert [x["id"] for x in c.iter_list("all")] == ["a", "b", "c"]
    assert calls[0]["limit"] == "1000" and calls[1]["offset"] == "1000"


def test_content_404_is_none_and_auth_error():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/content"):
            assert req.url.params["autoDownload"] == "false"
            return httpx.Response(404)
        return httpx.Response(401)

    c = make_client(handler)
    assert c.content("x") is None
    with pytest.raises(AuthError):
        c.lists()


def test_delete_repeats_id_param():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["q"] = str(req.url.query, "utf8")
        return httpx.Response(204)

    make_client(handler).delete_links(["a", "b"])
    assert seen["q"] == "id=a&id=b"


def test_edit_link_body():
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert body == {"addedTags": ["x"]}
        return httpx.Response(200, json={"id": "1", "tags": ["x"]})

    assert make_client(handler).edit_link("1", addedTags=["x"], title=None)["tags"] == ["x"]
