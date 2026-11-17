#!/usr/bin/env python3
"""Keep the MariaDB fixture churning from real public feeds.

The appliance's mirror path (dlt sql_database → RVBBIT) is only interesting
under a workload that inserts, updates, deletes and drifts on a cadence. Static
fixtures never do. This feeder turns four public feeds into exactly that kind
of source database:

  bikeshare  Citi Bike GBFS — `station_status` rewrites nearly every row every
             tick (~2.5k stations), `station_information` drifts slowly,
             `station_status_history` appends a full sample every N minutes
             and prunes by retention (an append-only table that also deletes).
  quakes     USGS earthquake feeds — events are *revised* for days after they
             happen (magnitude, place, `updated`), some are deleted, and the
             source is a rolling 30-day window, so rows age out.
  nyc311     NYC 311 service requests (Socrata) — a wide, business-shaped table
             with a real `updated_at` cursor, status transitions over days, a
             nightly bulk-update burst, and a retention window.
  chaos      Synthetic knobs steerable at runtime through `chaos.controls`:
             churn %, insert/delete rates, duplicate keys under a declared key,
             additive/subtractive schema drift, identical truncate+reload,
             pause. Everything a public feed will not do to you on demand.

Every source records its outcome in `chaos.feeder_status`, and every loop
touches /tmp/feeder-alive for the container healthcheck. Configuration is
environment-only (LIVE_*); see `_env` calls below for names and defaults.
Nothing here is secret: the fixture principals are documented test-only.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import os
import random
import time
import traceback
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable

log = logging.getLogger("live-feeder")

UTC = dt.timezone.utc
USER_AGENT = "rvbbit-live-feeder/1 (+https://github.com/ryrobes/calliope)"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# Value helpers (pure — covered by tests)
# --------------------------------------------------------------------------- #

def parse_ts(value: Any) -> dt.datetime | None:
    """ISO-8601 (with or without Z/offset, optional fractional seconds) or epoch
    seconds/milliseconds → naive UTC datetime, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:          # milliseconds
            seconds /= 1000.0
        try:
            return dt.datetime.fromtimestamp(seconds, UTC).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_text(value: Any, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    return text[:limit] if limit else text


def utcnow() -> dt.datetime:
    return dt.datetime.now(UTC).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def fetch_json(url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None,
               timeout: int = 90) -> Any:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


# --------------------------------------------------------------------------- #
# Row builders (pure — covered by tests)
# --------------------------------------------------------------------------- #

def gbfs_information_rows(payload: dict) -> list[dict]:
    known = {"station_id", "name", "short_name", "lat", "lon", "region_id", "capacity", "rental_uris"}
    rows = []
    for s in payload.get("data", {}).get("stations", []):
        sid = as_text(s.get("station_id"), 64)
        if not sid:
            continue
        rows.append({
            "station_id": sid,
            "name": as_text(s.get("name"), 200),
            "short_name": as_text(s.get("short_name"), 64),
            "lat": as_float(s.get("lat")),
            "lon": as_float(s.get("lon")),
            "region_id": as_text(s.get("region_id"), 32),
            "capacity": as_int(s.get("capacity")),
            "rental_uris": as_text(s.get("rental_uris")),
            "extra": as_text({k: v for k, v in s.items() if k not in known}) or None,
        })
    return rows


def gbfs_status_rows(payload: dict) -> list[dict]:
    feed_ts = parse_ts(payload.get("last_updated"))
    rows = []
    for s in payload.get("data", {}).get("stations", []):
        sid = as_text(s.get("station_id"), 64)
        if not sid:
            continue
        rows.append({
            "station_id": sid,
            "num_bikes_available": as_int(s.get("num_bikes_available")),
            "num_ebikes_available": as_int(s.get("num_ebikes_available")),
            "num_bikes_disabled": as_int(s.get("num_bikes_disabled")),
            "num_docks_available": as_int(s.get("num_docks_available")),
            "num_docks_disabled": as_int(s.get("num_docks_disabled")),
            "is_installed": as_int(s.get("is_installed")),
            "is_renting": as_int(s.get("is_renting")),
            "is_returning": as_int(s.get("is_returning")),
            "last_reported": parse_ts(s.get("last_reported")),
            "vehicle_types_available": as_text(s.get("vehicle_types_available")),
            "feed_last_updated": feed_ts,
        })
    return rows


def usgs_rows(payload: dict) -> list[dict]:
    rows = []
    for f in payload.get("features", []):
        p = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None, None]
        eid = as_text(f.get("id"), 32)
        if not eid:
            continue
        rows.append({
            "event_id": eid,
            "mag": as_float(p.get("mag")),
            "place": as_text(p.get("place"), 255),
            "event_time": parse_ts(p.get("time")),
            "updated_at": parse_ts(p.get("updated")),
            "tz_offset": as_int(p.get("tz")),
            "url": as_text(p.get("url"), 255),
            "felt": as_int(p.get("felt")),
            "cdi": as_float(p.get("cdi")),
            "mmi": as_float(p.get("mmi")),
            "alert": as_text(p.get("alert"), 16),
            "status": as_text(p.get("status"), 16),
            "tsunami": as_int(p.get("tsunami")),
            "sig": as_int(p.get("sig")),
            "net": as_text(p.get("net"), 8),
            "code": as_text(p.get("code"), 32),
            "ids": as_text(p.get("ids")),
            "sources": as_text(p.get("sources")),
            "types": as_text(p.get("types")),
            "nst": as_int(p.get("nst")),
            "dmin": as_float(p.get("dmin")),
            "rms": as_float(p.get("rms")),
            "gap": as_float(p.get("gap")),
            "mag_type": as_text(p.get("magType"), 8),
            "event_type": as_text(p.get("type"), 32),
            "title": as_text(p.get("title"), 255),
            "longitude": as_float(coords[0] if len(coords) > 0 else None),
            "latitude": as_float(coords[1] if len(coords) > 1 else None),
            "depth_km": as_float(coords[2] if len(coords) > 2 else None),
        })
    return rows


