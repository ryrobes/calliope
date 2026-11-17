#!/usr/bin/env python3
"""Read GCP topology and print a provisioning script; never mutate cloud resources.

Run the generated script from this checkout after inspecting the effective
firewall policy. No guest credentials are put in startup metadata.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess


def plan(*, appliance: dict, image: dict, project: str, zone: str,
         name: str, account: str, bootstrap: Path) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}[a-z0-9]", name):
        raise ValueError("name must be 2-41 lowercase letters/digits/hyphens")
    nics = appliance.get("networkInterfaces", [])
    if len(nics) != 1 or nics[0].get("stackType", "IPV4_ONLY") != "IPV4_ONLY":
        raise ValueError("expected one IPv4-only appliance interface; inspect topology manually")
    nic = nics[0]
    address = ipaddress.IPv4Address(nic["networkIP"])
    network, subnet = nic["network"], nic["subnetwork"]
    if image.get("status") != "READY" or (image.get("deprecated") or {}).get("state"):
        raise ValueError("resolved Debian image must be READY and not deprecated")
    if not bootstrap.is_file():
        raise ValueError(f"missing bootstrap: {bootstrap}")
    base = ["gcloud", "--project=" + project, "--account=" + account, "--quiet"]
    commands: list[list[str]] = []
    # Explicit denies also fence the usual broad default-allow-internal rule.
    # Higher precedence and hierarchical rules still require effective-policy review.
    for suffix, action, ports, source, priority in (
        ("desktop-allow", "ALLOW", "tcp:9000,tcp:6901", f"{address}/32", "900"),
        ("desktop-deny", "DENY", "tcp:9000,tcp:6901", "0.0.0.0/0", "901"),
        ("ssh-allow", "ALLOW", "tcp:22", "35.235.240.0/20", "900"),
        ("ssh-deny", "DENY", "tcp:22", "0.0.0.0/0", "901"),
    ):
        commands.append(base + ["compute", "firewall-rules", "create", f"{name}-{suffix}",
            "--network=" + network, "--direction=INGRESS", "--action=" + action,
            "--rules=" + ports, "--source-ranges=" + source, "--priority=" + priority,
            "--target-tags=" + name, "--description=Calliope dedicated Computer " + name])
    commands.append(base + ["compute", "instances", "create", name, "--zone=" + zone,
        "--machine-type=e2-standard-4", "--boot-disk-size=100GB", "--boot-disk-type=pd-balanced",
        "--no-boot-disk-auto-delete",
        "--image=" + image["selfLink"], "--network=" + network, "--subnet=" + subnet,
        "--stack-type=IPV4_ONLY", "--no-service-account", "--no-scopes", "--tags=" + name,
        "--metadata-from-file=startup-script=" + str(bootstrap.resolve())])
    commands.append(base + ["compute", "instances", "network-interfaces", "get-effective-firewalls",
        name, "--zone=" + zone, "--network-interface=nic0", "--format=json"])
    preflight = base + ["compute", "instances", "network-interfaces", "get-effective-firewalls",
        appliance["name"], "--zone=" + zone, "--network-interface=nic0", "--format=json"]
    header = ["#!/usr/bin/env bash", "set -euo pipefail",
        "# Generated from read-only GCP discovery. Review before running.",
        "# Creates billable CPU VM/disk with ephemeral external IPv4 for outbound access.",
        "# Dedicated target tag; desktop ports admit only the appliance private IPv4.",
        "# SSH admits IAP only; invoking identity needs IAP/SSH permissions.",
        "# Existing names fail closed. After partial failure inspect resources; do not blindly rerun.",
        "# Hierarchical/higher-priority firewall rules may override these VPC rules.",
        "# Inspect policy before applying, then verify the new VM's effective policy below.",
        "# Appliance policy inspection: " + shlex.join(preflight),
        "# No desktop is started; source/profile migration and endpoint cutover are separate.", ""]
    return "\n".join(header + [shlex.join(command) for command in commands]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--zone", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--appliance", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    base = ["gcloud", "--project=" + args.project, "--account=" + args.account, "--quiet"]

    def read(arguments: list[str], *, project: str | None = None) -> dict:
        command_base = base if project is None else [
            "gcloud", "--project=" + project, "--account=" + args.account, "--quiet"]
        result = subprocess.run(command_base + arguments + ["--format=json"],
                                check=True, text=True, stdout=subprocess.PIPE)
        return json.loads(result.stdout)

    appliance = read(["compute", "instances", "describe", args.appliance, "--zone=" + args.zone])
    image = read(["compute", "images", "describe-from-family", "debian-12"], project="debian-cloud")
    print(plan(appliance=appliance, image=image, project=args.project, zone=args.zone,
        name=args.name, account=args.account,
        bootstrap=Path(__file__).resolve().parents[1] / "desktop/bootstrap-computer-vm.sh"), end="")


if __name__ == "__main__":
    main()
