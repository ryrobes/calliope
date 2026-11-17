"""Unattended appliance defaults and operator-policy preservation across upgrades."""

import copy
from pathlib import Path
import runpy

import pytest
import yaml

ROOT = Path(__file__).parents[2] / "docker/calliope"


@pytest.mark.parametrize("mode", [None, "manual", "smart", "off", False])
def test_default_root_mode_preserves_explicit_policy_and_other_settings(mode):
    policy = runpy.run_path(str(ROOT / "reconcile-hermes-config.py"))
    config = {
        "model": {"default": "chosen", "provider": "custom"},
        "approvals": {"timeout": 17, "deny": ["operator-rule"]},
    }
    if mode is not None:
        config["approvals"]["mode"] = mode
    original = copy.deepcopy(config)
    changed = policy["apply_managed_approval_defaults"](config)
    assert changed == (mode is None)
    assert config == {
        **original,
        "approvals": {
            **original["approvals"],
            "mode": "off" if mode is None else mode,
        },
    }
    assert not policy["apply_managed_approval_defaults"](config)


def test_root_seed_and_upgrade_default_agree():
    policy = runpy.run_path(str(ROOT / "reconcile-hermes-config.py"))
    template = yaml.safe_load((ROOT / "hermes-config.yaml").read_text())
    assert template["approvals"]["mode"] == "off"
    template.pop("approvals")
    assert policy["reconcile_payload"](template)
    assert template["approvals"]["mode"] == "off"


@pytest.mark.parametrize("mode", [None, "manual", "smart", "off", False])
def test_specialist_upgrade_default_preserves_explicit_approval_choices(tmp_path, mode):
    module = runpy.run_path(str(ROOT / "reconcile-hermes-specialists.py"))
    desired = module["_profile_config"]("analytical-skeptic")
    assert desired["approvals"]["mode"] == "off"
    profile = tmp_path / "profile"
    profile.mkdir()
    markers = profile / ".calliope-managed"
    markers.mkdir()
    current = {"approvals": {"timeout": 17, "deny": ["operator-rule"]}}
    if mode is not None:
        current["approvals"]["mode"] = mode
    (profile / "config.yaml").write_text(yaml.safe_dump(current))
    module["_reconcile_config"](profile, desired, markers)
    actual = yaml.safe_load((profile / "config.yaml").read_text())
    assert actual["approvals"] == {
        "mode": "off" if mode is None else mode,
        "timeout": 17,
        "deny": ["operator-rule"],
    }


def test_user_managed_profile_is_not_rewritten(tmp_path):
    module = runpy.run_path(str(ROOT / "reconcile-hermes-specialists.py"))
    profile = tmp_path / "profile"
    profile.mkdir()
    markers = profile / ".calliope-managed"
    markers.mkdir()
    original = "model:\n  default: chosen\napprovals:\n  mode: manual\n"
    (profile / "config.yaml").write_text(original)
    (markers / "config.user-managed").touch()
    result = module["_reconcile_config"](
        profile, module["_profile_config"]("analytical-skeptic"), markers
    )
    assert result == "preserved-user-managed"
    assert (profile / "config.yaml").read_text() == original
