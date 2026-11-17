"""stage_citations: the footer contract, the turn vocabulary, chips and the spotlight."""
import stage_citations as sc


def _query(sid, ordinal, title, rows, tool_call_id=None, phase="exploration"):
    return {
        "id": sid, "ordinal": ordinal, "kind": "query", "title": title, "tool_call_id": tool_call_id,
        "payload": {"rows": rows, "row_count": len(rows)},
        "presentation": {"phase": phase},
    }


CANDIDATES = _query("s-cand", 3, "Candidates", [
    {"rank": i, "candidate": name, "market": "Fletcher, NC", "google_reviews": 649 + i, "status": "Unaware"}
    for i, name in enumerate(["Fletcher Great Smiles", "Lexington Endodontics", "Brown Valentine Dentistry", "Erena & Peterson", "My Smile Center"])
], tool_call_id="call_cands")
COVERAGE = _query("s-cov", 1, "Coverage", [{"location_count": 488, "place_match_status": "insufficient_address"}], tool_call_id="call_cov")
WIDE = _query("s-wide", 2, "Account Place", [{"sf_name": "Fletcher Great Smiles", "google_name": "Fletcher Great Smiles", "account_id": "0013p00002EuOTwAAN"}] + [
    {"sf_name": f"Practice {i}", "google_name": "Modern Periodontics", "account_id": f"0013p{i:012d}"} for i in range(29)
], tool_call_id="call_wide")

REPLY = (
    "Start with Fletcher Great Smiles, Lexington Endodontics, and Brown Valentine Dentistry. "
    "Hold Erena & Peterson: its Place match resolves to Modern Periodontics and is QA-flagged. "
    "I placed the five-candidate comparison on Stage."
)


def test_split_refs_strips_a_trailing_footer_only():
    clean, refs = sc.split_refs(REPLY + "\n\n[[stage: call_cands, \"Coverage\"]]\n")
    assert clean == REPLY
    assert refs == ["call_cands", "Coverage"]
    assert sc.split_refs("no footer here [[stage: x]] mid-text ends") == ("no footer here [[stage: x]] mid-text ends", [])
    assert sc.split_refs("") == ("", [])


def test_vocabulary_keeps_values_that_can_only_mean_one_thing():
    vocab = sc.index_values([CANDIDATES, COVERAGE, WIDE])
    assert "fletcher great smiles" in vocab
    assert vocab["fletcher great smiles"]["surfaces"] == {"s-cand": 1, "s-wide": 2}
    assert "candidate" in vocab["fletcher great smiles"]["columns"]
    assert "unaware" in vocab                      # Title-case single word, 7 chars
    assert "status" not in vocab                   # a column name is not a cell
    assert "0013p00002euotwaan" not in vocab       # hash-like ids never chip
    assert "insufficient_address" in vocab         # has no letters-only stoplist hit, contains no digit... single token
    assert "649" not in vocab and "650" not in vocab  # short numbers never chip


def test_chips_match_longest_first_with_word_boundaries():
    vocab = sc.index_values([CANDIDATES, COVERAGE, WIDE])
    values = sc.match_values(REPLY, vocab, [CANDIDATES, COVERAGE, WIDE])
    texts = {v["text"]: v for v in values}
    assert "Fletcher Great Smiles" in texts and "Erena & Peterson" in texts and "Modern Periodontics" in texts
    # the five-row comparison holds the name most specifically; the 30-row scan comes second
    assert texts["Fletcher Great Smiles"]["surfaces"] == ["s-cand", "s-wide"]
    assert texts["Modern Periodontics"]["surfaces"] == ["s-wide"]
    assert "Fletcher, NC" not in texts             # "Fletcher, NC" is not in the prose
    assert all(v["occurrences"] >= 1 for v in values)


