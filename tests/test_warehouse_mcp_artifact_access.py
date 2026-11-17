"""End-to-end contracts for owner-managed artifact viewers and archive."""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from conftest import RVBBIT_DSN


def _load_artifact_access():
    root = Path(__file__).resolve().parents[1] / "services" / "calliope"
    # artifact_access imports the sibling application_teams module.
    import sys

    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        f"artifact_access_{uuid.uuid4().hex}", root / "artifact_access.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _BorrowedConnection:
    """Let service helpers share one test transaction without committing it."""

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


def test_artifact_viewers_archive_and_one_time_grandfathering(rvbbit):
    rvbbit.execute("SELECT rvbbit.migrate()")
    access = _load_artifact_access()
    owner = f"owner-{uuid.uuid4().hex[:8]}@example.invalid"
    member = f"member-{uuid.uuid4().hex[:8]}@example.invalid"
    person = f"person-{uuid.uuid4().hex[:8]}@example.invalid"
    slug = f"artifact-access-{uuid.uuid4().hex[:10]}"
    auth = {
        "actor": owner,
        "subject": owner,
        "mode": "pytest",
        "delegated": False,
    }

    conn = psycopg.connect(RVBBIT_DSN, row_factory=dict_row)
    factory = lambda: _BorrowedConnection(conn)
    try:
        for email, name in (
            (owner, "Artifact owner"),
            (member, "Team viewer"),
            (person, "Individual viewer"),
        ):
            conn.execute(
                "INSERT INTO rvbbit.application_principals(email,display_name) "
                "VALUES (%s,%s)",
                (email, name),
            )
        team_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO rvbbit.teams "
            "(id,slug,name,description,created_by,updated_by) "
            "VALUES (%s::uuid,%s,%s,'pytest',%s,%s)",
            (team_id, f"access-{uuid.uuid4().hex[:8]}", f"Access {uuid.uuid4().hex[:8]}", owner, owner),
        )
        conn.execute(
            "INSERT INTO rvbbit.team_members(team_id,principal_email,added_by) "
            "VALUES (%s::uuid,%s,%s)",
            (team_id, member, owner),
        )
        artifact_id = conn.execute(
            "INSERT INTO rvbbit.dashboards(slug,name,owner_email) "
            "VALUES (%s,'Artifact access pytest',%s) RETURNING id",
            (slug, owner),
        ).fetchone()["id"]

        # Re-running the service bootstrap must not grant a newly published row
        # to Everyone. Only rows present at the first 0254 install are public.
        conn.execute(access.DDL)
        assert conn.execute(
            "SELECT count(*) AS n FROM rvbbit.artifact_view_grants WHERE artifact_id=%s",
            (artifact_id,),
        ).fetchone()["n"] == 0

        initial = access.get_access(factory, auth, slug)
        assert initial["private"] and initial["summary"] == "Only you"
        assert access.can_view(conn, slug, owner)
        assert not access.can_view(conn, slug, member)
        people = access.search_people(factory, auth, "viewer", 20)["people"]
        assert owner not in {item["email"] for item in people}
        assert {member, person}.issubset({item["email"] for item in people})

        shared = access.replace_access(
            factory,
            auth,
            slug,
            1,
            team_ids=[team_id],
            people=[person, owner],  # owner is implicit, never a redundant grant
        )
        assert shared["artifact"]["access_revision"] == 2
        assert access.can_view(conn, slug, member)
        assert access.can_view(conn, slug, person)
        assert [item["email"] for item in shared["grants"]["people"]] == [person]

        everyone_id = str(conn.execute(
            "SELECT id FROM rvbbit.teams WHERE system_key='everyone'"
        ).fetchone()["id"])
        try:
            access.replace_access(
                factory, auth, slug, 2, team_ids=[everyone_id], people=[]
            )
            raise AssertionError("Everyone was added without explicit confirmation")
        except access.ArtifactAccessError as exc:
            assert exc.code == "EVERYONE_CONFIRMATION_REQUIRED"
        everyone = access.replace_access(
            factory,
            auth,
            slug,
            2,
            team_ids=[everyone_id],
            people=[],
            confirm_everyone=True,
        )
        assert everyone["everyone"]

        archived = access.set_archived(factory, auth, slug, 3, True)
        assert archived["artifact"]["archived"]
        assert not access.can_view(conn, slug, owner)
        assert access.can_view(conn, slug, owner, include_archived=True)
        assert not access.can_view(conn, slug, member, include_archived=True)

        restored = access.set_archived(factory, auth, slug, 4, False)
        assert not restored["artifact"]["archived"]
        assert restored["everyone"] and access.can_view(conn, slug, member)
        assert len(restored["events"]) == 4

        try:
            access.replace_access(factory, auth, slug, 4, team_ids=[], people=[])
            raise AssertionError("A stale sharing revision was accepted")
        except access.ArtifactAccessError as exc:
            assert exc.code == "ARTIFACT_ACCESS_CONFLICT"
    finally:
        conn.rollback()
        conn.close()


