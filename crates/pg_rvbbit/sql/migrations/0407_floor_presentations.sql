-- Durable Floor presentation cache. Snapshot capture remains independent of inference.
CREATE TABLE IF NOT EXISTS rvbbit.floor_presentations (
    item_id text PRIMARY KEY,
    source_hash text NOT NULL,
    semantic_title text,
    semantic_status text,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    retry_after timestamptz
);
COMMENT ON TABLE rvbbit.floor_presentations IS
    'Current generated Floor labels; only serve after joining authorized current candidates with the matching source hash.';
CREATE OR REPLACE FUNCTION rvbbit.floor_presentation_prompt() RETURNS text
LANGUAGE sql IMMUTABLE AS $prompt$ SELECT 'title string: specific natural 3-to-8-word noun phrase naming the enduring project, artifact, dataset, decision, or outcome; never a sentence, activity report, or generic Calliope work label. status string: one concrete present-tense clause, maximum 11 words, saying what the human needs to do for awaiting_you, the current step for in_motion, or the shipped outcome for done. Prefer receipt numbers, actions, and outcomes over vague prose. Do not repeat the title. No markdown.'::text $prompt$;
CREATE OR REPLACE FUNCTION rvbbit.floor_presentation_candidates(p_owner text DEFAULT NULL)
RETURNS TABLE(item_id text,column_state text,lane_rank bigint,source_text text,source_hash text)
LANGUAGE sql STABLE AS $candidates$
WITH ranked AS (
    SELECT w.*,coalesce(src.user_message,latest.user_message) AS user_context, coalesce(src.assistant_message,latest.assistant_message) AS assistant_context, row_number() OVER (PARTITION BY CASE WHEN p_owner IS NULL THEN w.owner_email END,w.column_state  ORDER BY w.attention_rank DESC,w.last_activity_at DESC NULLS LAST) AS lane_rank
    FROM rvbbit.work_graph w
    LEFT JOIN rvbbit.calliope_turns src ON src.id::text=w.ref->>'source_ref'
    LEFT JOIN LATERAL (SELECT t.user_message,t.assistant_message FROM rvbbit.calliope_turns t WHERE w.item_kind='chat' AND t.session_id::text=split_part(w.item_id,':',2) ORDER BY t.ordinal DESC LIMIT 1) latest ON true
    WHERE (p_owner IS NULL OR (
        (
            w.owner_email IS NULL
            OR lower(w.owner_email) = lower(p_owner)
            OR (
                w.item_kind = 'chat'
                AND EXISTS (
                    SELECT 1
                    FROM rvbbit.calliope_sessions visible_session
                    WHERE visible_session.id::text = split_part(w.item_id, ':', 2)
                      AND rvbbit.calliope_session_can_view(
                          visible_session.id, p_owner, false
                      )
                )
            )
            OR (
                -- Approved playbooks published to everyone (factory canon,
                -- deliberately shared SOPs) are shared furniture: every
                -- Floor shows them in Standing regardless of owner.
                w.item_kind = 'playbook'
                AND EXISTS (
                    SELECT 1
                    FROM rvbbit.calliope_playbooks everyone_pb
                    JOIN rvbbit.capability_access_policies everyone_cap
                      ON everyone_cap.capability_kind = 'cap_playbook'
                     AND everyone_cap.capability_name = everyone_pb.capability_name
                    WHERE everyone_pb.id::text = split_part(w.item_id, ':', 2)
                      AND everyone_pb.approved_version IS NOT NULL
                      AND everyone_cap.visibility = 'everyone'
                )
            )
        )
        AND (
            w.item_kind <> 'chat'
            OR EXISTS (
                SELECT 1
                FROM rvbbit.calliope_turns visible_turn
                WHERE visible_turn.session_id::text = split_part(w.item_id, ':', 2)
                  AND visible_turn.turn_kind IN ('chat', 'agent_message')
            )
        )
    )) AND w.column_state IN ('awaiting_you','in_motion','done')), candidates AS (
    SELECT item_id,column_state,lane_rank,
        concat_ws(E'\n', 'lane='||column_state,
        'kind='||item_kind, 'current title='||coalesce(display_title,''), 'state evidence='||coalesce(state_reason,''), 'summary='||left(coalesce(summary,''),2200), 'action needed='||left(coalesce(ref->>'action_prompt',''),1200), 'latest user context='||left(coalesce(user_context,''),1800), 'latest assistant progress='||left(coalesce(assistant_context,''),1800), 'active='||active_now,'active collaborators='||active_child_count, 'signals='||related_signal_count,'artifacts='||surface_count,
        CASE WHEN column_state='in_motion' THEN 'freshness bucket='|| floor(extract(epoch FROM now())/1800)::text END) AS source_text
    FROM ranked WHERE (column_state='awaiting_you' AND lane_rank<=20) OR (column_state='in_motion' AND lane_rank<=5) OR (column_state='done' AND lane_rank<=25)
    ORDER BY CASE column_state WHEN 'awaiting_you' THEN 1 WHEN 'in_motion' THEN 2 ELSE 3 END,lane_rank)
