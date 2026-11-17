"""Managed profile upgrades must fix context policy without changing user choices."""
import runpy
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / 'docker/calliope/reconcile-hermes-specialists.py'


def test_upgrade_old_pruning_preserves_selected_model_and_media(tmp_path):
    m = runpy.run_path(str(SCRIPT))
    markers = tmp_path / '.managed'
    markers.mkdir()
    (markers / 'model.operator-managed').touch()
    old = m['_profile_config']('dashboard-studio')
    old['compression'].update(proactive_prune_tokens=9000, protect_last_n=6)
    old['model']['default'] = 'calliope-opus'
    old['image_gen'] = {'provider': 'off'}
    old['skills']['disabled'] = ['example']
    config = tmp_path / 'config.yaml'
    config.write_text(yaml.safe_dump(old))
    reconcile = m['_reconcile_config']
    assert reconcile(tmp_path, m['_profile_config']('dashboard-studio'), markers) == 'updated'
    upgraded = yaml.safe_load(config.read_text())
    assert upgraded['compression']['proactive_prune_tokens'] == 120000
    assert upgraded['model'] == old['model']
    assert upgraded['image_gen'] == old['image_gen']
    assert upgraded['skills']['disabled'] == ['example']
    assert reconcile(tmp_path, m['_profile_config']('dashboard-studio'), markers) == 'current'


def test_explicit_user_managed_context_remains_untouched(tmp_path):
    m = runpy.run_path(str(SCRIPT))
    markers = tmp_path / '.managed'
    markers.mkdir()
    (markers / 'config.user-managed').touch()
    config = tmp_path / 'config.yaml'
    original = 'compression:\n  proactive_prune_tokens: 45000\n'
    config.write_text(original)
    assert m['_reconcile_config'](tmp_path, m['_profile_config']('dashboard-studio'), markers) == 'preserved-user-managed'
    assert config.read_text() == original


def test_specialist_skill_upgrade_preserves_operator_edits(tmp_path):
    m = runpy.run_path(str(SCRIPT))
    shared = SCRIPT.parent / 'hermes-skills/calliope-live-dashboards'
    scoped = SCRIPT.parent / 'hermes-specialist-skills/calliope-live-dashboards'
    target = tmp_path / 'live-dashboards'
    marker = tmp_path / 'managed.sha256'
    compose = m['_managed_compose']
    compose(shared, target, marker, '')
    compose(scoped, target, marker, '')
    assert (target / 'SKILL.md').read_text() == (scoped / 'SKILL.md').read_text()
    assert not (target / 'references/exploratory-blueprint.md').exists()
    (target / 'SKILL.md').write_text('operator instructions')
    compose(shared, target, marker, '')
    assert (target / 'SKILL.md').read_text() == 'operator instructions'
    assert 'mcp__Specialist__read_calliope_playbook' not in m['SPECIALIST_ROLE_PINNED_TOOLS']['dashboard-studio']


def test_new_profiles_use_openrouter_but_existing_unmarked_models_survive(tmp_path):
    m = runpy.run_path(str(SCRIPT))
    desired = m['_profile_config']('dashboard-studio')
    assert desired['model']['provider'] == 'openrouter'
    assert desired['model']['default'] == 'openai/gpt-6.1-sol'
    assert 'base_url' not in desired['model']
    markers = tmp_path / '.managed'
    markers.mkdir()
    old = m['_profile_config']('dashboard-studio')
    old['model'] = {**old['model'], 'provider': 'custom', 'default': 'calliope-opus',
                    **m['POLICY'].CLOVER_MODEL_BINDINGS}
    config = tmp_path / 'config.yaml'
    config.write_text(yaml.safe_dump(old))
    m['_reconcile_config'](tmp_path, desired, markers)
    current = yaml.safe_load(config.read_text())
    assert current['model'] == old['model']
    assert not (markers / 'model.operator-managed').exists()
