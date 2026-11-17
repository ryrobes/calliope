-- Images in Jev decisions. An image rides in state as a chat-style part built by
-- image_part(); truth, choose and clover_decide (jev-1.13) accept it unchanged.
-- Box questions (where is the target?) need jev-1.14, so clover_look pins that
-- model; its own cache identity keeps 1.13 judgments untouched.
SELECT rvbbit.create_operator(
    'clover_look', ARRAY['state', 'questions'], 'jsonb',
    op_arg_types := ARRAY['jsonb', 'jsonb'],
    op_model := 'typesafe/jev-1.14',
    op_parser := 'json',
    op_description := 'Clover Jev 1.14 decision: state may include images (rvbbit.image_part) plus named choice, noul and box questions. Box answers are edges as fractions of the image. Cached in semantic receipts; NULL on transport failure.',
    op_steps := '[{"name":"decision","kind":"specialist","specialist":"decisions","inputs":{"model":"typesafe/jev-1.14","contract":"clover-decisions-v1","state":"{{state}}","questions":"{{questions}}"}}]'::jsonb
);

-- A base64 PNG or JPEG data URL; size and dimension checks happen in Clover,
-- which reports them clearly (each side 256..1568 px, no remote URLs).
CREATE OR REPLACE FUNCTION rvbbit.image_part(image text)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $fn$
BEGIN
    IF left(image, 32) !~ '^data:image/(png|jpeg);base64,' THEN
        RAISE EXCEPTION 'image must be a base64 PNG or JPEG data URL (data:image/jpeg;base64,...)';
    END IF;
    RETURN jsonb_build_object('type', 'image_url', 'image_url', jsonb_build_object('url', image));
END
$fn$;

-- Raw image bytes; the type comes from the file signature.
CREATE OR REPLACE FUNCTION rvbbit.image_part(image bytea)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $fn$
DECLARE kind text;
BEGIN
    kind := CASE
        WHEN substring(image FROM 1 FOR 8) = '\x89504e470d0a1a0a'::bytea THEN 'image/png'
        WHEN substring(image FROM 1 FOR 2) = '\xffd8'::bytea THEN 'image/jpeg'
    END;
    IF kind IS NULL THEN
        RAISE EXCEPTION 'image bytes must be a PNG or JPEG file';
    END IF;
    RETURN rvbbit.image_part('data:' || kind || ';base64,' || translate(encode(image, 'base64'), E'\n', ''));
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.box_question(target text)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $fn$
BEGIN
    IF btrim(target) = '' OR length(target) > 2000 THEN
        RAISE EXCEPTION 'target must contain 1 to 2000 characters';
    END IF;
    RETURN jsonb_build_object('result', jsonb_build_object('type', 'box', 'instructions', btrim(target)));
END
$fn$;

-- {top,bottom,left,right,confidence} as fractions of the image. A box always
-- comes back, even when the target is absent: pair it with truth() to ask.
CREATE OR REPLACE FUNCTION rvbbit.locate(state jsonb, target text)
RETURNS jsonb LANGUAGE sql STRICT PARALLEL SAFE AS $fn$
    SELECT (a -> 'box') || jsonb_strip_nulls(jsonb_build_object('confidence', a -> 'confidence'))
    FROM (SELECT rvbbit.clover_look(state, rvbbit.box_question(target)) #> '{answers,result}' AS a) answer
    WHERE a ? 'box'
$fn$;

COMMENT ON FUNCTION rvbbit.image_part(text) IS
'Wrap a base64 PNG/JPEG data URL as a Jev image part. Put it in a state array next to text, e.g. jsonb_build_array(description, rvbbit.image_part(photo)), for truth, choose, clover_decide or locate.';
COMMENT ON FUNCTION rvbbit.locate(jsonb, text) IS
'Jev 1.14 box: where the target is in the image(s) in state, as {top,bottom,left,right,confidence} fractions. Always answers; ask truth() whether the target is present.';

INSERT INTO rvbbit.capability_seed(name,kind,signature,description,example,cost_class,tags)
VALUES
('image_part','cap_operator','rvbbit.image_part(image text | bytea) → jsonb','Wrap an image for Jev decisions: a base64 PNG/JPEG data URL, or raw PNG/JPEG bytes. Put it in a state array beside text; truth, choose, clover_decide and locate then judge the picture and the words together. Each side 256..1568 px; remote URLs are not fetched. No inference.','SELECT rvbbit.truth(jsonb_build_array(''Seller says: like new'', rvbbit.image_part(photo)), ''Does the photo support the description?'') FROM listings','free',ARRAY['semantic','jev','decision','image','vision','multimodal']),
('locate','cap_operator','rvbbit.locate(state jsonb, target text) → jsonb','Jev 1.14 box question: where the described target is in the image(s) in state, as {top,bottom,left,right,confidence} fractions of the image. It always answers, even when the target is absent, so pair it with truth(). Uses the clover_look cache.','SELECT rvbbit.locate(jsonb_build_array(rvbbit.image_part(photo)), ''the visible damage'') FROM listings','metered_llm',ARRAY['semantic','jev','decision','image','vision','box','cache']),
('box_question','cap_operator','rvbbit.box_question(target text) → jsonb','Build a named box question for clover_look or prewarm_operator. No inference.','SELECT rvbbit.clover_look(jsonb_build_array(rvbbit.image_part(photo)), rvbbit.box_question(''the product''))','free',ARRAY['semantic','jev','decision','image','box'])
ON CONFLICT(name) DO UPDATE SET kind=excluded.kind,signature=excluded.signature,
 description=excluded.description,example=excluded.example,cost_class=excluded.cost_class,tags=excluded.tags;
