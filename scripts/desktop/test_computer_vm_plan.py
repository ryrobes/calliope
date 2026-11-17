"""Offline checks of the generated infrastructure plan; no GCP mutations."""
import copy
import importlib.util
from pathlib import Path
import shlex
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("computer_vm_plan", ROOT / "scripts/release/plan-computer-vm.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def inputs():
    return dict(
        appliance={"name": "appliance", "networkInterfaces": [{
            "networkIP": "10.0.0.7", "stackType": "IPV4_ONLY",
            "network": "https://www.googleapis.com/compute/v1/projects/example/global/networks/default",
            "subnetwork": "https://www.googleapis.com/compute/v1/projects/example/regions/us-east1/subnetworks/default",
        }]},
        image={"status": "READY", "selfLink": "https://www.googleapis.com/compute/v1/projects/debian-cloud/global/images/debian-12-concrete"},
        project="example", zone="us-east1-b", name="appliance-computer",
        account="operator@example.com", bootstrap=ROOT / "scripts/desktop/bootstrap-computer-vm.sh",
    )


def commands(script):
    return [shlex.split(line) for line in script.splitlines() if line.startswith("gcloud ")]


def test_private_desktop_and_iap_ssh_override_default_internal_allow():
    rows = commands(module.plan(**inputs()))
    rules = rows[:4]
    assert len(rows) == 6
    assert all("--target-tags=appliance-computer" in row for row in rules)
    assert "--source-ranges=10.0.0.7/32" in rules[0]
    assert "--rules=tcp:9000,tcp:6901" in rules[0]
    assert "--priority=900" in rules[0]
    assert "--source-ranges=35.235.240.0/20" in rules[2]
    assert "--rules=tcp:22" in rules[2]
    for row in (rules[1], rules[3]):
        assert "--source-ranges=0.0.0.0/0" in row
        assert "--action=DENY" in row
        assert "--priority=901" in row
    assert "--rules=tcp:9000,tcp:6901" in rules[1]
    assert "--rules=tcp:22" in rules[3]
    create = rows[4]
    assert "--no-service-account" in create and "--no-scopes" in create
    assert "--no-boot-disk-auto-delete" in create
    assert "--image=" + inputs()["image"]["selfLink"] in create
    assert not any("image-family" in value for value in create)
    assert "get-effective-firewalls" in rows[-1]


@pytest.mark.parametrize("change", ["dual_stack", "two_nics", "deprecated", "unready", "bad_name"])
def test_unsupported_or_ambiguous_topology_fails_before_emitting_plan(change):
    args = inputs()
    if change == "dual_stack":
        args["appliance"]["networkInterfaces"][0]["stackType"] = "IPV4_IPV6"
    elif change == "two_nics":
        args["appliance"]["networkInterfaces"].append(copy.deepcopy(args["appliance"]["networkInterfaces"][0]))
    elif change == "deprecated":
        args["image"]["deprecated"] = {"state": "DEPRECATED"}
    elif change == "unready":
        args["image"]["status"] = "PENDING"
    else:
        args["name"] = "computer;touch /tmp/unwanted"
    with pytest.raises(ValueError):
        module.plan(**args)


def test_paths_with_spaces_are_shell_quoted_and_scripts_parse(tmp_path):
    args = inputs()
    bootstrap = tmp_path / "source with spaces" / "bootstrap.sh"
    bootstrap.parent.mkdir()
    bootstrap.write_text("#!/bin/bash\n")
    args["bootstrap"] = bootstrap
    script = module.plan(**args)
    assert "--metadata-from-file=startup-script=" + str(bootstrap) in commands(script)[4]
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    subprocess.run(["bash", "-n", str(inputs()["bootstrap"])], check=True)