NYC311_TEXT = {
    "agency": 16, "agency_name": 200, "complaint_type": 120, "descriptor": 200, "location_type": 120,
    "incident_zip": 16, "incident_address": 200, "street_name": 200, "cross_street_1": 200,
    "cross_street_2": 200, "intersection_street_1": 200, "intersection_street_2": 200,
    "address_type": 32, "city": 80, "landmark": 200, "facility_type": 80, "status": 32,
    "resolution_description": None, "community_board": 64, "bbl": 16, "borough": 32,
    "open_data_channel_type": 32, "park_facility_name": 200, "park_borough": 32,
    "vehicle_type": 80, "taxi_company_borough": 32, "taxi_pick_up_location": 200,
    "bridge_highway_name": 120, "bridge_highway_direction": 80, "road_ramp": 32,
    "bridge_highway_segment": 200,
}
NYC311_TS = ("created_date", "closed_date", "due_date", "resolution_action_updated_date")
NYC311_INT = ("x_coordinate_state_plane", "y_coordinate_state_plane")
NYC311_FLOAT = ("latitude", "longitude")


def nyc311_rows(records: Iterable[dict]) -> list[dict]:
    rows = []
    for r in records:
        key = as_int(r.get("unique_key"))
        if key is None:
            continue
        row: dict[str, Any] = {
            "unique_key": key,
            "socrata_row_id": as_text(r.get(":id"), 64),
            "socrata_updated_at": parse_ts(r.get(":updated_at")),
        }
        for col, limit in NYC311_TEXT.items():
            row[col] = as_text(r.get(col), limit)
        for col in NYC311_TS:
            row[col] = parse_ts(r.get(col))
        for col in NYC311_INT:
            row[col] = as_int(r.get(col))
        for col in NYC311_FLOAT:
            row[col] = as_float(r.get(col))
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Chaos planning (pure — covered by tests)
# --------------------------------------------------------------------------- #

