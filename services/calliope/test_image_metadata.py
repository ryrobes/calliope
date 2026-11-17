import json
from types import SimpleNamespace
from uuid import uuid4

import image_metadata as im
import stage_context


def test_generation_has_useful_defaults_and_separates_intent_from_observation():
    meta = im.generated('  A lifecycle\n dashboard mock ' + 'x' * 2000, model='test')
    assert meta['title'].startswith('Image · A lifecycle dashboard mock')
    assert len(meta['generation_brief']) == 700
    assert meta['brief_kind'] == 'requested_content_not_verified_caption'
    assert im.generated('prompt', title='Mock v2', purpose='dashboard mockup')['title'] == 'Mock v2'


def test_visual_intent_and_capture_relationships_survive_bounding():
    row = {'kind': 'image', 'title': 'Visual intent', 'parent_surface_id': 'parent',
           'payload': {'mime_type': 'image/png'}, 'source': {
               'origin': 'user_visual_intent', 'source_surface_id': 'source',
               'artifact_slug': 'lead-lifecycle', 'artifact_version': 3,
               'instructions': ['old', 'make the handoff amber', 'larger labels']}}
    meta = im.describe(row)
    assert meta['source_surface_id'] == 'source' and meta['parent_surface_id'] == 'parent'
    assert meta['artifact'] == 'lead-lifecycle@v3'
    assert meta['edit_instructions'] == 'make the handoff amber | larger labels'
    assert meta['purpose'] == 'visual_intent'
    row.update(tool_name='capture_live_app', source={})
    assert im.describe(row)['purpose'] == 'page_capture'


def test_saved_png_keeps_creator_and_filename_but_not_paths_or_other_payload():
    row = {'kind': 'document', 'title': 'Mock v2',
           'payload': {'mime_type': 'image/png', 'original_name': '/private/path/mock-v2.png',
                       'source_path': '/private/path/mock-v2.png', 'rows': ['secret']},
           'source': {'specialist_key': 'iris', 'specialist_thread_id': 'unneeded'}}
    meta = im.describe(row)
    assert meta['creator'] == 'iris' and meta['filename'] == 'mock-v2.png'
    encoded = json.dumps(stage_context.descriptor({'surface_id': '1', 'image': meta}))
    assert '/private' not in encoded and 'secret' not in encoded
    assert im.describe({'kind': 'document', 'payload': {'mime_type': 'text/plain'}}) == {}


def test_exact_copy_inherits_brief_with_separate_identity():
    original = {'id': 'original', 'kind': 'image', 'payload': {
        'image_metadata': im.generated('lead lifecycle', title='Mock v2'), 'content_sha256': 'a'*64}}
    copy = {'kind': 'document', 'title': 'renamed', 'payload': {'mime_type': 'image/png', 'original_name': 'renamed.png'}}
    meta = im.describe(im.inherit_copy(copy, original))
    assert meta['generation_brief'] == 'lead lifecycle'
    assert meta['same_image_as'] == 'original' and meta['filename'] == 'renamed.png'
    assert 'same_image_as' not in original['payload']['image_metadata']


def test_legacy_prompt_fetch_is_batched_and_only_uses_referenced_image_ids():
    image_id = str(uuid4())
    row = {'kind': 'image', 'title': 'Generated image', 'payload': {'image_id': image_id}}
    calls = []
    class Conn:
        def execute(self, sql, args=None):
            calls.append((sql, args))
            return SimpleNamespace(fetchone=lambda: {'r': 'rvbbit.generated_images'},
                                   fetchall=lambda: [{'id': image_id, 'prompt': 'a lead lifecycle', 'model': 'test'}])
    result = im.enrich_rows(Conn(), 'session', [row])
    assert len(calls) == 2 and calls[1][1] == ([image_id],)
    assert 'png' not in calls[1][0]
    assert im.describe(result[0])['generation_brief'] == 'a lead lifecycle'
    assert 'image_metadata' not in row['payload']


def test_copy_lookup_is_exact_hash_scoped_to_session_and_bad_ids_are_ignored():
    calls = []
    class Conn:
        def execute(self, sql, args=None):
            calls.append((sql, args))
            return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: None)
    row = {'kind': 'document', 'payload': {'mime_type': 'image/png', 'content_sha256': 'a'*64,
                                         'image_id': 'not a UUID'}}
    im.enrich_rows(Conn(), 'this-session', [row])
    assert len(calls) == 1
    assert 'WHERE session_id=%s::uuid' in calls[0][0]
    assert calls[0][1] == ('this-session', ['a'*64])
    row['payload']['content_sha256'] = 'invalid'
    calls.clear()
    im.enrich_rows(Conn(), 'this-session', [row])
    assert not calls
