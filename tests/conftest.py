import sqlite3

import pytest

from goodlinks_cleaner import db


def make_link(i: int, url: str, **kw) -> dict:
    link = {
        "id": f"id{i:03d}", "url": url, "title": kw.get("title", f"Title {i}"),
        "summary": None, "author": None, "tags": kw.get("tags"),
        "wordCount": kw.get("wordCount"), "starred": kw.get("starred", False),
        "highlighted": False, "addedAt": kw.get("addedAt", f"2020-01-{(i % 28) + 1:02d}T00:00:00Z"),
        "modifiedAt": "2020-02-01T00:00:00Z", "readAt": kw.get("readAt"),
    }
    return link


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.sqlite")
    links = [
        # true duplicates: same article, tracking junk differs; one is read and tagged
        make_link(1, "https://www.economist.com/a/1?utm_source=x", wordCount=500, title="Same article | The Economist"),
        make_link(2, "https://economist.com/a/1", wordCount=500, readAt="2021-01-01T00:00:00Z", tags=["econ"], title="Same article"),
        make_link(3, "https://www.economist.com/a/1?cid1=zz", wordCount=500, tags=["other"], title=None),
        # collapsed: same landing URL, different titles
        make_link(4, "https://abcnews.com/politics", title="Why militias | FiveThirtyEight"),
        make_link(5, "https://abcnews.com/politics", title="Gun deaths | FiveThirtyEight"),
        make_link(6, "https://abcnews.com/politics", title="Politics News | ABC"),
        # empties and shorts
        make_link(7, "https://article-14.com/post/x", wordCount=1),
        make_link(8, "https://nowhere.example/y", wordCount=None),
        make_link(9, "https://short.example/z", wordCount=120),
        make_link(10, "https://pocket.co/abc?cta=1"),
        make_link(11, "https://long.example/w", wordCount=4000),
    ]
    db.upsert_links(c, links, "2026-01-01T00:00:00Z")
    c.commit()
    return c
