from goodlinks_cleaner import actions, db, reports


def test_stats(conn):
    st = reports.stats(conn)
    assert st["total"] == 11
    assert st["read"] == 1
    assert st["dupe_groups"] == 2  # economist group + abcnews group share norm_url


def test_dupe_groups_keep_read_tagged_copy_and_exclude_collapsed(conn):
    groups = reports.dupe_groups(conn)
    assert len(groups) == 1
    keeper, *losers = groups[0]
    assert keeper["id"] == "id002"
    assert {r["id"] for r in losers} == {"id001", "id003"}


def test_collapsed_groups(conn):
    groups = reports.collapsed_groups(conn)
    assert len(groups) == 1
    assert {r["id"] for r in groups[0]} == {"id004", "id005", "id006"}


def test_norm_title_strips_site_suffix():
    assert reports.norm_title("A very long article title about something | FiveThirtyEight") == \
        reports.norm_title("A Very Long Article Title About Something - FiveThirtyEight")


def test_empty_and_short_candidates(conn):
    empties = {r["id"] for r in reports.empty_candidates(conn, 20)}
    assert empties == {"id004", "id005", "id006", "id007", "id008", "id010"}
    shorts = [r["id"] for r in reports.short_candidates(conn, 21, 300)]
    assert shorts == ["id009"]


def test_dedupe_dry_run_merges_tags_and_deletes(conn):
    res = actions.dedupe(None, conn, apply=False)
    kinds = [(c.action, c.link_id) for c in res.changes]
    assert ("add_tags", "id002") in kinds  # 'other' merged into keeper
    assert ("delete", "id001") in kinds and ("delete", "id003") in kinds
    assert all(not c.done for c in res.changes)
    # dry run must not touch the cache
    assert conn.execute("SELECT COUNT(*) FROM links WHERE deleted_at IS NOT NULL").fetchone()[0] == 0


def test_tag_domains_skips_shorteners_and_existing(conn):
    db.set_tags(conn, "id001", ["site/economist.com"])
    res = actions.tag_domains(None, conn, min_count=2, apply=False)
    tags = {c.detail for c in res.changes}
    assert tags == {"site/economist.com", "site/abcnews.com"}
    assert res.skipped == 1
    assert "site/pocket.co" not in tags


def test_random_pick_filters(conn):
    rows = actions.random_pick(conn, count=5, max_words=300)
    assert {r["id"] for r in rows} == {"id007", "id009"}
    rows = actions.random_pick(conn, count=1, host_filter="www.long.example")
    assert rows[0]["id"] == "id011"


def test_prose_of_strips_header_and_nav():
    text = ("GHOSTFACE ASKS\n\n- https://www.mcsweeneys.net/articles/x\n- Jonathan Zeller\n\n"
            "* Internet Tendency [https://www.mcsweeneys.net/]\n * The Store [https://store.x/]\n\n"
            "Real prose starts here and continues.\nSecond paragraph.")
    assert actions.prose_of(text) == "Real prose starts here and continues.\nSecond paragraph."
