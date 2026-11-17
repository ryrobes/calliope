"""Briarpatch ring placement must terminate for any schema count.

The 2026-09-11 pilot had 21 schemas feeding the map; the old nudge loop
required a 52° gap around each placed slice plus the wilds sector, which only
fits six slices on a ring, so the first tick after boot spun forever and held
the GIL at 100% for the life of the server process.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("WAREHOUSE_DSN", "postgresql://x")

import briarpatch  # noqa: E402


def _place_all(schemas: list[str]) -> list[tuple[float, str]]:
    placed: list[tuple[float, str]] = []
    for angle, schema in sorted(((briarpatch._ring_distance(0.0, 0.0) + i * 3.0) % 360.0, s) for i, s in enumerate(schemas)):
        placed.append((briarpatch._place_angle(angle, placed), schema))
    return placed


def test_twenty_one_schemas_place_quickly_and_uniquely():
    schemas = [f"schema_{i}" for i in range(21)]
    started = time.monotonic()
    placed = _place_all(schemas)
    assert time.monotonic() - started < 1.0
    angles = [round(a, 3) for a, _ in placed]
    assert len(set(angles)) == len(angles)
    assert all(0.0 <= a < 360.0 for a in angles)


def test_small_sets_keep_the_preferred_gap_and_avoid_the_wilds():
    placed = _place_all([f"s{i}" for i in range(5)])
    for i, (a, _) in enumerate(placed):
        assert briarpatch._ring_distance(a, briarpatch.WILDS_ANGLE) >= briarpatch.SCHEMA_MIN_GAP_DEG
        for b, _ in placed[:i]:
            assert briarpatch._ring_distance(a, b) >= briarpatch.SCHEMA_MIN_GAP_DEG


def test_placement_is_deterministic():
    schemas = [f"schema_{i}" for i in range(12)]
    assert _place_all(schemas) == _place_all(schemas)


def test_first_clear_candidate_wins_when_the_ring_has_room():
    # An angle already clear of the wilds sector is returned untouched.
    assert briarpatch._place_angle(200.0, []) == 200.0
    # One that sits on the wilds sector is nudged in 7° steps until clear.
    nudged = briarpatch._place_angle(briarpatch.WILDS_ANGLE, [])
    assert briarpatch._ring_distance(nudged, briarpatch.WILDS_ANGLE) >= briarpatch.SCHEMA_MIN_GAP_DEG
    assert (nudged - briarpatch.WILDS_ANGLE) % briarpatch.SCHEMA_NUDGE_DEG == 0