def test_a_lone_title_case_word_must_match_the_cell_exactly():
    linked = _query("s-link", 4, "Contacts", [{"crm_contact": "Linked", "name": "Fletcher Great Smiles"}])
    vocab = sc.index_values([linked])
    prose = "Fletcher Great Smiles already has linked CRM contacts; FLETCHER GREAT SMILES is the strongest."
    values = sc.match_values(prose, vocab, [linked])
    texts = [v["text"] for v in values]
    assert "Linked" not in texts                      # lowercase "linked" in prose is just a word
    assert texts == ["Fletcher Great Smiles"]         # multi-word names match in any case
    assert values[0]["occurrences"] == 2 and values[0]["loose_case"] is True
    assert sc.match_values("Status is Linked.", vocab, [linked])[0]["text"] == "Linked"


def test_backfill_recomputes_receipts_from_an_older_version():
    turns = [{"id": "t", "status": "complete", "assistant_message": "x", "response_receipt": {"citations": {"version": 1}}}]
    seen = []
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            seen.append(sql)
            class R:
                def fetchall(self_inner): return []
            return R()
    assert sc.backfill(lambda: Conn(), turns) == 1
    assert turns[0]["response_receipt"]["citations"]["version"] == sc.VERSION


def test_numbers_chip_in_the_spellings_prose_uses():
    totals = _query("s-tot", 6, "Totals", [{"orders": 1400000, "revenue": 649321.5, "year": 2022, "small": 42, "rows": "23,450", "order_count": 1950, "value": 299889.62}])
    vocab = sc.index_values([totals])
    assert {"1400000", "1,400,000", "1.4m", "1m", "23.5k", "23k"} <= set(vocab)   # keys are lowercased; compact forms match either case
    assert "649321.5" in vocab and "649,321.5" in vocab and "23,450" in vocab and "23450" in vocab
    assert "2022" not in vocab and "42" not in vocab          # a year in a date-like column, and small counts, never chip
    assert "1,950" in vocab and "1950" in vocab               # a four-digit count in an orders column is a number
    assert "299,889.62" in vocab and "299889.62" in vocab     # floats get thousands separators too
    prose = "About 1.4M orders; the book is $649321.5, from 23,450 rows, not 21,400,000."
    texts = [v["text"] for v in sc.match_values(prose, vocab, [totals])]
    assert texts == ["649321.5", "23,450", "1.4M"]            # longest first; 1,400,000 inside 21,400,000 is not a hit
    cases = {v["text"]: v["loose_case"] for v in sc.match_values(prose, vocab, [totals])}
    assert cases == {"649321.5": False, "23,450": False, "1.4M": True}
    assert [v["text"] for v in sc.match_values("roughly 1.4m orders", vocab, [totals])] == ["1.4M"]


def test_explicit_refs_win_and_resolve_by_call_id_uuid_or_title():
    surfaces = [CANDIDATES, COVERAGE, WIDE]
    receipt = sc.build(REPLY, ["call_cov", "candidates", "nope"], surfaces)
    assert [s["surface_id"] for s in receipt["spotlight"]] == ["s-cov", "s-cand"]
    assert all(s["reason"] == "explicit" for s in receipt["spotlight"])
    assert receipt["unresolved_refs"] == ["nope"]


def test_refs_resolve_by_title_or_batch_key_under_any_spelling():
    batched = dict(_query("s-batch", 5, "Complete Order Value", [{"store": "Online"}], tool_call_id="call_b-3"))
    batched["source"] = {"args": {"sql": "SELECT 1", "batch": {"queries": "{\"complete_order_value\": \"SELECT 1\", \"window\": \"SELECT 2\"}"}}}
    resolved, unresolved = sc.resolve_refs(["complete_order_value", "complete-order-value", "COMPLETE ORDER VALUE", "window", "nope"], [batched, COVERAGE])
    assert resolved == ["s-batch"]                    # all spellings collapse to one surface
    assert unresolved == ["window", "nope"]           # "window" is a sibling statement's key, not this surface's


def test_an_unresolved_footer_still_falls_back_to_inference():
    receipt = sc.build(REPLY, ["something_she_made_up"], [COVERAGE, WIDE, CANDIDATES])
    assert receipt["unresolved_refs"] == ["something_she_made_up"]
    assert receipt["spotlight"][0]["surface_id"] == "s-cand" and receipt["spotlight"][0]["reason"] == "inferred"


