"""Claims extraction: the gate, quote grounding, the retry and the eval scoring."""
import json

import memory_claims as claims

WORDS = "For the record: we have 14 stores in Germany, and Lena Fischer runs the German region."


def _claim(**overrides):
    return {"quote": "we have 14 stores in Germany", "claim": "The company has 14 stores in Germany.",
            "subject": "stores in Germany", "value": "14", "unit": "stores", "as_of": None,
            "kind": "business_fact", "durable": True, "check_with": "data", "sensitivity": "normal", **overrides}


def test_a_claim_must_quote_the_person_word_for_word():
    assert claims.validate_claim(_claim(), WORDS) == []
    # Curly quotes, case and spacing don't make a quote untrue.
    assert claims.validate_claim(_claim(quote="We have  14 stores in Germany"), WORDS) == []
    assert claims.validate_claim(_claim(quote="we own 14 shops in Germany"), WORDS)[0].startswith("quote")
    assert claims.validate_claim(_claim(kind="rumour"), WORDS) == ["kind must be one of " + ", ".join(claims.KINDS)]
    assert claims.validate_claim(_claim(as_of="last week"), WORDS)[0].startswith("as_of")
    assert claims.validate_claim({"quote": "x"}, WORDS)[0].startswith("missing fields")


def test_a_bad_answer_goes_back_once_and_failing_claims_are_dropped():
    answers = iter([
        json.dumps({"claims": [_claim(quote="we own 14 shops")]}),
        json.dumps({"claims": [_claim(), _claim(quote="still not quoted", claim="Made up.")]}),
    ])
    seen = []

    def complete(messages, schema, name):
        seen.append(messages)
        assert schema is claims.CLAIM_SCHEMA and name == "claims"
        return next(answers)

    result = claims.extract({"speaker": "Alice Park", "words": WORDS, "at": "2026-10-20"}, complete=complete)
    assert result["attempts"] == 2 and result["dropped"] == 1
    assert [c["claim"] for c in result["claims"]] == ["The company has 14 stores in Germany."]
    assert result["claims"][0]["speaker"] == "Alice Park"
    assert "is not in the person's message word for word" in seen[1][-1]["content"]


class _Jev:
    def __init__(self, choice, p):
        self.answer = {"choice": choice, "probabilities": {choice: p}}

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return self

    def execute(self, sql, params=None):
        answer = self.answer
        return type("R", (), {"fetchone": lambda _: {"d": {"answers": {"result": answer}}}})()


def test_the_gate_keeps_remembering_and_unsure_skips():
    turn = {"speaker": "Sam", "words": "Since our fiscal year starts in February, what was Q1 revenue?"}
    assert claims.gate(_Jev("remember", 0.9), turn)["keep"]
    assert claims.gate(_Jev("skip", 0.6), turn)["keep"]  # unsure: the extractor may still return nothing
    assert not claims.gate(_Jev("skip", 0.95), turn)["keep"]
    assert not claims.gate(_Jev("remember", 0.3), turn)["keep"]


def test_the_eval_counts_found_missing_and_extra_claims(monkeypatch):
    cases = [{"speaker": "Alice", "words": WORDS, "claims": [{"mentions": ["14", "Germany"]},
                                                            {"mentions": ["Lena Fischer"]}]},
             {"speaker": "Bob", "words": "thanks!"}]
    results = {WORDS: {"gate": {"keep": True}, "claims": [_claim(), _claim(claim="Bob likes tea.", subject="tea")],
                       "dropped": 0},
               "thanks!": {"gate": {"keep": False}, "claims": []}}
    monkeypatch.setattr(claims, "process", lambda factory, turn, complete=None: results[turn["words"]])
    report = claims.evaluate(None, cases)
    assert (report["gate_right"], report["expected"], report["found"], report["extra"]) == (2, 2, 1, 1)
    assert report["misses"][0]["expected"] == {"mentions": ["Lena Fischer"]}


# -- writing (real database) -----------------------------------------------------
import datetime as dt  # noqa: E402
import uuid  # noqa: E402

import pytest  # noqa: E402

import application_teams  # noqa: E402
import calliope_access  # noqa: E402
from test_workspace_sql import db, OWNER, SESSION  # noqa: E402,F401


def test_shared_claims_merge_and_sensitive_ones_stay_with_their_audience(monkeypatch):
    audience = [f"person:{OWNER}", "person:bob@example.com"]
    monkeypatch.delenv("CALLIOPE_MEMORY_SCOPE", raising=False)
    tags, scopes = claims.tags_for(_claim(), audience)
    assert tags == ["calliope:processed", "calliope:claim", "kind:business_fact"] and scopes == "shared"
    tags, scopes = claims.tags_for(_claim(sensitivity="restricted"), audience)
    assert tags[-3:] == ["calliope:sensitive", *audience] and scopes == [["calliope:sensitive", *audience]]
    monkeypatch.setenv("CALLIOPE_MEMORY_SCOPE", "audience")
    tags, scopes = claims.tags_for(_claim(), audience)
    assert tags[3:] == ["calliope:scoped", *audience] and scopes == [["calliope:scoped", *audience]]
    # Scoped memory already carries the audience; sensitive adds only the marker.
    assert claims.tags_for(_claim(sensitivity="personal"), audience)[0][-1] == "calliope:sensitive"
    assert claims.claim_text({**_claim(), "speaker": "Bob Rivera"}, "2026-10-20 09:00:00+00") == (
        'Bob Rivera said on 2026-10-20: "we have 14 stores in Germany"\nThe company has 14 stores in Germany.')


