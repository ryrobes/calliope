"""Contracts for Calliope's stable observation ledger."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import calliope_observations as observations


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _RecordingConn:
    def __init__(self, rows=None, backfill_rows=None):
        self.calls = []
        self.rows = rows or []
        self.backfill_rows = backfill_rows or []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "WHERE d.stable_observation_id IS NULL" in sql:
            return _Result(rows=self.backfill_rows)
        if "INSERT INTO rvbbit.calliope_observations" in sql:
            return _Result({
                "id": params[0], "fingerprint": params[1],
                "scope_kind": params[2], "owner_email": params[3],
            })
        if "FROM rvbbit.calliope_observations o" in sql:
            return _Result(rows=self.rows)
        return _Result()


def _sample(local_id="observation:recent:1"):
    return {
        "id": local_id,
        "fingerprint": "weekly-enrollment-retry",
        "kind": "friction",
        "title": "Enrollment reporting keeps retrying",
        "summary": "Several governed enrollment investigations retried the same query family.",
        "evidence": [{"id": "tool:1", "kind": "activity"}],
        "entities": ["Enrollment"],
        "signal_count": 4,
        "confidence": 0.84,
    }


def test_cycle_observations_get_stable_ids_and_idempotent_source_events():
    conn = _RecordingConn()
    seen_at = datetime(2026, 8, 25, 7, 30, tzinfo=timezone.utc)
    first = observations.persist_cycle_observations(
        conn,
        cycle_id="00000000-0000-4000-8000-000000000001",
        scope_kind="company",
        owner_email="ignored@example.com",
        observations=[_sample()],
        observed_at=seen_at,
    )
    second_conn = _RecordingConn()
    second = observations.persist_cycle_observations(
        second_conn,
        cycle_id="00000000-0000-4000-8000-000000000001",
        scope_kind="company",
        owner_email=None,
        observations=[_sample()],
        observed_at=seen_at,
    )

    assert first == second
    assert first["observation:recent:1"] == observations.stable_id(
        "company", None, "weekly-enrollment-retry"
    )
    upsert_sql, upsert_params = next(
        call for call in conn.calls if "INSERT INTO rvbbit.calliope_observations" in call[0]
    )
    assert "latest_cycle_id IS DISTINCT FROM EXCLUDED.latest_cycle_id" in upsert_sql
    assert upsert_params[2:4] == ("company", None)
    event_sql, event_params = next(
        call for call in conn.calls if "'observed','dream_cycle'" in call[0]
    )
    assert "ON CONFLICT (observation_id,event_kind,source_kind,source_ref) DO NOTHING" in event_sql
    assert event_params[1] == "00000000-0000-4000-8000-000000000001"
    assert json.loads(event_params[2])["signal_count"] == 4


def test_personal_observation_identity_is_owner_scoped():
    company = observations.stable_id("company", None, "same")
    ada = observations.stable_id("personal", "ada@example.com", "same")
    grace = observations.stable_id("personal", "grace@example.com", "same")

    assert len({company, ada, grace}) == 3
    assert observations.normalize_scope("personal", " ADA@EXAMPLE.COM ") == (
        "personal", "ada@example.com"
    )


def test_recurring_resolved_observation_reopens_with_a_revisioned_event():
    class RecurringConn(_RecordingConn):
        def execute(self, sql, params=()):
            if "SELECT state,state_revision" in sql:
                self.calls.append((sql, params))
                return _Result({"state": "resolved", "state_revision": 2})
            result = super().execute(sql, params)
            if "INSERT INTO rvbbit.calliope_observations" in sql:
                result._row["state_revision"] = 3
            return result

    conn = RecurringConn()
    observations.persist_cycle_observations(
        conn,
        cycle_id="00000000-0000-4000-8000-000000000009",
        scope_kind="company",
        owner_email=None,
        observations=[_sample()],
    )

    reopen_sql, reopen_params = next(
        call for call in conn.calls if "'reopened','dream_cycle'" in call[0]
    )
    assert "ON CONFLICT" in reopen_sql
    assert json.loads(reopen_params[2])["state_revision"] == 3


def test_legacy_dream_observations_backfill_oldest_first_and_link_once():
    seen_at = datetime(2026, 8, 24, 7, 30, tzinfo=timezone.utc)
    cycle_id = "00000000-0000-4000-8000-000000000021"
    dream_observation_id = "00000000-0000-4000-8000-000000000022"
    conn = _RecordingConn(backfill_rows=[{
        "dream_observation_id": dream_observation_id,
        "cycle_id": cycle_id,
        "fingerprint": "weekly-enrollment-retry",
        "kind": "friction",
        "title": "Enrollment reporting keeps retrying",
        "summary": "The same governed investigation needed repeated work.",
        "evidence": [],
        "entities": ["Enrollment"],
        "signal_count": 2,
        "confidence": 0.75,
        "scope_kind": "personal",
        "owner_email": "ada@example.com",
        "observed_at": seen_at,
    }])

    assert observations.backfill_dream_observations(conn) == 1

    select_sql, _ = conn.calls[0]
    assert "ORDER BY c.started_at,d.created_at,d.id" in select_sql
    update_sql, update_params = conn.calls[-1]
    assert "SET stable_observation_id=%s::uuid" in update_sql
    assert update_params == (
        observations.stable_id("personal", "ada@example.com", "weekly-enrollment-retry"),
        dream_observation_id,
    )


def test_snapshot_combines_company_with_only_the_callers_personal_rows():
    conn = _RecordingConn(rows=[{
        "id": "00000000-0000-4000-8000-000000000011",
        "fingerprint": "topic",
        "scope_kind": "company",
        "kind": "change",
        "title": "A company change",
        "summary": "A grounded summary.",
        "evidence": [],
        "entities": ["Enrollment"],
        "signal_count": 2,
        "recurrence_count": 3,
        "confidence": 0.8,
        "urgency": 0.6,
        "state": "active",
        "sensitivity": "internal",
        "ranking_audience_key": "ada@example.com",
        "ranking_relevance": 0.91,
        "ranking_priority": 0.83,
        "ranking_surface_role": "lead",
        "ranking_factors": {"relevance": 0.91},
        "ranking_reason_codes": ["profile_match"],
        "ranking_ranker": "clover",
        "ranking_model": "rvbbit.clover_relevance",
        "ranking_ranked_at": datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc),
    }])

    result = observations.snapshot(
        lambda: conn, "Ada@Example.com", scope_kind="all", limit=999,
    )

    sql, params = conn.calls[0]
    assert "o.scope_kind='company' OR (o.scope_kind='personal' AND o.owner_email=%s)" in sql
    assert "LEFT JOIN LATERAL" in sql
    assert params == (
        "ada@example.com", "ada@example.com", "active", "ada@example.com", 200,
    )
    assert result["counts"] == {"company": 1}
    assert result["observations"][0]["entities"] == ["Enrollment"]
    assert result["observations"][0]["ranking"]["surface_role"] == "lead"
    assert result["ranking"]["ranked_count"] == 1


def test_ranking_combines_semantic_relevance_with_receipted_deterministic_factors():
    item = {
        "kind": "friction",
        "recurrence_count": 4,
        "signal_count": 6,
        "confidence": 0.9,
        "urgency": 0.8,
        "last_observed_at": datetime.now(timezone.utc),
        "viewer_feedback": "following",
    }

    priority, role, factors, reasons = observations._ranking(
        item, 0.92, used_clover=True,
    )

    assert priority > 0.8
    assert role == "lead"
    assert factors["relevance"] == 0.92
    assert factors["feedback_delta"] == 0.15
    assert "profile_match" in reasons
    assert "recurring" in reasons
    assert "feedback:following" in reasons


def test_saturated_semantic_scores_are_calibrated_and_surface_roles_stay_selective():
    calibrated = observations._calibrate_relevance({
        "low": 0.9965,
        "middle": 0.9998,
        "high": 1.0,
    })

    assert calibrated["low"] < 0.4
    assert 0.64 < calibrated["middle"] < 0.66
    assert calibrated["high"] == 1.0
    assert [
        observations._surface_role(position, 112) for position in (0, 5, 6, 23, 24)
    ] == ["lead", "lead", "supporting", "supporting", "background"]
    assert observations._surface_role(80, 112, "following") == "lead"
    assert observations._surface_role(0, 112, "not_relevant") == "background"


def test_ranking_refresh_isolates_one_audience_failure(monkeypatch):
    class LockConn(_RecordingConn):
        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            if "pg_try_advisory_lock" in sql:
                return _Result({"acquired": True})
            if "FROM rvbbit.calliope_user_dossiers" in sql:
                return _Result(rows=[
                    {"owner_email": "ada@example.com"},
                    {"owner_email": "grace@example.com"},
                ])
            return _Result()

    lock_conn = LockConn()
    calls = []

    def fake_refresh(_conn_factory, audience):
        calls.append(audience)
        if audience == "ada@example.com":
            raise RuntimeError("profile unavailable")
        return {"audience": audience, "ranked": 2}

    monkeypatch.setattr(observations, "refresh_rankings", fake_refresh)
    receipt = observations.refresh_all_rankings(lambda: lock_conn)

    assert calls == ["company", "ada@example.com", "grace@example.com"]
    assert receipt["audiences"][1] == {
        "audience": "personal",
        "audience_key": "ada@example.com",
        "error": "RuntimeError: profile unavailable",
    }
    assert "pg_advisory_unlock" in lock_conn.calls[-1][0]


def test_schema_migration_and_service_contract_ship_together():
    backend = (HERE / "calliope.py").read_text(encoding="utf-8")
    dreams = (HERE / "calliope_dreams.py").read_text(encoding="utf-8")
    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    migration = (
        ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations"
        / "0337_calliope_observations.sql"
    ).read_text(encoding="utf-8")
    ranking_migration = (
        ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations"
        / "0338_calliope_observation_feedback_rankings.sql"
    ).read_text(encoding="utf-8")
    server = (HERE / "server.py").read_text(encoding="utf-8")
    registry = (
        ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS rvbbit.calliope_observations" in migration
    assert "CREATE TABLE IF NOT EXISTS rvbbit.calliope_observation_events" in migration
    assert "stable_observation_id" in migration
    assert '"0337_calliope_observations"' in registry
    assert "CREATE TABLE IF NOT EXISTS rvbbit.calliope_observation_feedback" in ranking_migration
    assert "CREATE TABLE IF NOT EXISTS rvbbit.calliope_observation_rankings" in ranking_migration
    assert '"0338_calliope_observation_feedback_rankings"' in registry
    assert "conn.execute(calliope_observations.DDL)" in backend
    assert '"/api/calliope/observations"' in backend
    assert '"/api/calliope/observations/{observation_id}/feedback"' in backend
    assert '"/api/calliope/observations/{observation_id}/state"' in backend
    assert "backfill_dream_observations(conn)" in backend
    assert "persist_cycle_observations" in dreams
    assert "calliope_observations.py" in dockerfile
    assert "calliope.start_observation_worker(_conn)" in server
