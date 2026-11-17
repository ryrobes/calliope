-- 0478: Floor titles and status lines say what is true now, in business words.
--
-- 0422's prompt asked the status line to "prefer receipt numbers", so Done
-- cards carried raw UUIDs ("ANALYZE is frozen for approval under receipt
-- 013ee465-…"), and it kept a previous title verbatim unless the subject
-- changed, so a title naming a momentary state ("Frozen ANALYZE Change for …")
-- outlived the approval it described. Briefs now carry each action's current
-- outcome (calliope.py); this prompt names the subject rather than its state,
-- revises a previous title the brief contradicts, and never prints identifiers.
-- Changing the prompt changes every packet hash, so cached presentations
-- regenerate lazily (bounded per read, as before).

CREATE OR REPLACE FUNCTION rvbbit.floor_presentation_prompt() RETURNS text
LANGUAGE sql IMMUTABLE AS $prompt$ SELECT 'title string: a specific natural 3-to-8-word noun phrase naming the enduring subject of the WHOLE thread — the project, artifact, dataset, decision, or outcome the person set out to get — taken from the opening ask and the brief, never from the latest message alone. Name the subject, not its momentary state: no words such as frozen, pending, awaiting, blocked, approved, or done. If a previous title is given, return it verbatim unless the thread''s subject has clearly changed or the previous title states something the brief shows is no longer true. Never a sentence, activity report, or generic Calliope work label. status string: one or two present-tense sentences, at most 30 words, saying what the human needs to do for awaiting_you, the current step and what it serves for in_motion, or the shipped outcome for done, as the brief describes it now. Prefer concrete actions, numbers, and outcomes over vague prose. Never include receipt numbers, UUIDs, or other record identifiers, and describe database operations in business terms (refreshed the table''s statistics, not ANALYZE). Do not repeat the title. No markdown.'::text $prompt$;
