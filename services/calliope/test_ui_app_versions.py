import json
from pathlib import Path
import ui_apps
from ui_app_preview import digest


def test_release_identity_matches_preview_and_includes_unselected_fragments():
    doc = json.loads((Path(__file__).parent / 'examples/ui-apps/hvac-visit.app.json').read_text())
    doc.setdefault('fragments', {})['future'] = {'components': [{'id':'extra','component':'FutureMap'}]}
    release = ui_apps.release_metadata(doc)
    assert release['definition_sha256'] == digest(doc)
    assert 'FutureMap' in release['components']
    catalog = ui_apps.catalog_definition()
    # Nothing here is newer than the baseline, so older clients still qualify.
    assert release['catalog_version'] == catalog['baseline'] < catalog['version']
    doc['fragments']['future']['components'].append({'id': 'rows', 'component': 'CResultTable'})
    assert ui_apps.release_metadata(doc)['catalog_version'] == catalog['since']['CResultTable']


def test_historical_assets_survive_a_clean_build(tmp_path, monkeypatch):
    root = tmp_path / 'build'
    (root / 'assets').mkdir(parents=True)
    (root / 'assets/host-old.js').write_text('old host')
    monkeypatch.setattr(ui_apps, 'ROOT', root)
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_DIR', str(tmp_path / 'durable'))
    ui_apps.archive_assets()
    (root / 'assets/host-old.js').unlink()
    (root / 'assets/host-new.js').write_text('new host')
    ui_apps.archive_assets()
    assert ui_apps.asset_path('assets/host-old.js').read_text() == 'old host'
    assert ui_apps.asset_path('assets/host-new.js').read_text() == 'new host'
    assert ui_apps.asset_path('../../durable/ui-app-assets/assets/host-old.js') is None
    (root / 'assets/host-new.js').write_text('changed')
    import pytest
    with pytest.raises(ValueError, match='Immutable'): ui_apps.archive_assets()