SELECT c.item_id,c.column_state,c.lane_rank,c.source_text,
       md5(c.source_text || rvbbit.floor_presentation_prompt() ||
           jsonb_build_array(o.model,o.system_prompt,o.user_prompt,o.steps)::text) AS source_hash
FROM candidates c CROSS JOIN rvbbit.operators o WHERE o.name='clover_llm_extract'
$candidates$;

CREATE OR REPLACE FUNCTION rvbbit.floor_presentation_refresh(
    p_item_id text,p_source_text text,p_source_hash text)
RETURNS boolean LANGUAGE plpgsql AS $refresh$
DECLARE v_result jsonb; v_title text; v_status text;
BEGIN
    -- Cron and browser-triggered work share one nonblocking lock per item.
    IF NOT pg_try_advisory_xact_lock(hashtextextended('floor-presentation:'||p_item_id,0)) THEN
        RETURN false;
    END IF;
    IF EXISTS (SELECT 1 FROM rvbbit.floor_presentations
        WHERE item_id=p_item_id AND source_hash=p_source_hash
          AND (semantic_title IS NOT NULL OR retry_after>now())) THEN RETURN false; END IF;
    v_result := rvbbit.clover_llm_extract(p_source_text,rvbbit.floor_presentation_prompt());
    v_title := trim(v_result->>'title'); v_status := trim(v_result->>'status');
    IF coalesce(length(v_title),0) NOT BETWEEN 3 AND 90
       OR coalesce(length(v_status),0) NOT BETWEEN 3 AND 180
       OR v_title ~* '^(null|calliope work( needs attention)?|server reboot(ed)? again|transport failure|connection error|parent workstream)$'
    THEN v_title := NULL; v_status := NULL; END IF;
    INSERT INTO rvbbit.floor_presentations(item_id,source_hash,semantic_title,semantic_status,retry_after)
    VALUES(p_item_id,p_source_hash,v_title,v_status,
           CASE WHEN v_title IS NULL THEN clock_timestamp()+interval '5 minutes' END)
    ON CONFLICT(item_id) DO UPDATE SET source_hash=excluded.source_hash,
        semantic_title=excluded.semantic_title,semantic_status=excluded.semantic_status,
        updated_at=clock_timestamp(),retry_after=excluded.retry_after;
    RETURN v_title IS NOT NULL;
END $refresh$;

CREATE OR REPLACE PROCEDURE rvbbit.floor_presentations_warm(p_limit integer DEFAULT 100)
LANGUAGE plpgsql AS $warm$
DECLARE r record; v_start timestamptz := clock_timestamp();
BEGIN
    -- Commit each item: cancellation does not discard earlier successful labels.
    -- pg_cron serializes this job; per-item locks also deduplicate live requests.
    FOR r IN
        SELECT c.* FROM rvbbit.floor_presentation_candidates(NULL) c
        LEFT JOIN rvbbit.floor_presentations p USING(item_id)
        WHERE p.source_hash IS DISTINCT FROM c.source_hash
           OR (p.semantic_title IS NULL AND coalesce(p.retry_after,'-infinity')<=now())
        ORDER BY CASE c.column_state WHEN 'awaiting_you' THEN 0 WHEN 'in_motion' THEN 1 ELSE 2 END,
                 c.lane_rank,c.item_id
        LIMIT greatest(1,least(coalesce(p_limit,100),200))
    LOOP
        PERFORM rvbbit.floor_presentation_refresh(r.item_id,r.source_text,r.source_hash);
        COMMIT;
        -- Bound each pass between calls; an individual call retains its backend timeout.
        EXIT WHEN clock_timestamp()-v_start>interval '5 minutes';
    END LOOP;
    DELETE FROM rvbbit.floor_presentations WHERE updated_at<now()-interval '30 days';
    COMMIT;
END $warm$;
COMMENT ON PROCEDURE rvbbit.floor_presentations_warm(integer) IS
    'Warm current Floor labels every 15 minutes, independent of work-graph snapshots. Commit each item and skip unchanged inputs.';