CHAOS_DEFAULTS: dict[str, str] = {
    "pause": "0",              # 1 → the chaos source skips its tick entirely
    "churn_pct": "5",          # % of widgets updated per tick
    "insert_rows": "20",       # new widgets per tick
    "delete_rows": "5",        # widgets deleted per tick
    "dupe_keys": "0",          # duplicate widget_id rows inserted per tick (declared key, no constraint)
    "schema_drift": "0",       # keep exactly N extra_N columns (adds or drops to match)
    "identical_reload": "0",   # 1 → truncate and reinsert byte-identical rows every tick
    "widget_base_rows": "20000",
}
WIDGET_CATEGORIES = ("gizmo", "sprocket", "flange", "bracket", "widget", "cog")
WIDGET_STATUSES = ("active", "backorder", "discontinued", "draft")


def parse_controls(rows: Iterable[tuple[str, str]]) -> dict[str, int]:
    merged = dict(CHAOS_DEFAULTS)
    for name, value in rows:
        if name in merged and value is not None:
            merged[name] = str(value).strip()
    out: dict[str, int] = {}
    for name, value in merged.items():
        try:
            out[name] = max(0, int(float(value)))
        except ValueError:
            out[name] = int(CHAOS_DEFAULTS[name])
    return out


def make_widget(widget_id: int, rng: random.Random, now: dt.datetime) -> dict:
    return {
        "widget_id": widget_id,
        "sku": f"SKU-{widget_id:07d}",
        "category": rng.choice(WIDGET_CATEGORIES),
        "qty": rng.randint(0, 500),
        "unit_price": round(rng.uniform(0.5, 250.0), 2),
        "status": rng.choice(WIDGET_STATUSES),
        "notes": None if rng.random() < 0.7 else f"note {rng.randint(1, 99999)}",
        "updated_at": now,
    }