@pytest.fixture
def claims_db(db):
    with db() as c:
        c.execute("""ALTER TABLE rvbbit.calliope_sessions ADD COLUMN title text DEFAULT 'Orders',
          ADD COLUMN channel text DEFAULT 'web', ADD COLUMN archived boolean DEFAULT false,
          ADD COLUMN created_at timestamptz DEFAULT now(), ADD COLUMN updated_at timestamptz DEFAULT now();
          CREATE TABLE rvbbit.settings(key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz);
          CREATE TABLE rvbbit.calliope_turns(id uuid, session_id uuid, turn_kind text, status text,
            user_message text, assistant_message text, author_email text, author_display_name text,
            created_at timestamptz, completed_at timestamptz)""")
        c.execute(application_teams.DDL)
        c.execute(calliope_access.DDL)
    return db


def _turn(db, words, *, minutes_ago=5, kind="chat", status="complete"):
    at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)
    turn = uuid.uuid4()
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_turns VALUES(%s,%s,%s,%s,%s,'Noted.',%s,'Bob Rivera',%s,%s)",
                  (turn, SESSION, kind, status, words, OWNER, at, at))
    return str(turn)


def _started(db, *, minutes_ago):
    with db() as c:
        c.execute("INSERT INTO rvbbit.settings VALUES(%s, jsonb_build_object('at', now() - make_interval(mins => %s)), now()) "
                  "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (claims.SINCE_KEY, minutes_ago))


def test_the_worker_writes_each_finished_turn_once_from_when_it_started(claims_db, monkeypatch):
    _turn(claims_db, "old news", minutes_ago=120)
    # The first run starts from now: earlier turns were remembered by Hindsight.
    assert claims.tick(claims_db)["turns"] == 0
    _started(claims_db, minutes_ago=60)
    stores = _turn(claims_db, WORDS)
    _turn(claims_db, "thanks!")
    _turn(claims_db, "still running", status="running")
    _turn(claims_db, "the room chatting", kind="note")
    written = []
    monkeypatch.setattr(claims, "process", lambda factory, turn, complete=None: (
        {"gate": {"keep": True}, "claims": [{**_claim(), "speaker": turn["speaker"]}], "dropped": 1}
        if turn["words"] == WORDS else {"gate": {"keep": False, "choice": "skip"}, "claims": []}))
    monkeypatch.setattr(claims, "write", lambda turn, found, audience: written.append((turn["id"], audience)) or [
        f"claim:{turn['id']}:{n}" for n in range(len(found))])
    assert claims.tick(claims_db) == {"turns": 2, "kept": 1, "claims": 1, "errors": 0}
    assert written == [(uuid.UUID(stores), [f"person:{OWNER}"])]
    with claims_db() as c:
        row = c.execute("SELECT * FROM rvbbit.calliope_claims").fetchone()
        ledger = c.execute("SELECT count(*) AS n, sum(dropped) AS dropped FROM rvbbit.calliope_memory_turns").fetchone()
    assert (row["speaker"], row["speaker_email"], row["hindsight_document"]) == (
        "Bob Rivera", OWNER, f"claim:{stores}:0")
    assert row["claim"]["quote"] == "we have 14 stores in Germany" and row["audience"] == [f"person:{OWNER}"]
    assert (ledger["n"], ledger["dropped"]) == (2, 1)
    assert claims.tick(claims_db)["turns"] == 0


def test_failed_turns_retry_a_few_times_and_rewrites_start_clean(claims_db, monkeypatch):
    _started(claims_db, minutes_ago=60)
    turn = _turn(claims_db, WORDS)
    monkeypatch.setattr(claims, "process", lambda *a, **k: 1 / 0)
    for _ in range(claims.MAX_ATTEMPTS):
        assert claims.tick(claims_db)["errors"] == 1
    assert claims.tick(claims_db)["turns"] == 0
    with claims_db() as c:
        row = c.execute("SELECT attempts, error FROM rvbbit.calliope_memory_turns WHERE turn_id=%s", (turn,)).fetchone()
    assert row["attempts"] == claims.MAX_ATTEMPTS and row["error"].startswith("ZeroDivisionError")
    # A turn written again first drops what it wrote before, observations and all.
    forgotten = []
    monkeypatch.setattr(claims, "forget", forgotten.append)
    monkeypatch.setattr(claims, "process", lambda *a, **k: {"gate": {"keep": True}, "claims": [_claim(speaker="Bob")]})
    monkeypatch.setattr(claims, "write", lambda turn, found, audience: ["claim:x:0"])
    with claims_db() as c:
        c.execute("UPDATE rvbbit.calliope_memory_turns SET attempts=1 WHERE turn_id=%s", (turn,))
    assert claims.tick(claims_db)["claims"] == 1 and [str(t) for t in forgotten] == [turn]


def test_the_flag_hands_memory_back_to_hindsight(monkeypatch):
    monkeypatch.delenv("CALLIOPE_MEMORY_EXTRACTOR", raising=False)
    assert claims.writes()
    monkeypatch.setenv("CALLIOPE_MEMORY_EXTRACTOR", "hindsight")
    assert not claims.writes() and not claims.start_worker(lambda: None)
