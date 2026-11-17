-- Inbox rows and run receipts already carry authored titles. Never spend
-- inference rewriting them, even when an old worker queued one before upgrade.
DO $policy$
DECLARE candidate_def text; refresh_def text;
BEGIN
    IF to_regprocedure('rvbbit.floor_presentation_candidates(text)') IS NOT NULL THEN
        candidate_def := pg_get_functiondef('rvbbit.floor_presentation_candidates(text)'::regprocedure);
        IF position('floor-authored-labels' in candidate_def)=0 THEN
            candidate_def := replace(candidate_def, 'FROM rvbbit.work_graph w',
                'FROM (SELECT * FROM rvbbit.work_graph WHERE item_kind NOT IN (''work_item'',''dream'',''workflow_run'')) w /* floor-authored-labels */');
            IF position('floor-authored-labels' in candidate_def)=0 THEN
                RAISE EXCEPTION 'Unrecognized Floor presentation candidate query';
            END IF;
            EXECUTE candidate_def;
        END IF;
    END IF;
    IF to_regprocedure('rvbbit.floor_presentation_refresh(text,text,text)') IS NOT NULL THEN
        refresh_def := pg_get_functiondef('rvbbit.floor_presentation_refresh(text,text,text)'::regprocedure);
        IF position('floor-authored-labels' in refresh_def)=0 THEN
            refresh_def := replace(refresh_def, E'BEGIN\n', E'BEGIN\n    -- floor-authored-labels: reject stale browser/cron queue entries too.\n    IF split_part(p_item_id,'':'',1) IN (''work_item'',''dream'',''curated_dream'',''workflow'',''workflow_run'') THEN RETURN false; END IF;\n');
            IF position('floor-authored-labels' in refresh_def)=0 THEN
                RAISE EXCEPTION 'Unrecognized Floor presentation refresh function';
            END IF;
            EXECUTE refresh_def;
        END IF;
    END IF;
END $policy$;
