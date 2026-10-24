"""Thin client for the GoodLinks local HTTP API.

Reference: https://goodlinks.app/api/

Notes learned the hard way:

* The ``goodlinks`` CLI (and possibly the API) can emit JSON containing raw
  control characters inside strings, which strict parsers reject. Everything
  here is parsed with ``json.loads(..., strict=False)``.
* ``sort=shortest`` puts links with a null ``wordCount`` first, so pair it
  with ``wordCountMin=1`` if you want genuinely short articles.
* There is no ``modifiedAfter`` filter; incremental syncs can only use
  ``addedAfter``. Deletions are only detectable with a full pass.
* ``GET /links/{id}/content`` returns 404 when nothing is cached and
  ``autoDownload=false``. With ``autoDownload=true`` GoodLinks downloads the
  article as a side effect, which is sometimes exactly what we want.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

Link = dict[str, Any]

PAGE_SIZE = 1000  # API maximum


class GoodLinksError(RuntimeError):
    pass


class AuthError(GoodLinksError):
    pass


class NotFound(GoodLinksError):
    pass


def parse_json(text: str) -> Any:
    """json.loads that tolerates raw control characters inside strings."""
    return json.loads(text, strict=False)


class GoodLinksClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60.0):
        if not token:
            raise AuthError(
                "GOODLINKS_API_TOKEN is not set. Enable the API in GoodLinks → Settings → API, "
                "copy env.default to .env and paste the token there."
            )
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GoodLinksClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low level -----------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise GoodLinksError(
                f"Could not connect to GoodLinks at {self._client.base_url}. "
                "Is GoodLinks running with API access enabled?"
            ) from e
        if resp.status_code == 401:
            raise AuthError("GoodLinks rejected the API token (401). Check GOODLINKS_API_TOKEN.")
        if resp.status_code == 404:
            raise NotFound(f"{method} {path}: not found")
        if resp.status_code >= 400:
            raise GoodLinksError(f"{method} {path}: HTTP {resp.status_code}: {resp.text[:300]}")
        return resp

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._request(method, path, **kwargs)
        if resp.status_code == 204 or not resp.content:
            return None
        return parse_json(resp.text)

    # -- links ---------------------------------------------------------------

    def get_link(self, link_id: str) -> Link:
        return self._json("GET", f"/links/{link_id}")

    def get_link_by_url(self, url: str) -> Link | None:
        try:
            return self._json("GET", "/links", params={"url": url})
        except NotFound:
            return None

    def search_page(self, limit: int = PAGE_SIZE, offset: int = 0, **filters: Any) -> dict:
        """One page of GET /links. Filters use the API's camelCase names."""
        params = {"limit": limit, "offset": offset}
        params.update({k: v for k, v in filters.items() if v is not None})
        return self._json("GET", "/links", params=params)

    def list_page(self, list_name: str = "all", limit: int = PAGE_SIZE, offset: int = 0,
                  **filters: Any) -> dict:
        """One page of GET /lists/{list}. list_name in unread|read|starred|untagged|highlighted|all."""
        params = {"limit": limit, "offset": offset}
        params.update({k: v for k, v in filters.items() if v is not None})
        return self._json("GET", f"/lists/{list_name}", params=params)

    def iter_list(self, list_name: str = "all", **filters: Any) -> Iterator[Link]:
        offset = 0
        while True:
            page = self.list_page(list_name, PAGE_SIZE, offset, **filters)
            yield from page.get("data", [])
            if not page.get("hasMore"):
                return
            offset += PAGE_SIZE

    def iter_search(self, **filters: Any) -> Iterator[Link]:
        offset = 0
        while True:
            page = self.search_page(PAGE_SIZE, offset, **filters)
            yield from page.get("data", [])
            if not page.get("hasMore"):
                return
            offset += PAGE_SIZE

    def content(self, link_id: str, fmt: str = "plaintext", auto_download: bool = False) -> str | None:
        """Article text, or None if nothing is cached (and auto_download is False)."""
        try:
            resp = self._request(
                "GET",
                f"/links/{link_id}/content",
                params={"format": fmt, "autoDownload": "true" if auto_download else "false"},
            )
        except NotFound:
            return None
        return resp.text

    def add_link(self, url: str, **fields: Any) -> Link:
        """POST /links. Upserts by URL. fields: title, summary, tags, read, starred, addedAt."""
        body = {"url": url, **{k: v for k, v in fields.items() if v is not None}}
        return self._json("POST", "/links", json=body)

    def edit_link(self, link_id: str, **fields: Any) -> Link:
        """PATCH /links/{id}. fields: title, summary, starred, read, addedTags, removedTags, tags."""
        body = {k: v for k, v in fields.items() if v is not None}
        return self._json("PATCH", f"/links/{link_id}", json=body)

    def delete_links(self, link_ids: list[str]) -> None:
        """Move links to GoodLinks' trash (recoverable in the app)."""
        if not link_ids:
            return
        self._request("DELETE", "/links", params=[("id", i) for i in link_ids])

    # -- misc ----------------------------------------------------------------

    def lists(self) -> list[dict]:
        return self._json("GET", "/lists")

    def tags(self) -> list[str]:
        return self._json("GET", "/tags")
