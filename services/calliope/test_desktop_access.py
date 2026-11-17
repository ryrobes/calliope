from contextlib import nullcontext
import uuid
import pytest
import desktop_access as access


class Database:
    def __init__(self, desktop=None, current=None, allow_seed=True):
        self.desktop, self.current, self.allow_seed = desktop, current, allow_seed
        self.queries = []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def transaction(self): return nullcontext()
    def execute(self, sql, params=()):
        self.queries.append(sql)
        if sql.startswith("INSERT INTO rvbbit.calliope_desktops"):
            if self.desktop is None and self.allow_seed:
                self.desktop = {"id": uuid.uuid4(), "name": "default"}
            self.result = None
        elif sql.startswith("SELECT * FROM rvbbit.calliope_desktops"):
            self.result = self.desktop
        elif sql.startswith("SELECT * FROM rvbbit.calliope_desktop_runs"):
            self.result = self.current
        elif sql.startswith("INSERT INTO rvbbit.calliope_desktop_runs"):
            self.current = self.result = dict(zip(("id", "desktop_id", "session_id", "requester", "goal"), params))
        else: raise AssertionError(sql)
        return self
    def fetchone(self): return self.result


def test_open_recovers_missing_seed_and_reuses_same_run(monkeypatch):
    db = Database()
    monkeypatch.setattr(access.calliope_access, "require_participant", lambda *a: None)
    sid = str(uuid.uuid4())
    first = access.open_run(lambda: db, sid, "owner", "Test desktop")
    assert first["desktop_id"] == str(db.desktop["id"])
    assert access.open_run(lambda: db, sid, "owner", "Continue") == first
    assert sum(q.startswith("INSERT INTO rvbbit.calliope_desktop_runs") for q in db.queries) == 1


def test_seed_does_not_change_existing_desktop_or_run_owner(monkeypatch):
    desktop = {"id": uuid.uuid4(), "name": "default"}
    db = Database(desktop, {"requester": "someone-else"})
    monkeypatch.setattr(access.calliope_access, "require_participant", lambda *a: None)
    with pytest.raises(access.DesktopError) as exc:
        access.open_run(lambda: db, str(uuid.uuid4()), "owner", "Test")
    assert exc.value.code == "COMPUTER_IDENTITY_CHANGED"
    assert db.desktop is desktop
    assert not any(q.startswith("INSERT INTO rvbbit.calliope_desktop_runs") for q in db.queries)


def test_unauthorized_open_never_seeds(monkeypatch):
    db = Database()
    def denied(*args): raise PermissionError("not a participant")
    monkeypatch.setattr(access.calliope_access, "require_participant", denied)
    with pytest.raises(PermissionError):
        access.open_run(lambda: db, str(uuid.uuid4()), "stranger", "Test")
    assert db.queries == []


def test_unavailable_seed_has_actionable_error(monkeypatch):
    db = Database(allow_seed=False)
    monkeypatch.setattr(access.calliope_access, "require_participant", lambda *a: None)
    with pytest.raises(access.DesktopError) as exc:
        access.open_run(lambda: db, str(uuid.uuid4()), "owner", "Test")
    assert exc.value.code == "DESKTOP_NOT_CONFIGURED" and exc.value.status == 503