def plan_chaos_tick(controls: dict[str, int], existing_ids: list[int], max_id: int,
                    rng: random.Random) -> dict:
    """Decide what one chaos tick does, given the current widget population."""
    n = len(existing_ids)
    churn = min(n, (n * controls["churn_pct"]) // 100)
    to_update = rng.sample(existing_ids, churn) if churn else []
    to_delete = rng.sample(existing_ids, min(n, controls["delete_rows"])) if controls["delete_rows"] else []
    new_ids = list(range(max_id + 1, max_id + 1 + controls["insert_rows"]))
    dupes = rng.sample(existing_ids, min(n, controls["dupe_keys"])) if controls["dupe_keys"] else []
    return {"update": to_update, "delete": to_delete, "insert": new_ids, "dupe": dupes,
            "drift_columns": controls["schema_drift"], "identical_reload": bool(controls["identical_reload"])}


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

DDL: dict[str, list[str]] = {
    "bikeshare": [
        """CREATE TABLE IF NOT EXISTS station_information (
            station_id varchar(64) NOT NULL PRIMARY KEY,
            name varchar(200), short_name varchar(64), lat double, lon double,
            region_id varchar(32), capacity int, rental_uris json, extra json,
            first_seen_at datetime(6) NOT NULL,
            updated_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
        )""",
        """CREATE TABLE IF NOT EXISTS station_status (
            station_id varchar(64) NOT NULL PRIMARY KEY,
            num_bikes_available int, num_ebikes_available int, num_bikes_disabled int,
            num_docks_available int, num_docks_disabled int,
            is_installed tinyint, is_renting tinyint, is_returning tinyint,
            last_reported datetime(6), vehicle_types_available json, feed_last_updated datetime(6),
            updated_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
        )""",
        """CREATE TABLE IF NOT EXISTS station_status_history (
            sample_id bigint NOT NULL AUTO_INCREMENT PRIMARY KEY,
            sampled_at datetime(6) NOT NULL,
            station_id varchar(64) NOT NULL,
            num_bikes_available int, num_ebikes_available int, num_docks_available int,
            is_renting tinyint,
            KEY station_status_history_sampled_at (sampled_at)
        )""",
    ],
    "quakes": [
        """CREATE TABLE IF NOT EXISTS events (
            event_id varchar(32) NOT NULL PRIMARY KEY,
            mag double, place varchar(255), event_time datetime(6), updated_at datetime(6),
            tz_offset int, url varchar(255), felt int, cdi double, mmi double, alert varchar(16),
            status varchar(16), tsunami tinyint, sig int, net varchar(8), code varchar(32),
            ids text, sources text, types text, nst int, dmin double, rms double, gap double,
            mag_type varchar(8), event_type varchar(32), title varchar(255),
            longitude double, latitude double, depth_km double,
            first_seen_at datetime(6) NOT NULL, last_seen_at datetime(6) NOT NULL,
            KEY events_updated_at (updated_at), KEY events_event_time (event_time)
        )""",
    ],
    "nyc311": [
        """CREATE TABLE IF NOT EXISTS service_requests (
            unique_key bigint NOT NULL PRIMARY KEY,
            socrata_row_id varchar(64), socrata_updated_at datetime(6),
            created_date datetime(6), closed_date datetime(6),
            agency varchar(16), agency_name varchar(200), complaint_type varchar(120), descriptor varchar(200),
            location_type varchar(120), incident_zip varchar(16), incident_address varchar(200),
            street_name varchar(200), cross_street_1 varchar(200), cross_street_2 varchar(200),
            intersection_street_1 varchar(200), intersection_street_2 varchar(200), address_type varchar(32),
            city varchar(80), landmark varchar(200), facility_type varchar(80), status varchar(32),
            due_date datetime(6), resolution_description text, resolution_action_updated_date datetime(6),
            community_board varchar(64), bbl varchar(16), borough varchar(32),
            x_coordinate_state_plane int, y_coordinate_state_plane int, open_data_channel_type varchar(32),
            park_facility_name varchar(200), park_borough varchar(32), vehicle_type varchar(80),
            taxi_company_borough varchar(32), taxi_pick_up_location varchar(200),
            bridge_highway_name varchar(120), bridge_highway_direction varchar(80), road_ramp varchar(32),
            bridge_highway_segment varchar(200), latitude double, longitude double,
            feeder_updated_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
            KEY service_requests_socrata_updated_at (socrata_updated_at),
            KEY service_requests_created_date (created_date)
        )""",
    ],
    "chaos": [
        """CREATE TABLE IF NOT EXISTS controls (
            name varchar(64) NOT NULL PRIMARY KEY,
            value varchar(64) NOT NULL,
            updated_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
        )""",
        # Deliberately no PRIMARY KEY: the mirror declares widget_id as the key,
        # and `dupe_keys` shows what happens when the source does not honour it.
        """CREATE TABLE IF NOT EXISTS widgets (
            widget_id bigint NOT NULL,
            sku varchar(32) NOT NULL, category varchar(32) NOT NULL, qty int NOT NULL,
            unit_price decimal(10,2) NOT NULL, status varchar(16) NOT NULL, notes text,
            updated_at datetime(6) NOT NULL,
            KEY widgets_widget_id (widget_id), KEY widgets_updated_at (updated_at)
        )""",
        """CREATE TABLE IF NOT EXISTS feeder_status (
            source varchar(32) NOT NULL PRIMARY KEY,
            last_started_at datetime(6), last_finished_at datetime(6), last_ok tinyint,
            last_rows int, last_error text, runs int NOT NULL DEFAULT 0
        )""",
    ],
}


# --------------------------------------------------------------------------- #
# DB plumbing
# --------------------------------------------------------------------------- #

class Db:
    def __init__(self) -> None:
        import pymysql  # local import keeps the pure helpers importable without the driver
        self._pymysql = pymysql
        self.host = _env("LIVE_DB_HOST", "sample-mariadb")
        self.port = _env_int("LIVE_DB_PORT", 3306)
        self.user = _env("LIVE_DB_USER", "feeder")
        self.password = _env("LIVE_DB_PASSWORD", "rvbbit_feeder")

    def connect(self, database: str):
        return self._pymysql.connect(host=self.host, port=self.port, user=self.user, password=self.password,
                                     database=database, charset="utf8mb4", autocommit=False,
                                     connect_timeout=15, read_timeout=600, write_timeout=600)

    def ensure_schema(self) -> None:
        for database, statements in DDL.items():
            with self.connect(database) as conn, conn.cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)
                if database == "chaos":
                    cur.executemany("INSERT IGNORE INTO controls (name, value) VALUES (%s, %s)",
                                    list(CHAOS_DEFAULTS.items()))
                conn.commit()

    def record_status(self, source: str, started: dt.datetime, ok: bool, rows: int, error: str | None) -> None:
        with self.connect("chaos") as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO feeder_status (source, last_started_at, last_finished_at, last_ok, last_rows, last_error, runs)
                   VALUES (%s, %s, %s, %s, %s, %s, 1)
                   ON DUPLICATE KEY UPDATE last_started_at = VALUES(last_started_at),
                       last_finished_at = VALUES(last_finished_at), last_ok = VALUES(last_ok),
                       last_rows = VALUES(last_rows), last_error = VALUES(last_error), runs = runs + 1""",
                (source, started, utcnow(), 1 if ok else 0, rows, (error or None) and error[:4000]),
            )
            conn.commit()


def upsert(cur, table: str, rows: list[dict], key: str, insert_only: tuple[str, ...] = (),
           batch: int = 2000, touch_when_changed: dict[str, str] | None = None) -> int:
    """INSERT ... ON DUPLICATE KEY UPDATE for every column except the key and
    the insert-only columns. MariaDB only touches a row when a value actually
    differs, so ON UPDATE timestamps stay honest. `touch_when_changed` maps a
    bookkeeping column to a signal column: it is only rewritten when the signal
    column's value changes, so a blanket re-upsert of an unchanged feed does not
    churn every row (e.g. last_seen_at ← updated_at for USGS revisions)."""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    touch = touch_when_changed or {}
    updatable = [c for c in cols if c != key and c not in insert_only]
    # MariaDB evaluates the SET list left to right against already-updated
    # values, so bookkeeping columns must be assigned before their signal column.
    updatable.sort(key=lambda c: 0 if c in touch else 1)
    sets = []
    for c in updatable:
        if c in touch:
            sets.append(f"{c} = IF({touch[c]} <=> VALUES({touch[c]}), {c}, VALUES({c}))")
        else:
            sets.append(f"{c} = VALUES({c})")
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
           f"ON DUPLICATE KEY UPDATE " + ", ".join(sets))
    total = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        cur.executemany(sql, [tuple(r[c] for c in cols) for r in chunk])
        total += len(chunk)
    return total


def insert_rows(cur, table: str, rows: list[dict], batch: int = 2000) -> int:
    """Plain multi-row INSERT (for tables without a usable key)."""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    for i in range(0, len(rows), batch):
        cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows[i:i + batch]])
    return len(rows)


def delete_missing(cur, table: str, key: str, present: Iterable[Any], where: str = "1=1") -> int:
    """Delete rows whose key is not in `present` (staged through a temp table so
    the IN-list never hits packet limits)."""
    cur.execute("CREATE TEMPORARY TABLE IF NOT EXISTS _present_keys (k varchar(64) NOT NULL PRIMARY KEY)")
    cur.execute("TRUNCATE _present_keys")
    keys = [(str(k),) for k in present]
    for i in range(0, len(keys), 5000):
        cur.executemany("INSERT IGNORE INTO _present_keys (k) VALUES (%s)", keys[i:i + 5000])
    cur.execute(f"DELETE t FROM {table} t LEFT JOIN _present_keys p ON p.k = CAST(t.{key} AS CHAR) "
                f"WHERE p.k IS NULL AND {where}")
    deleted = cur.rowcount
    cur.execute("DROP TEMPORARY TABLE _present_keys")
    return deleted


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #

class Source:
    name = "base"

    def __init__(self, db: Db, interval: int) -> None:
        self.db = db
        self.interval = interval
        self.next_due = 0.0

    def run(self) -> int:  # rows touched
        raise NotImplementedError


class Bikeshare(Source):
    name = "bikeshare"

    def __init__(self, db: Db) -> None:
        super().__init__(db, _env_int("LIVE_GBFS_STATUS_SECONDS", 120))
        self.discovery_url = _env("LIVE_GBFS_URL", "https://gbfs.lyft.com/gbfs/2.3/bkn/gbfs.json")
        self.history_seconds = _env_int("LIVE_GBFS_HISTORY_SECONDS", 600)
        self.retention_hours = _env_int("LIVE_GBFS_HISTORY_RETENTION_HOURS", 72)
        self.info_seconds = _env_int("LIVE_GBFS_INFORMATION_SECONDS", 1800)
        self._feeds: dict[str, str] = {}
        self._last_history = 0.0
        self._last_info = 0.0

    def feeds(self) -> dict[str, str]:
        if not self._feeds:
            doc = fetch_json(self.discovery_url)
            langs = doc.get("data", {})
            block = langs.get("en") or next(iter(langs.values()))
            self._feeds = {f["name"]: f["url"] for f in block.get("feeds", [])}
        return self._feeds

    def run(self) -> int:
        now = time.monotonic()
        touched = 0
        feeds = self.feeds()
        with self.db.connect("bikeshare") as conn, conn.cursor() as cur:
            if now - self._last_info >= self.info_seconds:
                info = gbfs_information_rows(fetch_json(feeds["station_information"]))
                stamp = utcnow()
                for r in info:
                    r["first_seen_at"] = stamp
                touched += upsert(cur, "station_information", info, "station_id", insert_only=("first_seen_at",))
                touched += delete_missing(cur, "station_information", "station_id", (r["station_id"] for r in info))
                self._last_info = now
            status = gbfs_status_rows(fetch_json(feeds["station_status"]))
            touched += upsert(cur, "station_status", status, "station_id")
            touched += delete_missing(cur, "station_status", "station_id", (r["station_id"] for r in status))
            if now - self._last_history >= self.history_seconds:
                stamp = utcnow()
                cur.executemany(
                    "INSERT INTO station_status_history (sampled_at, station_id, num_bikes_available, "
                    "num_ebikes_available, num_docks_available, is_renting) VALUES (%s, %s, %s, %s, %s, %s)",
                    [(stamp, r["station_id"], r["num_bikes_available"], r["num_ebikes_available"],
                      r["num_docks_available"], r["is_renting"]) for r in status])
                touched += len(status)
                cur.execute("DELETE FROM station_status_history WHERE sampled_at < %s",
                            (stamp - dt.timedelta(hours=self.retention_hours),))
                touched += cur.rowcount
                self._last_history = now
            conn.commit()
        return touched


class Quakes(Source):
    name = "quakes"

    def __init__(self, db: Db) -> None:
        super().__init__(db, _env_int("LIVE_USGS_DAY_SECONDS", 300))
        base = _env("LIVE_USGS_BASE_URL", "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary")
        self.day_url = f"{base}/all_day.geojson"
        self.month_url = f"{base}/all_month.geojson"
        self.month_seconds = _env_int("LIVE_USGS_MONTH_SECONDS", 3600)
        self.retention_days = _env_int("LIVE_USGS_RETENTION_DAYS", 30)
        self._last_month = 0.0

    def run(self) -> int:
        now = time.monotonic()
        full = now - self._last_month >= self.month_seconds
        rows = usgs_rows(fetch_json(self.month_url if full else self.day_url))
        stamp = utcnow()
        for r in rows:
            r["first_seen_at"] = stamp
            r["last_seen_at"] = stamp
        touched = 0
        with self.db.connect("quakes") as conn, conn.cursor() as cur:
            touched += upsert(cur, "events", rows, "event_id", insert_only=("first_seen_at",),
                              touch_when_changed={"last_seen_at": "updated_at"})
            if full:
                # Events that vanished from the 30-day window while still inside it were deleted upstream.
                floor = stamp - dt.timedelta(days=self.retention_days)
                touched += delete_missing(cur, "events", "event_id", (r["event_id"] for r in rows),
                                          where=f"t.event_time >= '{floor:%Y-%m-%d %H:%M:%S}'")
                cur.execute("DELETE FROM events WHERE event_time < %s", (floor,))
                touched += cur.rowcount
                self._last_month = now
            conn.commit()
        return touched


class Nyc311(Source):
    name = "nyc311"

    def __init__(self, db: Db) -> None:
        super().__init__(db, _env_int("LIVE_NYC311_SECONDS", 900))
        self.url = _env("LIVE_NYC311_URL", "https://data.cityofnewyork.us/resource/erm2-nwe9.json")
        self.borough = _env("LIVE_NYC311_BOROUGH", "BROOKLYN")
        self.window_days = _env_int("LIVE_NYC311_WINDOW_DAYS", 30)
        self.retention_days = _env_int("LIVE_NYC311_RETENTION_DAYS", 45)
        self.page = _env_int("LIVE_NYC311_PAGE", 20000)
        self.max_pages = _env_int("LIVE_NYC311_MAX_PAGES", 40)
        self.token = _env("LIVE_SOCRATA_APP_TOKEN", "")

    def _headers(self) -> dict[str, str]:
        return {"X-App-Token": self.token} if self.token else {}

    def _scope(self) -> str:
        since = utcnow() - dt.timedelta(days=self.window_days)
        scope = f"created_date > '{since:%Y-%m-%dT%H:%M:%S}'"
        if self.borough:
            scope += f" AND borough = '{self.borough}'"
        return scope

    def _fetch_pages(self, where: str, order: str) -> Iterable[list[dict]]:
        offset = 0
        for _ in range(self.max_pages):
            batch = fetch_json(self.url, {
                "$select": "*,:id,:updated_at", "$where": where, "$order": order,
                "$limit": str(self.page), "$offset": str(offset),
            }, headers=self._headers(), timeout=180)
            if not isinstance(batch, list) or not batch:
                return
            yield batch
            if len(batch) < self.page:
                return
            offset += len(batch)

    def run(self) -> int:
        touched = 0
        with self.db.connect("nyc311") as conn, conn.cursor() as cur:
            cur.execute("SELECT MAX(socrata_updated_at) FROM service_requests")
            watermark = (cur.fetchone() or [None])[0]
            if watermark is None:
                where = self._scope()                          # first run: backfill the window
                order = "created_date, unique_key"
            else:                                             # then: whatever Socrata touched since
                floor = watermark - dt.timedelta(seconds=1)
                where = f"{self._scope()} AND :updated_at >= '{floor:%Y-%m-%dT%H:%M:%S}'"
                order = ":updated_at, unique_key"
            for batch in self._fetch_pages(where, order):
                touched += upsert(cur, "service_requests", nyc311_rows(batch), "unique_key")
                conn.commit()
            cur.execute("DELETE FROM service_requests WHERE created_date < %s",
                        (utcnow() - dt.timedelta(days=self.retention_days),))
            touched += cur.rowcount
            conn.commit()
        return touched


class Chaos(Source):
    name = "chaos"

    def __init__(self, db: Db) -> None:
        super().__init__(db, _env_int("LIVE_CHAOS_SECONDS", 120))
        self.rng = random.Random()

    def run(self) -> int:
        touched = 0
        with self.db.connect("chaos") as conn, conn.cursor() as cur:
            cur.execute("SELECT name, value FROM controls")
            controls = parse_controls(cur.fetchall())
            if controls["pause"]:
                return 0
            now = utcnow()
            cur.execute("SELECT COUNT(*), COALESCE(MAX(widget_id), 0) FROM widgets")
            count, max_id = cur.fetchone()
            if count == 0 and controls["widget_base_rows"]:
                base = [make_widget(i, self.rng, now) for i in range(1, controls["widget_base_rows"] + 1)]
                touched += insert_rows(cur, "widgets", base)
                conn.commit()
                return touched
            cur.execute("SELECT DISTINCT widget_id FROM widgets")
            ids = [row[0] for row in cur.fetchall()]
            plan = plan_chaos_tick(controls, ids, int(max_id), self.rng)

            # Schema drift: converge on exactly N extra_N columns.
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = DATABASE() "
                        "AND table_name = 'widgets' AND column_name LIKE 'extra\\_%'")
            have = sorted(int(r[0].split("_", 1)[1]) for r in cur.fetchall() if r[0].split("_", 1)[1].isdigit())
            for n in range(1, plan["drift_columns"] + 1):
                if n not in have:
                    cur.execute(f"ALTER TABLE widgets ADD COLUMN extra_{n} varchar(64) NULL")
            for n in have:
                if n > plan["drift_columns"]:
                    cur.execute(f"ALTER TABLE widgets DROP COLUMN extra_{n}")
            drift_set = ", ".join(f"extra_{n} = CONCAT('drift-', widget_id, '-', %s)"
                                  for n in range(1, plan["drift_columns"] + 1))

            if plan["identical_reload"]:
                cur.execute("SELECT * FROM widgets")
                snapshot = cur.fetchall()
                cur.execute("TRUNCATE TABLE widgets")
                if snapshot:
                    ph = ", ".join(["%s"] * len(snapshot[0]))
                    cur.executemany(f"INSERT INTO widgets VALUES ({ph})", snapshot)
                touched += len(snapshot)
                conn.commit()
                return touched

            if plan["update"]:
                for i in range(0, len(plan["update"]), 1000):
                    chunk = plan["update"][i:i + 1000]
                    marks = ", ".join(["%s"] * len(chunk))
                    sets = "qty = GREATEST(0, qty + %s), unit_price = ROUND(unit_price * %s, 2), status = %s, updated_at = %s"
                    params: list[Any] = [self.rng.randint(-25, 25), round(self.rng.uniform(0.9, 1.1), 4),
                                         self.rng.choice(WIDGET_STATUSES), now]
                    if drift_set:
                        sets += ", " + drift_set
                        params += [now.strftime("%H%M%S")] * plan["drift_columns"]
                    cur.execute(f"UPDATE widgets SET {sets} WHERE widget_id IN ({marks})", params + chunk)
                    touched += cur.rowcount
            if plan["delete"]:
                marks = ", ".join(["%s"] * len(plan["delete"]))
                cur.execute(f"DELETE FROM widgets WHERE widget_id IN ({marks})", plan["delete"])
                touched += cur.rowcount
            inserts = [make_widget(i, self.rng, now) for i in plan["insert"]]
            inserts += [make_widget(i, self.rng, now) for i in plan["dupe"]]      # same widget_id, new row
            touched += insert_rows(cur, "widgets", inserts)
            conn.commit()
        return touched


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def touch_alive() -> None:
    with open("/tmp/feeder-alive", "w", encoding="utf-8") as fh:
        fh.write(utcnow().isoformat())


def build_sources(db: Db) -> list[Source]:
    enabled = {s.strip() for s in _env("LIVE_ENABLED_SOURCES", "bikeshare,quakes,nyc311,chaos").split(",")}
    factories: dict[str, Callable[[Db], Source]] = {
        "bikeshare": Bikeshare, "quakes": Quakes, "nyc311": Nyc311, "chaos": Chaos,
    }
    return [factories[name](db) for name in factories if name in enabled]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = Db()
    for attempt in range(60):
        try:
            db.ensure_schema()
            break
        except Exception as exc:  # noqa: BLE001 — the database is still starting
            log.warning("schema not ready (%s); retrying", exc)
            time.sleep(5)
    else:
        raise SystemExit("MariaDB never became reachable")
    sources = build_sources(db)
    log.info("feeding %s into %s:%s", [s.name for s in sources], db.host, db.port)
    while True:
        touch_alive()
        for source in sources:
            if time.monotonic() < source.next_due:
                continue
            started = utcnow()
            try:
                rows = source.run()
                db.record_status(source.name, started, True, rows, None)
                log.info("%s: %s rows in %.1fs", source.name, rows, (utcnow() - started).total_seconds())
            except Exception as exc:  # noqa: BLE001 — one bad feed must not stop the others
                log.error("%s failed: %s\n%s", source.name, exc, traceback.format_exc())
                try:
                    db.record_status(source.name, started, False, 0, f"{type(exc).__name__}: {exc}")
                except Exception:  # noqa: BLE001
                    log.error("could not record failure for %s", source.name)
            source.next_due = time.monotonic() + source.interval
            touch_alive()
        time.sleep(5)


if __name__ == "__main__":
    main()
