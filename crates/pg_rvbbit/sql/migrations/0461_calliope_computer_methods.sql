-- Passive learning from archived Computer work. No method is executed here.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_computer_learning_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    enabled_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO rvbbit.calliope_computer_learning_settings(singleton) VALUES(true) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS rvbbit.calliope_computer_reviews (
    run_id uuid PRIMARY KEY REFERENCES rvbbit.calliope_desktop_runs(id),
    watermark text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','complete','failed')),
    attempts integer NOT NULL DEFAULT 0,
    lease uuid,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    result jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_computer_methods (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    desktop_id uuid NOT NULL REFERENCES rvbbit.calliope_desktops(id),
    actor_email text NOT NULL,
    method_key text NOT NULL,
    version integer NOT NULL DEFAULT 1,
    status text NOT NULL DEFAULT 'tentative' CHECK(status IN ('tentative','established','needs_review','retired')),
    successes integer NOT NULL DEFAULT 0,
    failures integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(desktop_id,actor_email,method_key)
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_computer_method_versions (
    method_id uuid NOT NULL REFERENCES rvbbit.calliope_computer_methods(id),
    version integer NOT NULL,
    run_id uuid NOT NULL REFERENCES rvbbit.calliope_desktop_runs(id),
    body jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    retention_status text NOT NULL DEFAULT 'pending' CHECK(retention_status IN ('pending','retained','failed')),
    retention_result jsonb,
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(method_id,version)
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_computer_method_evaluations (
    method_id uuid NOT NULL REFERENCES rvbbit.calliope_computer_methods(id),
    run_id uuid NOT NULL REFERENCES rvbbit.calliope_desktop_runs(id),
    version integer NOT NULL,
    outcome text NOT NULL CHECK(outcome IN ('supported','failed','uncertain')),
    used boolean NOT NULL,
    evidence jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(method_id,run_id),
    FOREIGN KEY(method_id,version) REFERENCES rvbbit.calliope_computer_method_versions(method_id,version)
);
CREATE INDEX IF NOT EXISTS calliope_computer_methods_scope
    ON rvbbit.calliope_computer_methods(desktop_id,actor_email,updated_at DESC);
