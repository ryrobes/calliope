-- 0406: give clover_llm the same per-call ceiling as the other long-running
-- clover backends. The artifact semantic enricher's single calls carry up to
-- 180k chars of rendered-artifact evidence plus a screenshot and may generate
-- up to 32k tokens — routinely past the 120 s wall, where reqwest 0.12 kills
-- the call and reports only "error sending request for url (…)", which reads
-- as a network failure. tabular_predict/tabular_explain already run at 600 s.
-- A genuinely unreachable server still fails fast at connect; this only lets
-- slow calls be slow. The 0208 statement trigger bumps rvbbit.backends_gen,
-- so warm connections drop their cached spec immediately.
UPDATE rvbbit.backends
   SET timeout_ms = 600000
 WHERE name = 'clover_llm'
   AND coalesce(timeout_ms, 0) < 600000;
