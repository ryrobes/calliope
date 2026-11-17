-- 0415: the Resolution Ledger (docs/RESOLUTION_LEDGER_PLAN.md). One live
-- binding per company term: taught conversationally (stipulated), approved
-- from mining (affirmed), or mined-but-unratified (observed — ranking prior
-- only, never a short-circuit). Bindings are denominators, not memories:
-- they live in canon so resolution is lookup, not recall. Invalidate, never
-- edit or delete — a correction is itself a receipt. The table doubles as
-- the debug surface: browse it in DataRabbit like any governed relation.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_term_bindings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    term_norm text NOT NULL,
    scope text NOT NULL DEFAULT 'company',
    referent jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('stipulated','affirmed','observed')),
    confidence numeric,
    gloss text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    taught_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    invalidated_at timestamptz,
    invalidated_reason text
);

CREATE UNIQUE INDEX IF NOT EXISTS calliope_term_bindings_live_idx
    ON rvbbit.calliope_term_bindings (term_norm, scope)
    WHERE invalidated_at IS NULL;
