"""The live mutating-source feeder turns public feeds into a churning MariaDB
mirror source. Its row builders and chaos planner are pure; the compose wiring
is a contract the appliance's mirror path depends on."""
from __future__ import annotations

import datetime as dt
import importlib.util
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEEDER = ROOT / "docker" / "sample-sources" / "live-feeder" / "feeder.py"
COMPOSE = ROOT / "docker" / "docker-compose.ingestion-test.yml"
INIT = ROOT / "docker" / "sample-sources" / "mariadb-init.sql"


def _load():
    spec = importlib.util.spec_from_file_location("live_feeder", FEEDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)      # no pymysql needed: the driver import is deferred to Db()
    return module


def test_timestamps_accept_every_shape_the_feeds_use():
    f = _load()
    assert f.parse_ts("2026-08-15T15:22:45.000") == dt.datetime(2026, 8, 15, 15, 22, 45)          # Socrata floating
    assert f.parse_ts("2026-08-17T01:40:09.688Z") == dt.datetime(2026, 8, 17, 1, 40, 9, 688000)   # Socrata :updated_at
    assert f.parse_ts(1786951971070) == dt.datetime.fromtimestamp(1786951971.070, dt.timezone.utc).replace(tzinfo=None)  # USGS ms
    assert f.parse_ts(1786952553) == dt.datetime.fromtimestamp(1786952553, dt.timezone.utc).replace(tzinfo=None)  # GBFS s
    assert f.parse_ts("") is None and f.parse_ts(None) is None and f.parse_ts("garbage") is None
    assert f.as_int("70082040") == 70082040 and f.as_int("") is None and f.as_float("40.65611") == 40.65611
    assert f.as_text({"a": 1}) == '{"a":1}' and f.as_text("x" * 10, 4) == "xxxx"


def test_gbfs_rows_keep_every_station_and_json_columns_as_text():
    f = _load()
    status = {"last_updated": 1786952553, "ttl": 60, "data": {"stations": [
        {"station_id": "2235288981858690806", "num_bikes_available": 3, "num_bikes_disabled": 0,
         "num_docks_available": 12, "num_docks_disabled": 1, "is_installed": 1, "is_renting": 1,
         "is_returning": 1, "last_reported": 86400,
         "vehicle_types_available": [{"vehicle_type_id": "1", "count": 2}], "num_ebikes_available": 1},
        {"num_bikes_available": 9},                                # no id → dropped
    ]}}
    rows = f.gbfs_status_rows(status)
    assert len(rows) == 1 and rows[0]["station_id"] == "2235288981858690806"
    assert rows[0]["vehicle_types_available"] == '[{"vehicle_type_id":"1","count":2}]'
    assert rows[0]["feed_last_updated"] == f.parse_ts(1786952553)
    assert rows[0]["last_reported"] == dt.datetime(1970, 1, 2)     # bogus upstream value preserved, not laundered

    info = {"data": {"stations": [{"station_id": "s1", "name": "Lenox Rd & Remsen Ave", "short_name": "3478.04",
                                    "lon": -73.92342, "lat": 40.65611, "region_id": "71", "capacity": 18,
                                    "rental_uris": {"ios": "https://x"}, "station_type": "classic"}]}}
    rows = f.gbfs_information_rows(info)
    assert rows[0]["capacity"] == 18 and rows[0]["rental_uris"] == '{"ios":"https://x"}'
    assert rows[0]["extra"] == '{"station_type":"classic"}'         # unknown fields survive as JSON


def test_usgs_rows_flatten_geometry_and_keep_revision_timestamps():
    f = _load()
    payload = {"features": [{"id": "nc75419912", "properties": {
        "mag": 1.2, "place": "8 km NW of The Geysers, CA", "time": 1786951971070, "updated": 1786952067491,
        "tz": None, "url": "https://x", "felt": None, "cdi": None, "mmi": None, "alert": None,
        "status": "automatic", "tsunami": 0, "sig": 22, "net": "nc", "code": "75419912",
        "ids": ",nc75419912,", "sources": ",nc,", "types": ",nearby-cities,origin,", "nst": 12,
        "dmin": 0.01, "rms": 0.02, "gap": 60, "magType": "md", "type": "earthquake", "title": "M 1.2"},
        "geometry": {"type": "Point", "coordinates": [-122.8038, 38.8255, 2.45]}}]}
    rows = f.usgs_rows(payload)
    assert rows[0]["event_id"] == "nc75419912" and rows[0]["longitude"] == -122.8038
    assert rows[0]["latitude"] == 38.8255 and rows[0]["depth_km"] == 2.45
    assert rows[0]["updated_at"] > rows[0]["event_time"]              # revised after the event
    assert rows[0]["mag_type"] == "md" and rows[0]["event_type"] == "earthquake"


