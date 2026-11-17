-- Run through Calliope's SQL editor after publishing the classifier example.
-- This source table is already loaded in the local test instance; substitute
-- another relation with an observed text column in another workspace.
-- The first query needs no saved rule. Its choice criteria match the graph.
WITH sightings AS MATERIALIZED (
-- Real reports already present in this workspace.
-- Bound the preview without truncating a narrative; only observed reaches Jev.
SELECT bfroid::text AS request_id, title AS case_name,
       state, county, observed
FROM public.bigfoot_sightings_locations
WHERE length(btrim(observed)) BETWEEN 1 AND 16000
ORDER BY bfroid
LIMIT 100
)
SELECT request_id, case_name,
       rvbbit.choose(jsonb_build_object('observed', observed),
         '{
  "visual": "The account explicitly describes seeing a creature or figure. Seeing tracks alone, hearing sounds, and explicitly NOT seeing a creature do not qualify. Prefer visual if sounds or tracks are also reported.",
  "physical": "Tracks, hair or another tangible sign are described, but no creature or figure was seen. Prefer physical over sounds when both are present.",
  "sounds": "Calls, howls, knocks, footsteps or other noises are described, without a visual encounter or tangible signs.",
  "unclear": "No specific visual encounter, physical sign or sound is described; only speculation, a vague feeling, or insufficient detail."
}'::jsonb) AS encounter_type
FROM sightings;

-- The named call follows the current published version; add a third argument
-- to pin a revision. The versioned graph adds the road-crossing decision on the visual route.
-- MATERIALIZED avoids repeating inference when reading several result fields.
WITH sightings AS MATERIALIZED (
-- Real reports already present in this workspace.
-- Bound the preview without truncating a narrative; only observed reaches Jev.
SELECT bfroid::text AS request_id, title AS case_name,
       state, county, observed
FROM public.bigfoot_sightings_locations
WHERE length(btrim(observed)) BETWEEN 1 AND 16000
ORDER BY bfroid
LIMIT 100
), evaluated AS MATERIALIZED (
  SELECT request_id, case_name,
         rvbbit.business_logic(
           'examples.bigfoot_report_classifier', to_jsonb(sightings)
         ) AS decision
  FROM sightings
)
SELECT request_id, case_name,
       decision->>'status' AS status,
       decision->>'label' AS outcome,
       decision->'path' AS path
FROM evaluated;
