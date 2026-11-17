-- 0488: a refresh that can't get a heavy slot no longer stops the worker's sweep.
--
-- When a table's delta refresh fails (it has UPDATEs/DELETEs) the tick falls
-- back to a full rebuild, which needs one of the shared heavy slots. If none is
-- free it defers the table, correctly, but then ended the sweep as if it had
-- done its one action, and the worker pass stopped because nothing completed.
-- A recently read table that keeps changing sorts first, so on the hub it sat at
-- the head of the queue at the top of each minute (when the layout workers hold
-- the slots) and nothing behind it ran. The sweep now moves on to the next
-- candidate, and later full rebuilds in the same sweep don't retry the slots.

DO $migration$
DECLARE
    definition text;
    needle text;
    replacement text;
BEGIN
    definition := pg_get_functiondef(
        'rvbbit._accel_tick_batch(integer,boolean,integer)'::regprocedure
    );
    IF position('fallback deferral keeps the sweep going' IN definition) > 0 THEN
        RETURN;
    END IF;

    needle := $needle$                        fallback_deferred := true;
                        act_reason := act_reason || '; heavy maintenance slots busy';$needle$;
    replacement := $replacement$                        fallback_deferred := true;
                        heavy_capacity_deferred := true;
                        act_reason := act_reason || '; heavy maintenance slots busy';$replacement$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0488 could not find the full-rebuild fallback deferral';
    END IF;
    definition := replace(definition, needle, replacement);

    needle := $needle$        RETURN NEXT;
        -- One worker invocation owns at most one table action.  The outer
        -- worker-pass procedure commits before requesting the next table.
        EXIT;$needle$;
    replacement := $replacement$        RETURN NEXT;
        -- A delta that fell back to a full rebuild without a heavy slot did
        -- nothing (fallback deferral keeps the sweep going): try cheaper work.
        IF fallback_deferred THEN
            CONTINUE;
        END IF;
        -- One worker invocation owns at most one table action.  The outer
        -- worker-pass procedure commits before requesting the next table.
        EXIT;$replacement$;
    IF position(needle IN definition) = 0 THEN
        RAISE EXCEPTION '0488 could not find the end of the table action';
    END IF;
    definition := replace(definition, needle, replacement);

    EXECUTE definition;
END
$migration$;
