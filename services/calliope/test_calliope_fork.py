"""Fork-from-here: the rewriting and the note a fork carries (DB contract: tests/test_calliope_fork_db.py)."""
import calliope_fork as fork


def test_rewriter_changes_whole_slugs_and_copied_ids_only():
    old_id, new_id = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    rewrite = fork.Rewriter({"regional-orders": "regional-orders-f1"}, {old_id: new_id})
    text = ("Open /d/regional-orders (surface " + old_id + "); regional-orders-v1-2026.png and "
            "big-regional-orders stay; regional_orders too.")
    assert rewrite.text(text) == ("Open /d/regional-orders-f1 (surface " + new_id + "); regional-orders-v1-2026.png and "
                                  "big-regional-orders stay; regional_orders too.")
    assert rewrite.json({"slug": "regional-orders", "ids": [old_id], "n": 3}) == {
        "slug": "regional-orders-f1", "ids": [new_id], "n": 3}
    assert fork.Rewriter({}, {}).text("regional-orders") == "regional-orders"


def test_titles_and_the_fork_note():
    assert fork.title_for({"title": "Regional orders room"}) == "Regional orders room (fork)"
    assert fork.title_for({"title": "Regional orders room (fork)"}) == "Regional orders room (fork)"
    note = fork.context({"session_id": "s", "session_title": "Room", "turn_ordinal": 4,
                         "artifacts": [{"from": "a", "slug": "a-f1"}], "not_copied": ["b"]})
    assert note.startswith("[CALLIOPE FORK — verified]") and "- a → a-f1" in note and "b" in note
    assert fork.context(None) == "" and fork.context({}) == ""