def test_inference_picks_the_query_whose_rows_carry_the_prose():
    receipt = sc.build(REPLY, [], [COVERAGE, WIDE, CANDIDATES])
    assert receipt["spotlight"] == [{"surface_id": "s-cand", "reason": "inferred", "score": 4.0}]


def test_inference_stays_quiet_without_a_pointer_or_when_something_was_published():
    assert sc.build("Start with Fletcher Great Smiles.", [], [CANDIDATES, COVERAGE])["spotlight"] == []
    board = {"id": "s-board", "ordinal": 9, "kind": "board", "title": "Comparison", "payload": {}, "presentation": {"phase": "published"}}
    assert sc.build(REPLY, [], [CANDIDATES, board])["spotlight"] == []


def test_a_lone_query_is_the_pointer_even_without_matches():
    receipt = sc.build("The numbers are on the Stage.", [], [COVERAGE])
    assert receipt["spotlight"] == [{"surface_id": "s-cov", "reason": "inferred", "score": 1.0}]
    assert sc.build("The numbers are on the Stage.", [], [COVERAGE, WIDE])["spotlight"] == []


def test_backfill_skips_turns_that_already_have_citations_or_are_not_done():
    calls = []
    def conn_factory():
        raise AssertionError("must not touch the database for skipped turns")
    turns = [
        {"id": "t1", "status": "running", "assistant_message": "x on the Stage", "response_receipt": {}},
        {"id": "t2", "status": "complete", "assistant_message": "", "response_receipt": {}},
        {"id": "t3", "status": "complete", "assistant_message": "done", "response_receipt": {"citations": {"version": 1}}},
    ]
    assert sc.backfill(conn_factory, turns) == 0


def test_complete_turn_keeps_its_writes_on_one_open_connection(monkeypatch):
    """2026-09-20: a patch closed the completion's connection early and the
    session-timestamp update ran on a closed connection; every complete turn
    then failed with "the connection is closed"."""
    import calliope

    executed = []

    class Conn:
        def __init__(self):
            self.closed = False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.closed = True
            return False
        def execute(self, sql, params=None):
            if self.closed:
                raise RuntimeError("the connection is closed")
            executed.append(sql.split()[0] + " " + sql.split()[1] if sql.startswith("UPDATE") else sql[:24])
            class Result:
                def fetchone(self_inner): return None
                def fetchall(self_inner): return []
            return Result()

    opened = []
    def conn_factory():
        conn = Conn()
        opened.append(conn)
        return conn

    monkeypatch.setattr(calliope.calliope_pages, "finish_unsaved_turn", lambda *a, **k: None)
    monkeypatch.setattr(sc, "record", lambda cf, turn_id, text, refs: executed.append(f"record {text!r} {refs}"))
    clean = calliope._complete_turn(conn_factory, "turn-1", "Look on the Stage.\n\n[[stage: call_x]]", "hm-1", "complete")
    assert clean == "Look on the Stage."
    assert executed == [
        "UPDATE rvbbit.calliope_turns",
        "UPDATE rvbbit.calliope_sessions",
        "record 'Look on the Stage.' ['call_x']",
    ]
    assert all(conn.closed for conn in opened)


def test_click_ledger_route_ddl_and_ui_hooks_exist():
    """The chips and spotlights are only worth keeping if people use them: the
    server keeps an owner-scoped click ledger and the UI posts to it."""
    from pathlib import Path
    server = (Path(__file__).parent / "calliope.py").read_text()
    ui = (Path(__file__).parent / "calliope" / "calliope.js").read_text()
    assert 'custom_route("/api/calliope/citations/events", methods=["POST"])' in server
    assert "CREATE TABLE IF NOT EXISTS rvbbit.calliope_citation_events" in server
    assert "CREATE OR REPLACE VIEW rvbbit.calliope_citation_event_stats" in server
    assert "_ensure_citation_events_schema(conn)" in server
    for kind in ("chip_click", "chip_menu_open", "chip_menu_pick", "ledger_open"):
        assert f'postCitationEvent("{kind}"' in ui, kind
    assert "function transcriptChipGroups(" in ui and "chipByWord" in ui
    assert 'answer && !draft ? withValueChips(' in ui
