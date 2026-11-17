-- listing_verdict(description, photo): does the photo back up the seller?
--   look    — Jev 1.14 sees the photo and the claim: honest? condition? flaw? where?
--   verdict — an LLM turns those facts into one dry buyer's line
--   result  — SQL assembles the answer (the box only when there is a flaw to show)
SELECT rvbbit.create_operator(
    'listing_verdict', ARRAY['description', 'photo'], 'jsonb',
    op_arg_types := ARRAY['text', 'text'],
    op_model := 'typesafe/jev-1.14',
    op_parser := 'json',
    op_description := 'Marketplace honesty check: Jev looks at the listing photo against the seller''s description (honest, condition, flaw and where it is), then an LLM writes a one-line verdict. Returns {honest, honest_score, condition, flaw, box, verdict}.',
    op_steps := $steps$[
      {"name": "look", "kind": "specialist", "specialist": "decisions",
       "inputs": {
         "model": "typesafe/jev-1.14",
         "contract": "clover-decisions-v1",
         "state": ["Seller's description: {{inputs.description}}",
                   {"type": "image_url", "image_url": {"url": "{{inputs.photo}}"}}],
         "questions": {
           "honest": {"type": "noul",
                      "instructions": "Is everything the seller claims in the description consistent with what the listing photo shows?"},
           "condition": {"type": "choice", "instructions": "What condition is the item in, judging only by the photo?",
                         "criteria": {"like new": "looks unused, no visible wear",
                                      "good": "light, normal wear",
                                      "worn": "obvious wear or cosmetic damage",
                                      "damaged": "broken, cracked, torn, stained or missing parts"}},
           "flaw": {"type": "choice", "instructions": "What is the most visible problem in the photo?",
                    "criteria": {"none": "nothing wrong is visible",
                                 "scratches": "scratches, scuffs or claw marks",
                                 "cracks": "cracked or shattered parts",
                                 "stains": "stains, spills or discoloration",
                                 "dents": "dents or collision damage",
                                 "torn": "torn or ripped material",
                                 "missing or mismatched": "parts missing, too few pieces, or items that do not match",
                                 "pet": "an animal or pet hair where the seller implies none",
                                 "heavy wear": "general heavy wear, rust or fading"}},
           "where": {"type": "box", "instructions": "the most visible damage, wear or problem"}}}},
      {"name": "verdict", "kind": "llm", "model": "clover", "provider": "clover_llm", "max_tokens": 200,
       "user": "You are a deadpan, slightly savage second-hand marketplace buyer. Write ONE sentence of at most 20 words reacting to this listing, using only these facts. No emojis, hashtags or quotation marks.\n\nSeller's description: {{inputs.description}}\nChance the photo backs up the description: {{steps.look.output.answers.honest.noul}}\nCondition in the photo: {{steps.look.output.answers.condition.choice}}\nMost visible problem: {{steps.look.output.answers.flaw.choice}}\n\nIf the chance is below 0.5, name the specific contradiction dryly. Otherwise give a short, grudgingly approving line."},
      {"name": "result", "kind": "sql",
       "sql": "SELECT $1::float8 > 0.5 AS honest, round($1::numeric, 2) AS honest_score, $2::text AS condition, $3::text AS flaw, CASE WHEN $1::float8 <= 0.5 OR $2::text IN ('worn', 'damaged') THEN $4::jsonb END AS box, btrim($5::text) AS verdict",
       "params": ["{{steps.look.output.answers.honest.noul}}",
                  "{{steps.look.output.answers.condition.choice}}",
                  "{{steps.look.output.answers.flaw.choice}}",
                  "{{steps.look.output.answers.where.box}}",
                  "{{steps.verdict.output}}"]}
    ]$steps$::jsonb
);