def test_nyc311_rows_are_wide_and_typed():
    f = _load()
    rows = f.nyc311_rows([{":id": "row-k8wc-n3hj-vwin", ":updated_at": "2026-08-17T01:40:09.688Z",
                           "unique_key": "70082040", "created_date": "2026-08-15T15:22:45.000",
                           "closed_date": "2026-08-15T18:51:33.000", "status": "Closed", "agency": "NYPD",
                           "complaint_type": "Illegal Parking", "borough": "BROOKLYN", "bbl": "3012340056",
                           "x_coordinate_state_plane": "1001234", "latitude": "40.65", "longitude": "-73.92"},
                          {"unique_key": None}])
    assert len(rows) == 1
    row = rows[0]
    assert row["unique_key"] == 70082040 and row["socrata_updated_at"] == dt.datetime(2026, 8, 17, 1, 40, 9, 688000)
    assert row["x_coordinate_state_plane"] == 1001234 and row["latitude"] == 40.65 and row["bbl"] == "3012340056"
    assert row["closed_date"] == dt.datetime(2026, 8, 15, 18, 51, 33) and row["vehicle_type"] is None
    expected = {"unique_key", "socrata_row_id", "socrata_updated_at", *f.NYC311_TEXT, *f.NYC311_TS,
                *f.NYC311_INT, *f.NYC311_FLOAT}
    assert set(row) == expected and len(expected) == 42          # stable width regardless of sparse payloads


def test_chaos_controls_default_and_plan_respects_the_knobs():
    f = _load()
    controls = f.parse_controls([("churn_pct", "10"), ("dupe_keys", "3"), ("bogus", "9"), ("insert_rows", "x")])
    assert controls["churn_pct"] == 10 and controls["dupe_keys"] == 3 and controls["insert_rows"] == 20
    assert "bogus" not in controls and controls["pause"] == 0
    rng = random.Random(7)
    plan = f.plan_chaos_tick(controls, list(range(1, 101)), 100, rng)
    assert len(plan["update"]) == 10 and len(plan["delete"]) == 5 and plan["insert"] == list(range(101, 121))
    assert len(plan["dupe"]) == 3 and set(plan["dupe"]) <= set(range(1, 101))    # duplicates reuse live ids
    assert plan["drift_columns"] == 0 and plan["identical_reload"] is False
    empty = f.plan_chaos_tick(controls, [], 0, rng)
    assert empty["update"] == [] and empty["delete"] == [] and empty["dupe"] == [] and empty["insert"]
    widget = f.make_widget(42, random.Random(1), dt.datetime(2026, 8, 17))
    assert widget["widget_id"] == 42 and widget["sku"] == "SKU-0000042" and widget["status"] in f.WIDGET_STATUSES


def test_live_profile_is_opt_in_and_uses_a_read_only_mirror_principal():
    compose = COMPOSE.read_text(encoding="utf-8")
    init = " ".join(INIT.read_text(encoding="utf-8").split())        # collapse the aligned spacing
    assert "SAMPLE_MARIADB_IMAGE:-mariadb:11.8@sha256:" in compose
    assert compose.count('profiles: ["live"]') == 2                    # both live services are opt-in
    assert "rvbbit.fixture.dialect: mariadb" in compose
    assert "context: ./sample-sources/live-feeder" in compose
    assert "LIVE_DB_USER: feeder" in compose and "ports:" not in compose
    for db in ("bikeshare", "quakes", "nyc311", "chaos"):
        assert f"GRANT SELECT ON {db}.* TO 'mirror_reader'@'%'" in init
        assert f"GRANT ALL PRIVILEGES ON {db}.* TO 'feeder'@'%'" in init
    feeder = FEEDER.read_text(encoding="utf-8")
    assert "widget_id bigint NOT NULL," in feeder and "PRIMARY KEY (widget_id)" not in feeder   # declared-key-only table
    assert '"$select": "*,:id,:updated_at"' in feeder                   # Socrata: star first, system fields after
    assert "/tmp/feeder-alive" in feeder and "/tmp/feeder-alive" in compose
    readme = (ROOT / "docker" / "sample-sources" / "README.md").read_text(encoding="utf-8")
    assert "--profile live" in readme and "mariadb+pymysql://mirror_reader:mirror_readonly@sample-mariadb:3306" in readme


def test_upsert_touch_columns_only_move_when_their_signal_changes():
    """USGS re-upserts the whole 30-day window hourly; last_seen_at must not churn
    every row (which made the snapshot mirror full-rebuild), only revised ones."""
    f = _load()
    executed = []

    class Cur:
        def executemany(self, sql, params):
            executed.append((sql, list(params)))

    rows = [{"event_id": "a", "mag": 1.0, "updated_at": 5, "first_seen_at": 1, "last_seen_at": 9}]
    n = f.upsert(Cur(), "events", rows, "event_id", insert_only=("first_seen_at",),
                 touch_when_changed={"last_seen_at": "updated_at"})
    assert n == 1
    sql = executed[0][0]
    assert "first_seen_at = VALUES" not in sql
    assert "last_seen_at = IF(updated_at <=> VALUES(updated_at), last_seen_at, VALUES(last_seen_at))" in sql
    assert sql.index("last_seen_at = IF(") < sql.index("updated_at = VALUES(updated_at)")   # evaluated before its signal
