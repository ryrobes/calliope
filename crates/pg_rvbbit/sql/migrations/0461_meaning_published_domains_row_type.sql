-- 0461_meaning_published_domains_row_type
--
-- Sorts immediately before 0462_meaning_breakdowns. The 0461 prefix is shared
-- with 0461_calliope_computer_methods on purpose: there is no integer between
-- 0461 and 0462, and shipped migration names cannot be renumbered.
--
-- PostgreSQL 18 refuses to add a column to rvbbit.meaning_domains while
-- rvbbit.meaning_published_domains expands that table's row type:
--
--   ERROR: cannot alter table "meaning_domains" because rule _RETURN on view
--   rvbbit.meaning_published_domains uses its row type
--
-- 0462_meaning_breakdowns is the statement that adds candidate_breakdowns and
-- recreates the view with the same query. That file shipped in baseline
-- 4.2.10-calliope-0477 (v1.2.0-rc.5) and is part of baseline
-- 4.2.10-calliope-0508, so its recorded SQL is left untouched. Functions that
-- only declare meaning_domains%ROWTYPE do not block the ALTER. No other view
-- uses the row type.
--
-- Fresh history remembers the view's owner, grants, options and comment, detaches
-- meaning_areas() (the only routine that reads the view at this point) without
-- discarding that function's comment or privileges, and drops the view. 0462
-- then adds the column and recreates the view and meaning_areas(). The following
-- migration puts the saved view identity back. A database that already applied
-- 0462 still has candidate_breakdowns, so this leaves the published view alone.

DO $guard$
DECLARE
    v_owner oid;
    v_acl aclitem[];
    v_options text[];
    v_comment text;
BEGIN
    IF to_regclass('rvbbit.meaning_domains') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_attribute a
          JOIN pg_class c ON c.oid = a.attrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'rvbbit'
           AND c.relname = 'meaning_domains'
           AND a.attname = 'candidate_breakdowns'
           AND a.attnum > 0
           AND NOT a.attisdropped
    ) OR to_regclass('rvbbit.meaning_published_domains') IS NULL THEN
        RETURN;
    END IF;

    SELECT c.relowner, c.relacl, c.reloptions, obj_description(c.oid, 'pg_class')
      INTO v_owner, v_acl, v_options, v_comment
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'rvbbit'
       AND c.relname = 'meaning_published_domains';

    CREATE TEMP TABLE meaning_published_domains_rebuild (
        relowner oid,
        relacl aclitem[],
        reloptions text[],
        comment text
    ) ON COMMIT DROP;
    INSERT INTO meaning_published_domains_rebuild
    VALUES (v_owner, v_acl, v_options, v_comment);

    -- 0462 replaces this body. CREATE OR REPLACE keeps the comment and grants
    -- from 0447 while removing the view dependency so DROP VIEW can succeed
    -- on a PostgreSQL version that records it.
    CREATE OR REPLACE FUNCTION rvbbit.meaning_areas()
    RETURNS jsonb
    LANGUAGE sql
    STABLE
    AS $fn$ SELECT '[]'::jsonb $fn$;

    DROP VIEW rvbbit.meaning_published_domains;
END
$guard$;
