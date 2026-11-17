-- 0370: Knowledge Loop phase 3 — the compiled layer's learned overlays.
--
-- SOUL.md and SKILL.md stay reconciler-owned repo baselines; what an agent
-- LEARNS lands here as an overlay block the reconciler composes beneath the
-- baseline (docs/KNOWLEDGE_LOOP_PLAN.md §4c). Risk-split per the adoption
-- ladder: additive KNOWLEDGE overlays adopt provisionally on proposal
-- (adopt-first); BEHAVIOR overlays wait as 'proposed' for an explicit
-- operator adoption (the one gate the doctrine keeps — behavior is not
-- cheap to be wrong about). The event ledger is the paper's skill-impact
-- audit trail: a proposal matching a previously rejected or demoted
-- overlay is refused with the prior reason, so rejected ideas are never
-- silently re-proposed.

CREATE TABLE IF NOT EXISTS rvbbit.agent_overlays (
    overlay_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_key text NOT NULL,
    target_kind text NOT NULL,
    target_key text,
    section text NOT NULL,
    body text NOT NULL,
    kind text NOT NULL,
    digest text NOT NULL,
    rationale text,
    proposed_by text NOT NULL,
    sources jsonb NOT NULL DEFAULT '[]'::jsonb,
    adoption_state text NOT NULL,
    adoption_state_changed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT agent_overlays_target_kind_check CHECK (
        target_kind IN ('soul','skill')
    ),
    CONSTRAINT agent_overlays_target_key_check CHECK (
        (target_kind='soul' AND target_key IS NULL)
        OR (target_kind='skill' AND nullif(btrim(target_key),'') IS NOT NULL)
    ),
    CONSTRAINT agent_overlays_kind_check CHECK (
        kind IN ('knowledge','behavior')
    ),
    CONSTRAINT agent_overlays_state_check CHECK (
        adoption_state IN (
            'proposed','auto_provisional','affirmed','demoted','retired','rejected'
        )
    ),
    CONSTRAINT agent_overlays_section_check CHECK (
        length(btrim(section)) BETWEEN 3 AND 120
    ),
    CONSTRAINT agent_overlays_body_check CHECK (
        length(body) BETWEEN 1 AND 8000
    ),
    CONSTRAINT agent_overlays_sources_check CHECK (jsonb_typeof(sources)='array')
);
CREATE INDEX IF NOT EXISTS agent_overlays_agent_idx
    ON rvbbit.agent_overlays (agent_key, target_kind, target_key, adoption_state);
CREATE INDEX IF NOT EXISTS agent_overlays_digest_idx
    ON rvbbit.agent_overlays (agent_key, target_kind, digest);

CREATE TABLE IF NOT EXISTS rvbbit.agent_overlay_events (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    overlay_id uuid,
    agent_key text NOT NULL,
    kind text NOT NULL,
    reason text,
    actor text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT agent_overlay_events_kind_check CHECK (
        kind IN ('proposed','adopted','rejected','demoted','retired',
                 'revived','reproposal_blocked')
    )
);
CREATE INDEX IF NOT EXISTS agent_overlay_events_agent_idx
    ON rvbbit.agent_overlay_events (agent_key, created_at DESC);

-- What the reconciler composes into profiles.
CREATE OR REPLACE VIEW rvbbit.agent_overlays_active AS
SELECT overlay_id, agent_key, target_kind, target_key, section, body,
       kind, adoption_state, updated_at
  FROM rvbbit.agent_overlays
 WHERE adoption_state IN ('auto_provisional','affirmed')
 ORDER BY agent_key, target_kind, target_key NULLS FIRST, created_at;

