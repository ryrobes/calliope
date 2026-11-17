-- 0426: Nemesis is reassigned — Design Red Team.
--
-- The shipping roster had no seat for design critique: Cassandra's charter
-- nominally covered "visual communication" beside 60 analytical reviews a
-- month, and the disabled Nemesis seat (marketing-attribution-assurance, two
-- runs, both failed, overlapped by Argus and Athena) sat empty. Same persona,
-- new desk: adversarial review of how a published dashboard or live app
-- looks and reads, returning ranked findings with exact edits. Review only.
-- The Hermes profile (docker/calliope/hermes-specialists/design-red-team)
-- appears on the next Hermes restart; this row puts her on the roster and in
-- Calliope's delegation preferences immediately.

INSERT INTO rvbbit.calliope_specialists
    (id, specialist_key, profile_name, display_name, description, capabilities, default_schedule, managed, status)
VALUES (
    '5d1e7c3a-9b2f-5a48-8f6e-2c0d41b9e7a1'::uuid,
    'design-red-team', 'design-red-team', 'Nemesis',
    'Design Red Team — adversarially reviews how a published dashboard or live app looks and reads, and returns ranked findings with exact edits.',
    '["design_review","visual_hierarchy","typography","color_and_contrast","spacing_rhythm","responsiveness","css_edit_proposals","visual_critique"]'::jsonb,
    NULL, true, 'active'
)
ON CONFLICT (specialist_key) DO UPDATE SET
    profile_name=EXCLUDED.profile_name,
    display_name=EXCLUDED.display_name,
    description=EXCLUDED.description,
    capabilities=EXCLUDED.capabilities,
    managed=EXCLUDED.managed,
    updated_at=now();

UPDATE rvbbit.calliope_specialists
   SET routing_hint='Prefer Nemesis for how a published dashboard or live app looks and reads: hierarchy, type, colour, spacing, responsiveness, the generic-template smell. She returns ranked findings with exact edits and never publishes.',
       updated_at=now()
 WHERE specialist_key='design-red-team';

-- The old attribution seat keeps its history but stops sharing the name.
UPDATE rvbbit.calliope_specialists
   SET display_name='Nemesis (former attribution seat)', updated_at=now()
 WHERE specialist_key='marketing-attribution-assurance'
   AND display_name='Nemesis';

-- Cassandra keeps chart honesty (does the picture imply more than the data
-- supports); look and feel is Nemesis's desk now.
UPDATE rvbbit.calliope_specialists
   SET description='Analytical Skeptic — adversarially reviews analytical claims, governed definitions, evidence, and whether a chart implies more than the data supports.',
       capabilities=(capabilities - 'visual_critique') || '["chart_honesty"]'::jsonb,
       updated_at=now()
 WHERE specialist_key='analytical-skeptic' AND managed;
