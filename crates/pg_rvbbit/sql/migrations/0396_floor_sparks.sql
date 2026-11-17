-- 0396: Floor sparks — the arrival band's substrate.
--
-- Sparks are doors, not plans: lightweight per-user invitations that point
-- at something that ALREADY EXISTS (an unseen artifact, unvisited territory,
-- a specialist never met). Dreams propose work; sparks propose engagement —
-- that structural rule is what keeps the two tiers from duplicating each
-- other. kinds: 'idea' (personalized doors), 'welcome' (new-user ladder,
-- retire-on-first-action), 'broadcast' (reserved: audience-scoped flares),
-- 'present' (reserved: the dream-builder's reveal publishes here).
--
-- Lifecycle laws (the band's doctrine): everything decays (valid_until),
-- nothing re-nags (the dedupe ledger keeps hidden/expired rows forever, so
-- a spark is offered once), every spark is a door (prompt/action), and
-- summoning is receipted (created_by) and rationed (deterministic tick).
-- v1 generation is pure SQL over canon — zero tokens, personalized by
-- construction; an LLM "host" pass can join later as another created_by.

CREATE TABLE IF NOT EXISTS rvbbit.calliope_floor_sparks (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind          text NOT NULL CHECK (kind IN ('idea','welcome','broadcast','present')),
    owner_email   text,                       -- NULL reserved for audience-scoped broadcasts
    audience      jsonb NOT NULL DEFAULT '{}'::jsonb,
    kicker        text NOT NULL DEFAULT 'A spark',
    title         text NOT NULL,
    summary       text NOT NULL DEFAULT '',
    prompt        text NOT NULL DEFAULT '',
    meta          jsonb NOT NULL DEFAULT '[]'::jsonb,
    render_mode   text NOT NULL DEFAULT 'prompt'
                  CHECK (render_mode IN ('prompt','artifact','metric','dream')),
    action        jsonb NOT NULL DEFAULT '{}'::jsonb,
    thumbnail_url text,
    dedupe_key    text NOT NULL,
    created_by    text NOT NULL DEFAULT 'spark_host',
    retire_condition text,                    -- welcome ladder: e.g. 'first_chat'
    state         text NOT NULL DEFAULT 'live'
                  CHECK (state IN ('live','entered','hidden','expired','retired')),
    impressions   integer NOT NULL DEFAULT 0,
    entered_at    timestamptz,
    hidden_at     timestamptz,
    hidden_reason text,
    valid_until   timestamptz NOT NULL DEFAULT now() + interval '10 days',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_floor_sparks_dedupe_idx
    ON rvbbit.calliope_floor_sparks (lower(coalesce(owner_email,'')), dedupe_key);
CREATE INDEX IF NOT EXISTS calliope_floor_sparks_serve_idx
    ON rvbbit.calliope_floor_sparks (lower(owner_email), state, valid_until);

CREATE OR REPLACE FUNCTION rvbbit.spark_host_tick()
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    expired integer := 0;
    retired integer := 0;
    social integer := 0;
    territory integer := 0;
    specialists integer := 0;
    welcome integer := 0;
BEGIN
    -- Decay pass: the band never goes stale because nothing outlives its TTL.
    UPDATE rvbbit.calliope_floor_sparks
       SET state='expired', updated_at=now()
     WHERE state='live' AND valid_until <= now();
    GET DIAGNOSTICS expired = ROW_COUNT;

    -- Welcome ladder retirement: orientation tiles step aside the moment the
    -- user takes the action they pointed at.
    UPDATE rvbbit.calliope_floor_sparks s
       SET state='retired', updated_at=now()
     WHERE s.state='live' AND s.kind='welcome'
       AND s.retire_condition='first_chat'
       AND EXISTS (SELECT 1 FROM rvbbit.calliope_sessions cs
                    WHERE lower(cs.owner_email)=lower(s.owner_email));
    GET DIAGNOSTICS retired = ROW_COUNT;

    -- 1. Org-social publishes: artifacts updated this week that the user may
    --    view (artifacts start private; artifact_can_view enforces sharing)
    --    and did not author. New version => new dedupe key => fresh spark.
    INSERT INTO rvbbit.calliope_floor_sparks
        (kind, owner_email, kicker, title, summary, prompt, meta, render_mode,
         action, thumbnail_url, dedupe_key, valid_until)
    SELECT 'idea', p.email, 'New in the gallery',
           left(coalesce(nullif(d.name,''), d.slug), 240),
           left('Published by ' || split_part(coalesce(nullif(d.owner_email,''),'the team'),'@',1)
                || coalesce('. ' || nullif(d.description,''), '.'), 600),
           'Walk me through the artifact “' || coalesce(nullif(d.name,''), d.slug)
                || '” — what it shows, what is newest in it, and what I could ask next.',
           jsonb_build_array(d.app_kind, 'v' || d.latest_version),
           'artifact',
           jsonb_build_object('kind','artifact','slug', d.slug),
           CASE WHEN lower(coalesce(d.runtime_kind,'html'))='html'
                THEN '/thumbs/' || CASE WHEN d.app_kind='dashboard' THEN 'dashboard' ELSE 'app' END
                     || '/' || d.slug || '.png' END,
           'social:' || d.slug || ':v' || d.latest_version,
           now() + interval '7 days'
      FROM rvbbit.dashboards d
      JOIN rvbbit.application_principals p ON true
     WHERE NOT d.archived
       AND d.updated_at > now() - interval '7 days'
       AND lower(coalesce(d.owner_email,'')) <> lower(p.email)
       AND rvbbit.artifact_can_view(d.id, p.email, false)
    ON CONFLICT (lower(coalesce(owner_email,'')), dedupe_key) DO NOTHING;
    GET DIAGNOSTICS social = ROW_COUNT;

    -- 2. Unvisited territory: promoted domains this user's sessions have
    --    never touched (work_session_domains is the geography evidence).
    --    Offered once, ever — the dedupe ledger is the no-nagging guarantee.
    IF to_regclass('rvbbit.business_value_graph_summary') IS NOT NULL
       AND to_regclass('rvbbit.work_session_domains') IS NOT NULL THEN
        INSERT INTO rvbbit.calliope_floor_sparks
            (kind, owner_email, kicker, title, summary, prompt, meta, dedupe_key, valid_until)
        SELECT 'idea', p.email, 'Territory you have not walked',
               left(coalesce(nullif(g.name,''), replace(g.domain_key,'_',' ')), 240),
               left(coalesce(g.validated_metric_count,0) || ' governed metrics live here and you have never asked about them.', 600),
               'Introduce me to the “' || coalesce(nullif(g.name,''), g.domain_key)
                   || '” domain — what it covers, its key metrics, and one question worth asking first.',
               jsonb_build_array('domain', coalesce(g.validated_metric_count,0) || ' metrics'),
               'territory:' || g.domain_key,
               now() + interval '14 days'
          FROM rvbbit.business_value_graph_summary g
          JOIN rvbbit.application_principals p ON true
         WHERE g.status = 'promoted'
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.work_session_domains wsd
               JOIN rvbbit.calliope_sessions cs ON cs.id = wsd.session_id
              WHERE lower(cs.owner_email)=lower(p.email)
                AND wsd.domain_key = g.domain_key)
           AND EXISTS (SELECT 1 FROM rvbbit.calliope_sessions cs2
                        WHERE lower(cs2.owner_email)=lower(p.email))
        ON CONFLICT (lower(coalesce(owner_email,'')), dedupe_key) DO NOTHING;
        GET DIAGNOSTICS territory = ROW_COUNT;
    END IF;

    -- 3. Meet the specialist: active domain experts whose territory overlaps
    --    the user's geography, whom this user has never worked with.
    IF to_regclass('rvbbit.work_session_domains') IS NOT NULL THEN
        INSERT INTO rvbbit.calliope_floor_sparks
            (kind, owner_email, kicker, title, summary, prompt, meta, dedupe_key, valid_until)
        SELECT DISTINCT ON (p.email, sp.specialist_key)
               'idea', p.email, 'Someone to meet',
               left(coalesce(nullif(sp.display_name,''), sp.specialist_key), 240),
               left('Keeps ' || replace(sp.specialist_key,'-',' ')
                    || ' honest — and you two have never worked a thread together.', 600),
               'Introduce me to ' || coalesce(nullif(sp.display_name,''), sp.specialist_key)
                   || ' — what they cover, and delegate one small question to them so I can see how that works.',
               jsonb_build_array('specialist'),
               'specialist:' || sp.specialist_key,
               now() + interval '14 days'
          FROM rvbbit.calliope_specialists sp
          JOIN rvbbit.application_principals p ON true
         WHERE sp.status='active'
           AND sp.capabilities @> '["domain_expert"]'::jsonb
           AND EXISTS (
               SELECT 1 FROM rvbbit.work_session_domains wsd
               JOIN rvbbit.calliope_sessions cs ON cs.id = wsd.session_id
              WHERE lower(cs.owner_email)=lower(p.email)
                AND wsd.domain_key = replace(sp.specialist_key,'-','_'))
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.calliope_specialist_threads th
              WHERE lower(th.owner_email)=lower(p.email)
                AND th.specialist_id = sp.id)
        ON CONFLICT (lower(coalesce(owner_email,'')), dedupe_key) DO NOTHING;
        GET DIAGNOSTICS specialists = ROW_COUNT;
    END IF;

    -- 4. Welcome ladder for principals with little or no history: org-specific
    --    orientation, retiring as first actions happen.
    INSERT INTO rvbbit.calliope_floor_sparks
        (kind, owner_email, kicker, title, summary, prompt, meta,
         retire_condition, dedupe_key, valid_until)
    SELECT 'welcome', p.email, 'Welcome in',
           'Take the tour',
           'A plain-English walk through the data, dashboards, and company knowledge Calliope can reach.',
           'Give me a plain-English tour of the data, dashboards, and company knowledge available here, then suggest one useful place to start.',
           '["orientation"]'::jsonb,
           'first_chat', 'welcome:tour', now() + interval '60 days'
      FROM rvbbit.application_principals p
     WHERE NOT EXISTS (SELECT 1 FROM rvbbit.calliope_sessions cs
                        WHERE lower(cs.owner_email)=lower(p.email))
    ON CONFLICT (lower(coalesce(owner_email,'')), dedupe_key) DO NOTHING;
    GET DIAGNOSTICS welcome = ROW_COUNT;

    RETURN jsonb_build_object(
        'expired', expired, 'retired', retired, 'social', social,
        'territory', territory, 'specialists', specialists, 'welcome', welcome);
END $$;

-- Same cron-home discipline as rvbbit.schedule_metadata_vacuum: this only
-- registers the job; pg_cron may live in another database. Operators rarely
-- need it: the Calliope server's boot hook (_ensure_spark_host_cron in
-- server.py) crosses to the cron home and registers the job idempotently,
-- so fresh installs wake up with the band already breathing. This helper
-- remains the manual/recovery path.
CREATE OR REPLACE FUNCTION rvbbit.schedule_spark_host(
    cron_schedule text DEFAULT '20 */4 * * *'
) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    jobid     bigint;
    cron_home text := current_setting('cron.database_name', true);
    this_db   text := current_database();
    command   text := 'SELECT rvbbit.spark_host_tick()';
BEGIN
    IF cron_home IS NOT NULL AND cron_home <> '' AND cron_home <> this_db THEN
        RAISE EXCEPTION 'pg_cron home database is %, not %; cron.* is not callable here.',
            cron_home, this_db
            USING HINT = format(
                'Connect to %L and run: SELECT cron.schedule_in_database(%L, %L, %L, %L);',
                cron_home, 'rvbbit_spark_host', cron_schedule, command, this_db);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        RAISE EXCEPTION 'pg_cron is not installed; run rvbbit.spark_host_tick() from any scheduler.';
    END IF;
    EXECUTE format('SELECT cron.schedule(%L, %L, %L)',
                   'rvbbit_spark_host', cron_schedule, command)
        INTO jobid;
    RETURN jobid;
END $$;
