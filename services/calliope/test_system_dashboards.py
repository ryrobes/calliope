"""System dashboards are seeded at Warehouse startup without a human caller.

The Acceleration health observatory ships in the image, is owned by
calliope@system, viewable by the Everyone team, re-versioned only when the
bundled HTML changes, and skipped when the extension views it reads are absent.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import calliope  # noqa: E402


class _Cursor:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return self._value


class _Conn:
    """Answers the seeder's queries by substring; records writes."""

    def __init__(self, *, dashboards_exist=True, views_exist=True, existing=None, grants_table=True):
        self.dashboards_exist = dashboards_exist
        self.views_exist = views_exist
        self.existing = existing            # None or (id, latest_version, manifest)
        self.grants_table = grants_table
        self.writes: list[tuple[str, tuple]] = []

    dict_rows = True   # the Warehouse connections use psycopg's dict_row

    def _row(self, **kv):
        return kv if self.dict_rows else tuple(kv.values())

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "to_regclass('rvbbit.dashboards')" in s:
            return _Cursor(self._row(present=self.dashboards_exist))
        if "to_regclass('rvbbit.artifact_view_grants')" in s:
            return _Cursor(self._row(present=self.grants_table))
        if s.startswith("SELECT to_regclass(%s)"):
            return _Cursor(self._row(present=self.views_exist))
        if "FROM rvbbit.calliope_design_profile_versions v" in s:
            if not getattr(self, "profile", True):
                return _Cursor(None)
            return _Cursor(self._row(
                id="ca1100e0-0000-4000-8000-000000000102", profile_id="ca1100e0-0000-4000-8000-000000000001",
                version=2, profile_name="Adaptive Calliope", profile_description="", profile_owner="calliope@system",
                profile_archived=False, tokens={"behavior": {"theme_source": "viewer", "live": True}},
                markdown="# Adaptive", name="Adaptive Calliope", created_at="2026-08-01T00:00:00Z"))
        if s.startswith("SELECT id, latest_version, manifest FROM rvbbit.dashboards"):
            if self.existing is None:
                return _Cursor(None)
            i, v, m = self.existing
            return _Cursor(self._row(id=i, latest_version=v, manifest=m))
        self.writes.append((s, params))
        if s.startswith("INSERT INTO rvbbit.dashboards"):
            return _Cursor(self._row(id=4242))
        return _Cursor(None)


def _bundled_html() -> str:
    return (calliope._SYSTEM_DASHBOARDS_DIR / "acceleration-health.html").read_text(encoding="utf-8")


def test_bundled_observatory_reads_only_the_extension_views():
    html = _bundled_html()
    assert '<meta name="rvbbit-system-dashboard" content="acceleration-observatory">' in html
    queries = re.findall(r"[a-z]+:`([^`]+)`", html[html.index("const Q={") : html.index("}", html.index("const Q={") + 2000)])
    assert len(queries) >= 10
    for q in queries:
        assert "rvbbit.accel_observatory_" in q or "rvbbit.accel_table_runtime_profile" in q, q
        assert "accel_fold_candidates" not in q and "layout_variant_status" not in q      # the raw sources are behind the views now
    assert "${E}" not in html


def test_seed_creates_versions_grants_everyone_and_is_idempotent():
    html = _bundled_html()
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()

    fresh = _Conn()
    out = calliope._seed_system_dashboards(fresh)
    assert out == [{"slug": "rvbbit-acceleration-health", "status": "created", "version": 1}]
    kinds = [w[0].split(" ")[0:3] for w in fresh.writes]
    assert kinds == [["INSERT", "INTO", "rvbbit.dashboards"], ["INSERT", "INTO", "rvbbit.dashboard_versions"],
                     ["INSERT", "INTO", "rvbbit.artifact_view_grants"]]
    insert_sql, insert_params = fresh.writes[0]
    assert insert_params[3] == "calliope@system"
    manifest = json.loads(insert_params[4])
    assert manifest["system_dashboard"]["content_sha256"] == digest
    # Seeded pages carry the frozen Adaptive Calliope profile like any published artifact.
    assert manifest["design_profile"]["schema_version"] == "rvbbit.artifact-design-profile.v1"
    assert manifest["design_profile"]["adaptive"] is True
    assert manifest["design_profile"]["version_id"] == "ca1100e0-0000-4000-8000-000000000102"
    assert manifest["system_dashboard"]["design_profile_version_id"] == "ca1100e0-0000-4000-8000-000000000102"
    version_sql, version_params = fresh.writes[1]
    assert version_params[0:2] == (4242, 1) and version_params[2] == html
    assert "t.system_key = 'everyone'" in fresh.writes[2][0]

    # Same content and profile again: no new version, grant re-asserted.
    same = _Conn(existing=(4242, 1, {"system_dashboard": {"content_sha256": digest,
                                                          "design_profile_version_id": "ca1100e0-0000-4000-8000-000000000102"}}))
    out = calliope._seed_system_dashboards(same)
    assert out == [{"slug": "rvbbit-acceleration-health", "status": "unchanged", "version": 1}]
    assert [w[0].split(" ")[2] for w in same.writes] == ["rvbbit.artifact_view_grants"]

    # An earlier seed without the profile is re-versioned even though the HTML is unchanged.
    unthemed = _Conn(existing=(4242, 2, {"system_dashboard": {"content_sha256": digest}}))
    out = calliope._seed_system_dashboards(unthemed)
    assert out == [{"slug": "rvbbit-acceleration-health", "status": "updated", "version": 3}]
    assert json.loads(unthemed.writes[0][1][3])["design_profile"]["adaptive"] is True

    # Changed content: bump the version, keep the row.
    changed = _Conn(existing=(4242, 3, {"system_dashboard": {"content_sha256": "old",
                                                             "design_profile_version_id": "ca1100e0-0000-4000-8000-000000000102"}}))
    out = calliope._seed_system_dashboards(changed)
    assert out == [{"slug": "rvbbit-acceleration-health", "status": "updated", "version": 4}]
    assert changed.writes[0][0].startswith("UPDATE rvbbit.dashboards SET")
    assert changed.writes[1][1][0:2] == (4242, 4)


def test_seed_still_works_when_the_profile_row_is_missing():
    bare = _Conn()
    bare.profile = False
    out = calliope._seed_system_dashboards(bare)
    assert out[0]["status"] == "created"
    assert "design_profile" not in json.loads(bare.writes[0][1][4])


def test_seed_works_with_tuple_rows_too():
    tuples = _Conn()
    tuples.dict_rows = False
    out = calliope._seed_system_dashboards(tuples)
    assert out[0]["status"] == "created" and len(tuples.writes) == 3


def test_seed_skips_when_the_extension_views_are_missing():
    older = _Conn(views_exist=False)
    out = calliope._seed_system_dashboards(older)
    assert out[0]["status"] == "skipped" and "accel_observatory_coverage" in out[0]["reason"]
    assert older.writes == []
    assert calliope._seed_system_dashboards(_Conn(dashboards_exist=False)) == []
