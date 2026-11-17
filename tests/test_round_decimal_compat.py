"""Database regression tests for two-argument round's overload resolution.

Run against a disposable/test warehouse via RVBBIT_DSN. The migration and
test objects live in one transaction, rolled back at fixture teardown.
"""
import os
from pathlib import Path

import psycopg
import pytest


MIGRATION = (Path(__file__).resolve().parents[1] / "crates/pg_rvbbit/sql/migrations"
             / "0463_round_decimal_compat.sql").read_text()


@pytest.fixture(scope="module")
def rounding_db():
    db = psycopg.connect(os.environ.get(
        "RVBBIT_DSN", "postgresql://postgres:rvbbit@pg-rvbbit:5432/bench"))
    try:
        db.execute("SET LOCAL lock_timeout = '3s'")
        db.execute("SET LOCAL statement_timeout = '15s'")
        db.execute("SET LOCAL search_path = pg_catalog, public")
        db.execute("SET LOCAL rvbbit.force_heap_scan = on")
        catalog_before = db.execute("""
            SELECT oid, prosrc FROM pg_proc
            WHERE pronamespace='pg_catalog'::regnamespace AND proname='round'
            ORDER BY oid
        """).fetchall()
        db.execute(MIGRATION)
        db.execute(MIGRATION)  # Future image migrations can follow the hotfix.
        assert db.execute("""
            SELECT oid, prosrc FROM pg_proc
            WHERE pronamespace='pg_catalog'::regnamespace AND proname='round'
            ORDER BY oid
        """).fetchall() == catalog_before
        db.execute("SET LOCAL rvbbit.force_heap_scan = off")
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize("expression,expected", [
    ("round(1.235::double precision,2)", "1.24"),
    ("round(-1.235::double precision,2)", "-1.24"),
    ("round(2.5::double precision,0)", "3"),
    ("round(-2.5::double precision,0)", "-3"),
    ("round(1234.56::double precision,-1)", "1230"),
    ("round(2.675::real,8)", "2.67500000"),
    ("round(9007199254740993::bigint,2)", "9007199254740993.00"),
    ("round(9223372036854775807::bigint,0)", "9223372036854775807"),
    ("round(2147483647::integer,2)", "2147483647.00"),
    ("round(32767::smallint,2)", "32767.00"),
    ("round(9007199254740993.125::numeric,2)", "9007199254740993.13"),
    ("round('9007199254740993.125',2)", "9007199254740993.13"),
    ("round('9007199254740993.125'::text,2)", "9007199254740993.13"),
    ("round(NULL,2)", None),
    ("round(NULL::double precision,2)", None),
    ("round(1.235::double precision,NULL)", None),
    ("round('NaN'::double precision,2)", "NaN"),
    ("round('Infinity'::double precision,2)", "Infinity"),
    ("round('-Infinity'::double precision,2)", "-Infinity"),
])
def test_decimal_rounding_preserves_values_and_type(rounding_db, expression, expected):
    row = rounding_db.execute(
        f"SELECT ({expression})::text, pg_typeof({expression})::text"
    ).fetchone()
    assert row == (expected, "numeric")


@pytest.mark.parametrize("expression", [
    "1.5::double precision", "2.5::double precision", "-2.5::double precision",
    "1.5::numeric", "9007199254740993::bigint",
])
def test_one_argument_round_is_unchanged(rounding_db, expression):
    assert rounding_db.execute(f"""
        SELECT round({expression}) IS NOT DISTINCT FROM pg_catalog.round({expression}),
               pg_typeof(round({expression})) = pg_typeof(pg_catalog.round({expression}))
    """).fetchone() == (True, True)


def test_aggregate_expression_matches_explicit_numeric_cast(rounding_db):
    assert rounding_db.execute("""
        SELECT round(avg(x),2)::text, pg_catalog.round(avg(x)::numeric,2)::text
        FROM (VALUES (1.235::double precision),(2.345::double precision)) t(x)
    """).fetchone() == ("1.79", "1.79")


def test_invalid_text_is_not_silently_coerced(rounding_db):
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        with rounding_db.transaction():
            rounding_db.execute("SELECT round('not a number'::text,2)")


def test_unrelated_types_are_not_caught(rounding_db):
    with pytest.raises(psycopg.errors.UndefinedFunction):
        with rounding_db.transaction():
            rounding_db.execute("SELECT round(current_date,2)")


def test_migration_refuses_to_overwrite_an_unrelated_function(rounding_db):
    with pytest.raises(psycopg.errors.RaiseException, match="already exists"):
        with rounding_db.transaction():
            rounding_db.execute("COMMENT ON FUNCTION public.round(real,integer) IS 'client function'")
            rounding_db.execute(MIGRATION)
