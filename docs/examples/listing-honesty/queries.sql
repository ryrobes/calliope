-- Listing honesty demo. Run in DataRabbit's SQL Scratch; photos show as
-- thumbnails and a photo__box column outlines its photo.

-- 1. One semantic predicate over a picture and a sentence together.
SELECT title, photo, description,
       rvbbit.truth(jsonb_build_array('Seller''s description: ' || description, rvbbit.image_part(photo)),
                    'Is everything the seller claims in the description consistent with what the listing photo shows?') AS honest__heat
FROM marketplace.listings
ORDER BY id;

-- 2. Classify from the picture alone, and point at the problem (Jev 1.14 box).
SELECT title, photo,
       rvbbit.choose(jsonb_build_array(rvbbit.image_part(photo)),
                     ARRAY['like new', 'good', 'worn', 'damaged']) AS condition,
       rvbbit.locate(jsonb_build_array(rvbbit.image_part(photo)),
                     'the most visible damage, wear or problem') AS photo__box
FROM marketplace.listings
ORDER BY id;

-- 3. The chained operator (listing_verdict.sql): Jev looks, an LLM writes the line.
--    One scalar call per listing, then pick keys from its JSON result. The
--    operator is volatile, so Postgres runs it once per row here rather than
--    once per key. Open Semantic → Operators → listing_verdict to see its
--    steps and each run.
WITH checked AS (
  SELECT *, rvbbit.listing_verdict(description, photo) AS v
  FROM marketplace.listings
)
SELECT title, photo, description,
       v ->> 'verdict'           AS verdict,
       (v ->> 'honest')::boolean AS honest__heat,
       v ->> 'condition'         AS condition,
       v -> 'box'                AS photo__box
FROM checked
ORDER BY id;
