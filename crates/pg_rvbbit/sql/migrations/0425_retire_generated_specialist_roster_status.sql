-- 0425: make rvbbit.retire_generated_specialist() work again.
--
-- 0374 wrote status='retired' onto the matching rvbbit.calliope_specialists
-- roster row; 0412 then narrowed that roster's status check to
-- ('active','paused','disabled','provisioning'), so every retirement has
-- failed on the constraint since (found 2026-09-10 while retiring the
-- thirteen pre-map "domain island" agents on the pilot — done by hand).
-- The generated row keeps its own 'retired' state; the roster row goes
-- 'disabled', which is what the Hermes reconciler and the roster already
-- understand. Re-activating is the reverse: generated 'active', roster
-- 'active' (the readiness trigger parks it in 'provisioning' until the
-- profile exists), then a Hermes restart.

CREATE OR REPLACE FUNCTION rvbbit.retire_generated_specialist(
    p_specialist_key text,
    p_reason text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
BEGIN
    UPDATE rvbbit.generated_specialists
       SET status='retired', updated_at=clock_timestamp()
     WHERE specialist_key=p_specialist_key;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status','missing','specialist_key',p_specialist_key);
    END IF;
    UPDATE rvbbit.calliope_specialists
       SET status='disabled', updated_at=now()
     WHERE specialist_key=p_specialist_key
       AND status<>'disabled';
    RETURN jsonb_build_object(
        'status','retired','specialist_key',p_specialist_key,
        'roster','disabled',
        'reason',left(coalesce(p_reason,''),300)
    );
END
$fn$;

COMMENT ON FUNCTION rvbbit.retire_generated_specialist(text, text) IS
'Retire a factory-generated domain expert: generated row → retired, roster row → disabled (the roster status check has no retired state since 0412). The Hermes reconciler drops the profile on its next restart.';
