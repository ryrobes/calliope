import io
from unittest.mock import patch
from PIL import Image
import avatar_colors
import hermes_profiles


def png(color):
    output = io.BytesIO()
    Image.new('RGB', (20, 20), color).save(output, format='PNG')
    return output.getvalue()


def test_cache_tracks_image_contents_and_preserves_identity():
    original = {'name': 'Example', 'custom': {'keep': True}, 'avatar_file': 'example.png'}
    first = avatar_colors.enrich(original, lambda _: png('#FF0088'))
    assert first['avatar_color'] == '#FF0088'
    assert first['custom'] == original['custom']
    with patch.object(avatar_colors, 'extract', side_effect=AssertionError('cache miss')):
        assert avatar_colors.enrich(first, lambda _: png('#FF0088')) == first
    second = avatar_colors.enrich(first, lambda _: png('#00AAFF'))
    assert second['avatar_color'] == '#00AAFF'
    assert first['avatar_color_cache'] != second['avatar_color_cache']


def test_variants_and_unsafe_paths():
    result = avatar_colors.enrich({'avatar_day_file': 'day.png', 'avatar_night_file': 'night.png'},
                                 lambda f: png('#FF0000' if f == 'day.png' else '#0000FF'))
    assert result['avatar_color'] == result['avatar_day_color'] == '#FF0000'
    assert result['avatar_night_color'] == '#0000FF'
    assert avatar_colors.enrich({'avatar_file': '../bad.png'}, lambda _: 1/0) == {'avatar_file': '../bad.png'}
    assert avatar_colors.fields({'avatar_color': 'red;display:none', 'avatar_day_color': '#abcdef'}) == {'avatar_day_color': '#ABCDEF'}


def test_profile_metadata_cache_invalidates_on_profile_revision(monkeypatch):
    hermes_profiles._portrait_fields.cache_clear()
    state = {'hash': 'one', 'color': '#aa1234', 'reads': 0}
    def control(method, path, **kwargs):
        if path == '/v1/profiles':
            return {'profiles': [{'key': 'example', 'profile_hash': state['hash']}]}
        state['reads'] += 1
        return {'content': 'avatar_color: "' + state['color'] + '"'}
    monkeypatch.setattr(hermes_profiles, '_control', control)
    assert hermes_profiles.profiles()['profiles'][0]['avatar_color'] == '#AA1234'
    hermes_profiles.profiles()
    assert state['reads'] == 1
    state.update(hash='two', color='#BB5678')
    assert hermes_profiles.profiles()['profiles'][0]['avatar_color'] == '#BB5678'
    assert state['reads'] == 2
    hermes_profiles._portrait_fields.cache_clear()
