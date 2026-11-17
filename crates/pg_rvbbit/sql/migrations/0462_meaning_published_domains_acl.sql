-- 0462_meaning_published_domains_acl
--
-- Sorts immediately after 0462_meaning_breakdowns. That migration recreates
-- rvbbit.meaning_published_domains with the same query once candidate_breakdowns
-- exists. This puts back the owner, grants, view options and comment saved by
-- 0461_meaning_published_domains_row_type. No saved row means the view was not
-- dropped, which is the upgrade from a database that already applied 0462.

DO $restore$
DECLARE
    v_view oid := to_regclass('rvbbit.meaning_published_domains');
    v_owner oid;
    v_acl aclitem[];
    v_options text[];
    v_comment text;
BEGIN
    IF to_regclass('pg_temp.meaning_published_domains_rebuild') IS NULL THEN
        RETURN;
    END IF;
    SELECT relowner, relacl, reloptions, comment
      INTO v_owner, v_acl, v_options, v_comment
      FROM meaning_published_domains_rebuild
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    IF v_view IS NULL THEN
        RAISE EXCEPTION 'meaning_published_domains was not recreated by 0462_meaning_breakdowns';
    END IF;

    UPDATE pg_class
       SET relowner = v_owner,
           relacl = v_acl,
           reloptions = v_options
     WHERE oid = v_view;
    EXECUTE format('COMMENT ON VIEW rvbbit.meaning_published_domains IS %L', v_comment);
END
$restore$;
