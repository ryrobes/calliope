-- 0374: the domain-agent factory — generated experts as data.
--
-- Business Topology decides which domain experts should exist
-- (deterministic eligibility over promoted value-system families, with
-- operator floors); Hermes' default profile — the smartest model on tap —
-- crafts each agent's SOUL, identity, and routing hint from the domain's
-- governed evidence; registration is atomic across all four legs of a
-- specialist (profile, roster row, routing hint, allowlist). The DB is
-- the source of truth: the Hermes reconciler fetches active generated
-- specialists over the authed internal endpoint (with a durable disk
-- cache) and synthesizes their profiles at boot, exactly like learned
-- overlays (0370).
--
-- Baselines track topology: when a family's revision advances past the
-- one a SOUL was crafted from, the factory re-crafts it (append-only
-- version history). Learned overlays compose beneath the regenerated
-- baseline, and Hindsight banks and wiki pages are untouched — the
-- agent's accumulated experience survives every definition refresh.

CREATE TABLE IF NOT EXISTS rvbbit.generated_specialists (
    specialist_key text PRIMARY KEY,
    family_id uuid NOT NULL,
    domain_key text NOT NULL UNIQUE,
    display_name text NOT NULL,
    role_label text NOT NULL,
    description text NOT NULL,
    soul_markdown text NOT NULL,
    routing_hint text NOT NULL,
    memory_mission text NOT NULL,
    capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    skills jsonb NOT NULL DEFAULT '["calliope-domain-expert","calliope-hosted-operations"]'::jsonb,
    source_family_revision integer NOT NULL,
    soul_version integer NOT NULL DEFAULT 1,
    status text NOT NULL DEFAULT 'active',
    crafted_by text NOT NULL DEFAULT 'domain-agent-factory',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT generated_specialists_key_check CHECK (
        specialist_key ~ '^[a-z0-9][a-z0-9-]{1,62}$'
    ),
    CONSTRAINT generated_specialists_status_check CHECK (
        status IN ('active','retired')
    ),
    CONSTRAINT generated_specialists_soul_check CHECK (
        length(soul_markdown) BETWEEN 200 AND 20000
    ),
    CONSTRAINT generated_specialists_hint_check CHECK (
        length(routing_hint) BETWEEN 20 AND 800
    )
);

CREATE TABLE IF NOT EXISTS rvbbit.generated_specialist_versions (
    specialist_key text NOT NULL
        REFERENCES rvbbit.generated_specialists(specialist_key) ON DELETE CASCADE,
    soul_version integer NOT NULL,
    source_family_revision integer NOT NULL,
    soul_markdown text NOT NULL,
    routing_hint text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (specialist_key, soul_version)
);

-- Operator floors for agent minting.
ALTER TABLE rvbbit.knowledge_loop_policy
    ADD COLUMN IF NOT EXISTS agent_min_assertions integer NOT NULL DEFAULT 20
        CHECK (agent_min_assertions >= 1),
    ADD COLUMN IF NOT EXISTS agent_max_count integer NOT NULL DEFAULT 12
        CHECK (agent_max_count BETWEEN 0 AND 50);
INSERT INTO rvbbit.knowledge_loop_policy (kind)
VALUES ('domain_agent') ON CONFLICT (kind) DO NOTHING;

-- routing_hint joins the roster row: the generated delegate-tool doc
-- prefers it over the static preference dictionary.
ALTER TABLE rvbbit.calliope_specialists
    ADD COLUMN IF NOT EXISTS routing_hint text;

-- Domains that deserve an agent and don't have one. Convention: the
-- specialist key is the domain key with underscores dashed.
CREATE OR REPLACE VIEW rvbbit.domain_agent_candidates AS
SELECT summary.family_id, summary.domain_key, summary.name,
       summary.family_revision, summary.graph_id,
       (SELECT count(*) FROM rvbbit.business_semantic_assertions a
         WHERE a.family_id=summary.family_id) AS assertions
  FROM rvbbit.business_value_graph_summary summary
 WHERE summary.status='promoted'
   AND summary.validated_metric_count > 0
   AND replace(summary.domain_key,'_','-') NOT IN (
       SELECT specialist_key FROM rvbbit.calliope_specialists
        WHERE status='active')
   AND NOT EXISTS (
       SELECT 1 FROM rvbbit.generated_specialists g
        WHERE g.domain_key=summary.domain_key AND g.status='retired');

