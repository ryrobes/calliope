"""Callie's tools and the database agree on which schemas are the appliance's own."""
import re
from pathlib import Path

import server

MIGRATION = Path(__file__).resolve().parents[2] / "crates/pg_rvbbit/sql/migrations/0496_new_data_shows_up.sql"


def _sql_list():
    body = MIGRATION.read_text()
    function = body[body.index("FUNCTION rvbbit.is_system_schema"):body.index("$fn$;")]
    listed = function[function.index("p_schema IN ("):]
    names = set(re.findall(r"'([a-z0-9_]+)'", listed[:listed.index(")")]))
    prefixes = {match.replace("\\_", "_").rstrip("%") for match in re.findall(r"LIKE '([^']+)'", function)}
    return names, prefixes


def test_the_python_and_sql_deny_lists_match():
    names, prefixes = _sql_list()
    assert names == server._DENY_SCHEMAS
    assert prefixes == set(server._DENY_SCHEMA_PREFIXES)


def test_new_data_shows_up_and_the_appliance_stays_out():
    for schema in ("shop", "public", "fieldwork", "calliope", "cubes"):
        assert server._schema_allowed(schema), schema
    for schema in ("rvbbit", "hindsight", "clover_telemetry", "clover_research", "_dlt_loads", "pg_temp_3",
                   "information_schema", "meaning_labels"):
        assert not server._schema_allowed(schema), schema