def test_admins_manage_edit_and_transfer_any_artifact(rvbbit):
    """Admins manage every artifact, private ones included, and can give it a new
    owner; lists still show them only what is shared; every admin act is receipted."""
    access = _load_artifact_access()
    tag = uuid.uuid4().hex[:8]
    owner, admin, heir, outsider = (f"{who}-{tag}@example.invalid" for who in ("owner", "admin", "heir", "outsider"))
    slug, second = f"admin-access-{tag}", f"admin-access-{tag}-old"

    def auth(who):
        return {"actor": who, "subject": who, "mode": "pytest", "delegated": False}

    def event(result, kind, **after):
        # One test transaction gives every receipt the same timestamp; find it by content.
        found = [e for e in result["events"] if e["event_type"] == kind
                 and all(e["after_state"].get(k) == v for k, v in after.items())]
        assert len(found) == 1, (kind, after, result["events"])
        return found[0]

    def code(thunk):
        try:
            thunk()
        except access.ArtifactAccessError as exc:
            return exc.code
        raise AssertionError("expected an ArtifactAccessError")

    conn = psycopg.connect(RVBBIT_DSN, row_factory=dict_row)
    factory = lambda: _BorrowedConnection(conn)
    try:
        for email in (owner, admin, heir, outsider):
            conn.execute("INSERT INTO rvbbit.application_principals(email,display_name) VALUES (%s,%s)", (email, email))
        admins = conn.execute("SELECT id FROM rvbbit.teams WHERE system_key='admins'").fetchone()["id"]
        conn.execute("INSERT INTO rvbbit.team_members(team_id,principal_email,added_by) VALUES (%s,%s,%s)",
                     (admins, admin, admin))
        for s, archived in ((slug, False), (second, True)):
            conn.execute("INSERT INTO rvbbit.dashboards(slug,name,owner_email,archived) VALUES (%s,%s,%s,%s)",
                         (s, "Private " + s, owner, archived))

        # Private to its owner: an outsider learns nothing, an admin can open and change it.
        assert not access.can_view(conn, slug, outsider)
        assert code(lambda: access.require_editor(conn, slug, auth(outsider))) == "ARTIFACT_NOT_FOUND"
        assert access.can_view(conn, slug, admin) and access.require_view(conn, slug, admin)["slug"] == slug
        assert access.require_editor(conn, slug, auth(admin))["editor_role"] == "admin"
        assert access.is_admin(conn, admin) and not access.is_admin(conn, owner)
        # ...but lists keep their rule: the SQL check the Gallery uses still says no.
        assert not conn.execute("SELECT rvbbit.artifact_can_view(id,%s,false) AS ok FROM rvbbit.dashboards WHERE slug=%s",
                                (admin, slug)).fetchone()["ok"]

        snapshot = access.get_access(factory, auth(admin), slug)
        assert snapshot["manager_role"] == "admin" and snapshot["is_admin"]
        assert snapshot["summary"] == "Only its owner"
        shared = access.replace_access(factory, auth(admin), slug, 1, team_ids=[], people=[outsider, owner, admin])
        assert {p["email"] for p in shared["grants"]["people"]} == {outsider, admin}  # never the owner
        assert event(shared, "sharing_updated")["detail"]["acting_as"] == "admin"
        archived = access.set_archived(factory, auth(admin), slug, 2, True)
        assert event(archived, "artifact_archived")["detail"]["acting_as"] == "admin"
        assert not access.can_view(conn, slug, admin) and access.can_view(conn, slug, admin, include_archived=True)
        access.set_archived(factory, auth(admin), slug, 3, False)

        # Ownership: admins need a reason and an observed person; the old owner keeps view access.
        assert code(lambda: access.transfer_owner(factory, auth(admin), slug, heir, 4)) == "REASON_REQUIRED"
        assert code(lambda: access.transfer_owner(factory, auth(admin), slug, f"ghost-{tag}@example.invalid", 4,
                                                  "left")) == "PERSON_NOT_OBSERVED"
        assert code(lambda: access.transfer_owner(factory, auth(outsider), slug, outsider, 4, "mine now")) == "ARTIFACT_NOT_FOUND"
        moved = access.transfer_owner(factory, auth(admin), slug, heir, 4, "Owner left; work moves to the heir")
        assert moved["changed"] and moved["artifact"]["owner_email"] == heir
        receipt = event(moved, "owner_transferred", owner=heir)
        assert receipt["detail"]["acting_as"] == "admin" and receipt["before_state"]["owner"] == owner
        assert receipt["detail"]["previous_owner_view"] == "kept" and access.can_view(conn, slug, owner)
        assert code(lambda: access.require_editor(conn, slug, auth(owner))) == "ARTIFACT_OWNER_ONLY"
        # An owner may hand their own artifact on without giving a reason.
        back = access.transfer_owner(factory, auth(heir), slug, owner, 5)
        assert back["artifact"]["owner_email"] == owner
        assert "acting_as" not in event(back, "owner_transferred", owner=owner)["detail"]

        # Someone left: an admin lists, then moves, everything they owned (archived too).
        assert code(lambda: access.transfer_all(factory, auth(heir), owner, heir)) == "ARTIFACT_ADMIN_REQUIRED"
        listed = access.transfer_all(factory, auth(admin), owner, heir)
        assert listed["dry_run"] and listed["count"] == 2 and not listed["changed"]
        assert conn.execute("SELECT count(*) AS n FROM rvbbit.dashboards WHERE owner_email=%s", (owner,)).fetchone()["n"] == 2
        assert code(lambda: access.transfer_all(factory, auth(admin), owner, heir, "", dry_run=False)) == "REASON_REQUIRED"
        done = access.transfer_all(factory, auth(admin), owner, heir, "Owner left", dry_run=False)
        assert done["changed"] and {a["slug"] for a in done["artifacts"]} == {slug, second}
        assert conn.execute("SELECT count(*) AS n FROM rvbbit.dashboards WHERE owner_email=%s", (heir,)).fetchone()["n"] == 2
    finally:
        conn.rollback()
        conn.close()