-- Atomic registration: generated row + version + roster row.
CREATE OR REPLACE FUNCTION rvbbit.register_generated_specialist(
    p_specialist_key text,
    p_family_id uuid,
    p_domain_key text,
    p_display_name text,
    p_role_label text,
    p_description text,
    p_soul_markdown text,
    p_routing_hint text,
    p_memory_mission text,
    p_capabilities jsonb,
    p_source_family_revision integer
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_version integer;
BEGIN
    INSERT INTO rvbbit.generated_specialists (
        specialist_key, family_id, domain_key, display_name, role_label,
        description, soul_markdown, routing_hint, memory_mission,
        capabilities, source_family_revision
    ) VALUES (
        p_specialist_key, p_family_id, p_domain_key, p_display_name,
        p_role_label, p_description, p_soul_markdown, p_routing_hint,
        p_memory_mission, coalesce(p_capabilities,'[]'::jsonb),
        p_source_family_revision
    )
    ON CONFLICT (specialist_key) DO UPDATE SET
        display_name=EXCLUDED.display_name,
        role_label=EXCLUDED.role_label,
        description=EXCLUDED.description,
        soul_markdown=EXCLUDED.soul_markdown,
        routing_hint=EXCLUDED.routing_hint,
        memory_mission=EXCLUDED.memory_mission,
        capabilities=EXCLUDED.capabilities,
        source_family_revision=EXCLUDED.source_family_revision,
        soul_version=rvbbit.generated_specialists.soul_version+1,
        status='active',
        updated_at=clock_timestamp()
    RETURNING soul_version INTO v_version;

    INSERT INTO rvbbit.generated_specialist_versions (
        specialist_key, soul_version, source_family_revision,
        soul_markdown, routing_hint
    ) VALUES (
        p_specialist_key, v_version, p_source_family_revision,
        p_soul_markdown, p_routing_hint
    ) ON CONFLICT (specialist_key, soul_version) DO NOTHING;

    INSERT INTO rvbbit.calliope_specialists (
        id, specialist_key, profile_name, display_name, description,
        capabilities, default_schedule, managed, routing_hint
    ) VALUES (
        md5('calliope-specialist:'||p_specialist_key)::uuid,
        p_specialist_key, p_specialist_key, p_display_name, p_description,
        coalesce(p_capabilities,'[]'::jsonb), NULL, true, p_routing_hint
    )
    ON CONFLICT (specialist_key) DO UPDATE SET
        display_name=EXCLUDED.display_name,
        description=EXCLUDED.description,
        capabilities=EXCLUDED.capabilities,
        routing_hint=EXCLUDED.routing_hint,
        status='active',
        updated_at=now();

    RETURN jsonb_build_object(
        'status','registered','specialist_key',p_specialist_key,
        'soul_version',v_version
    );
END
$fn$;

-- Retirement: the domain demoted, or an operator pruning the bench. The
-- profile directory, Hindsight bank, and wiki pages are deliberately
-- left in place — memory survives for revival.
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
       SET status='retired', updated_at=now()
     WHERE specialist_key=p_specialist_key;
    RETURN jsonb_build_object(
        'status','retired','specialist_key',p_specialist_key,
        'reason',left(coalesce(p_reason,''),300)
    );
END
$fn$;

COMMENT ON TABLE rvbbit.generated_specialists IS
    'Topology-derived domain-expert agents as data: Hermes-crafted SOULs, versioned, served to the Hermes reconciler over the internal endpoint. Baselines regenerate when the family revision advances; overlays and memory survive.';
