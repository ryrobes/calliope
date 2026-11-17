-- clover_sentiment returns its label ('positive' | 'negative' | 'neutral'), like
-- clover_classify and clover_language; clover_sentiment_score returns the number.
-- It used to return the sentiment route's whole JSON as text, which on the
-- self-hosted text-judgment contract also carried model, contract and usage.
-- Only the shipped single-step definition is rewritten; customized operators are
-- left alone. The new steps change the operator's cache identity, so earlier
-- JSON results are not served for the label.
UPDATE rvbbit.operators
   SET steps = '[{"name":"s","kind":"specialist","specialist":"sentiment","inputs":{"text":"{{t}}"}},{"name":"g","kind":"code","fn":"json_get","inputs":{"path":"label","value":"{{steps.s.output}}"}}]'::jsonb,
       description = 'Clover: sentiment label (positive, negative or neutral) for a text. clover_sentiment_score returns the number.'
 WHERE name = 'clover_sentiment'
   AND steps = '[{"name":"s","kind":"specialist","specialist":"sentiment","inputs":{"text":"{{t}}"}}]'::jsonb;

UPDATE rvbbit.operators
   SET tests = '[{"name": "label+", "sql": "SELECT rvbbit.clover_sentiment(''i love this database'')", "expect": {"type": "exact", "value": "positive"}, "description": ""}, {"name": "label-", "sql": "SELECT rvbbit.clover_sentiment(''terrible experience'')", "expect": {"type": "exact", "value": "negative"}, "description": ""}]'::jsonb
 WHERE name = 'clover_sentiment'
   AND tests = '[{"name": "label+", "sql": "SELECT rvbbit.clover_sentiment(''i love this database'')", "expect": {"type": "contains", "value": "positive"}, "description": ""}, {"name": "label-", "sql": "SELECT rvbbit.clover_sentiment(''terrible experience'')", "expect": {"type": "contains", "value": "negative"}, "description": ""}]'::jsonb;