CREATE OR REPLACE FUNCTION rvbbit.agent_overlay_propose(
    p_agent_key text,
    p_target_kind text,
    p_target_key text,
    p_section text,
    p_body text,
    p_kind text,
    p_rationale text,
    p_proposed_by text,
    p_sources jsonb DEFAULT '[]'::jsonb
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_digest text := md5(lower(regexp_replace(coalesce(p_body,''),'\s+',' ','g')));
    v_prior record;
    v_state text;
    v_overlay rvbbit.agent_overlays%ROWTYPE;
BEGIN
    -- the audit ledger's whole point: a rejected or demoted idea does not
    -- come back unchanged. (A revised body hashes differently and may.)
    SELECT o.overlay_id, o.adoption_state,
           (SELECT e.reason FROM rvbbit.agent_overlay_events e
             WHERE e.overlay_id=o.overlay_id
               AND e.kind IN ('rejected','demoted')
             ORDER BY e.created_at DESC LIMIT 1) AS prior_reason
      INTO v_prior
      FROM rvbbit.agent_overlays o
     WHERE o.agent_key=p_agent_key AND o.target_kind=p_target_kind
       AND o.digest=v_digest
       AND o.adoption_state IN ('rejected','demoted','retired')
     ORDER BY o.updated_at DESC LIMIT 1;
    IF FOUND THEN
        INSERT INTO rvbbit.agent_overlay_events (
            overlay_id, agent_key, kind, reason, actor
        ) VALUES (
            v_prior.overlay_id, p_agent_key, 'reproposal_blocked',
            left('identical body was previously '||v_prior.adoption_state
                 ||coalesce(': '||v_prior.prior_reason,''),500),
            p_proposed_by
        );
        RETURN jsonb_build_object(
            'status','blocked',
            'prior_overlay_id',v_prior.overlay_id,
            'prior_state',v_prior.adoption_state,
            'prior_reason',v_prior.prior_reason,
            'hint','this exact overlay was already rejected or demoted; revise it against the prior reason or drop it'
        );
    END IF;

    -- an identical ACTIVE overlay is idempotent, not a duplicate.
    SELECT o.overlay_id, o.adoption_state INTO v_prior
      FROM rvbbit.agent_overlays o
     WHERE o.agent_key=p_agent_key AND o.target_kind=p_target_kind
       AND o.digest=v_digest
       AND o.adoption_state IN ('proposed','auto_provisional','affirmed')
     LIMIT 1;
    IF FOUND THEN
        RETURN jsonb_build_object(
            'status','exists','overlay_id',v_prior.overlay_id,
            'adoption_state',v_prior.adoption_state
        );
    END IF;

    v_state := CASE WHEN p_kind='knowledge' THEN 'auto_provisional' ELSE 'proposed' END;
    INSERT INTO rvbbit.agent_overlays (
        agent_key, target_kind, target_key, section, body, kind, digest,
        rationale, proposed_by, sources, adoption_state, adoption_state_changed_at
    ) VALUES (
        p_agent_key, p_target_kind, p_target_key, btrim(p_section), p_body,
        p_kind, v_digest, p_rationale, p_proposed_by,
        coalesce(p_sources,'[]'::jsonb), v_state, clock_timestamp()
    ) RETURNING * INTO v_overlay;

    INSERT INTO rvbbit.agent_overlay_events (
        overlay_id, agent_key, kind, reason, actor,
        evidence
    ) VALUES (
        v_overlay.overlay_id, p_agent_key,
        CASE WHEN v_state='auto_provisional' THEN 'adopted' ELSE 'proposed' END,
        left(coalesce(p_rationale,''),500), p_proposed_by,
        jsonb_build_object('kind',p_kind,'digest',v_digest)
    );

    RETURN jsonb_build_object(
        'status',CASE WHEN v_state='auto_provisional' THEN 'adopted' ELSE 'proposed' END,
        'overlay_id',v_overlay.overlay_id,
        'adoption_state',v_state,
        'note',CASE WHEN v_state='proposed'
            THEN 'behavior overlays wait for explicit operator adoption'
            ELSE 'knowledge overlays adopt provisionally at once' END
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.agent_overlay_set_state(
    p_overlay_id uuid,
    p_state text,
    p_reason text DEFAULT NULL,
    p_actor text DEFAULT 'operator'
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_overlay rvbbit.agent_overlays%ROWTYPE;
    v_event text;
BEGIN
    IF p_state NOT IN ('auto_provisional','affirmed','demoted','retired','rejected') THEN
        RAISE EXCEPTION 'invalid overlay state %',p_state;
    END IF;
    SELECT * INTO v_overlay FROM rvbbit.agent_overlays
     WHERE overlay_id=p_overlay_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status','missing','overlay_id',p_overlay_id);
    END IF;
    IF v_overlay.adoption_state=p_state THEN
        RETURN jsonb_build_object('status','unchanged','overlay_id',p_overlay_id,
                                  'adoption_state',p_state);
    END IF;
    UPDATE rvbbit.agent_overlays
       SET adoption_state=p_state,
           adoption_state_changed_at=clock_timestamp(),
           updated_at=clock_timestamp()
     WHERE overlay_id=p_overlay_id;
    v_event := CASE
        WHEN p_state IN ('auto_provisional','affirmed')
             AND v_overlay.adoption_state IN ('proposed','demoted','retired','rejected')
            THEN CASE WHEN v_overlay.adoption_state='proposed' THEN 'adopted' ELSE 'revived' END
        WHEN p_state='rejected' THEN 'rejected'
        WHEN p_state='demoted' THEN 'demoted'
        WHEN p_state='retired' THEN 'retired'
        ELSE 'adopted'
    END;
    INSERT INTO rvbbit.agent_overlay_events (
        overlay_id, agent_key, kind, reason, actor
    ) VALUES (
        p_overlay_id, v_overlay.agent_key, v_event,
        left(coalesce(p_reason,''),500), coalesce(p_actor,'operator')
    );
    RETURN jsonb_build_object(
        'status',p_state,'overlay_id',p_overlay_id,
        'agent_key',v_overlay.agent_key,'event',v_event
    );
END
$fn$;

COMMENT ON TABLE rvbbit.agent_overlays IS
    'Learned SOUL/SKILL overlay blocks composed beneath reconciler-owned baselines. Knowledge adopts provisionally; behavior waits for explicit adoption.';
COMMENT ON TABLE rvbbit.agent_overlay_events IS
    'The skill-impact audit ledger: every proposal, adoption, rejection, and blocked re-proposal, with reasons.';
