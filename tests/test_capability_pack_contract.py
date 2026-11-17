"""Catalog pack metadata contract tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

yaml = pytest.importorskip("yaml")


PACKS = Path(__file__).resolve().parents[1] / "capabilities" / "packs"
ROOT = PACKS.parent.parent
CALLIOPE_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _pack_docs():
    for path in sorted(PACKS.rglob("rvbbit-pack.yaml")):
        with path.open(encoding="utf-8") as fh:
            yield path, yaml.safe_load(fh)


def _capability_doc(path: Path):
    with (path.parent / "capability.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_pack_exports_include_manifest_operators():
    bad: list[str] = []
    for path, pack in _pack_docs():
        capability_path = path.parent / "capability.yaml"
        if not capability_path.exists():
            continue
        manifest = _capability_doc(path)
        manifest_ops = {op["name"] for op in manifest.get("operators") or []}
        if not manifest_ops:
            continue
        exported_ops = set((pack.get("exports") or {}).get("operators") or [])
        missing = sorted(manifest_ops - exported_ops)
        extra = sorted(exported_ops - manifest_ops)
        if missing or extra:
            bad.append(
                f"{path.relative_to(PACKS.parent.parent)} "
                f"missing={missing} extra={extra}"
            )

    assert not bad, "pack exports.operators must mirror capability operators: " + "; ".join(bad)


def test_generated_compose_does_not_publish_default_host_port():
    pack = PACKS / "rerank" / "bge-reranker-base"
    proc = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "render",
            "--part",
            "compose",
            str(pack),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    assert "expose:" in proc.stdout
    assert "ports:" not in proc.stdout


def test_sql_test_pack_renders_without_backend_or_sidecar():
    pack = PACKS / "sql" / "core-workflows"
    register = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "render",
            "--part",
            "register",
            str(pack),
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    compose = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "render",
            "--part",
            "compose",
            str(pack),
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout

    assert "register_backend" not in register
    assert "register_python_runtime" not in register
    assert "No backend or runtime is registered" in register
    assert "services:" not in compose
    assert "No Docker sidecar" in compose


def test_generated_operator_sql_replaces_infix_bindings():
    pack = PACKS / "rerank" / "bge-reranker-base"
    proc = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "render",
            "--part",
            "operators",
            str(pack),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    assert "op.oprname = '~~%'" in proc.stdout
    assert "op.oprname = '~~?'" in proc.stdout
    assert "SET infix_symbol = '~~%'" in proc.stdout
    assert "SET infix_symbol = '~~?'" in proc.stdout


def test_llm_provider_pack_renders_chat_backend_and_provider_registration():
    pack = PACKS / "llm" / "gemma-4-12b-it-vllm"
    register = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "render",
            "--part",
            "register",
            str(pack),
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert "backend_transport        => 'openai_chat'" in register
    assert "rvbbit.register_self_hosted_model" in register
    assert "provider           => 'gemma_4_12b_it'" in register
    assert "model              => 'google/gemma-4-12B-it'" in register
    assert "rvbbit.set_default_provider" not in register

    compose = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "render",
            "--part",
            "compose",
            str(pack),
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert 'image: "vllm/vllm-openai:latest"' in compose
    assert "command:" in compose
    assert '      - "--model"' in compose
    assert '      - "google/gemma-4-12B-it"' in compose
    assert 'ipc: "host"' in compose
    assert "ports:" not in compose


def test_managed_clover_uses_a_stable_service_model_alias():
    capability = json.loads(
        (PACKS / "managed" / "clover" / "capability.json").read_text(encoding="utf-8")
    )
    install_sql = "\n".join(capability["managed"]["install"]["sql"])

    assert '"model": "clover"' in install_sql
    assert '"model_policy": "managed"' in install_sql
    assert '"model_aliases": ["gemma4"]' in install_sql
    assert '"model_allowlist": ["clover", "calliope"]' in install_sql
    assert '"model_prefix_allowlist": ["clover*", "calliope*"]' in install_sql
    assert "'provider','clover_llm','model','gemma4'" not in install_sql
    assert '"provider":"clover_llm","model":"gemma4"' not in install_sql
    assert "nvidia/Gemma-4-31B-IT-NVFP4" not in install_sql
    assert "register_backend('embed',     'http://clover:8090/v1/embeddings'" in install_sql
    assert "'openai', 32, 25, 30000, 'RVBBIT_CLOVER_KEY'" in install_sql
    assert "rvbb.it" not in install_sql


def test_artifact_semantic_enricher_pins_the_managed_calliope_route():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0224_artifact_semantic_enrichment.sql"
    ).read_text(encoding="utf-8")

    assert "op_model       => 'calliope'" in migration
    assert "'provider', 'clover_llm'" in migration
    assert "'model', 'calliope'" in migration


def test_google_meet_pack_renders_as_a_governed_brain_connector():
    pack = PACKS / "integrations" / "google-meet-brain"
    register = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "render",
            "--part",
            "register",
            str(pack),
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    compose = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "render",
            "--part",
            "compose",
            str(pack),
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout

    assert "rvbbit.register_backend" in register
    assert "rvbbit.brain_configure_source" in register
    assert "'gmeet_connector'" in register
    assert '"acl_mode":"calendar_invitees_strict"' in register
    assert '"summarize_meetings":true' in register
    assert "rvbbit.register_python_runtime" not in register
    assert "rvbbit.register_memory_service" not in register
    assert "rvbbit-gmeet-connector:latest" in compose
    assert 'GMEET_ACL_MODE: "${GMEET_ACL_MODE:-calendar_invitees_strict}"' in compose

    seed = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "catalog",
            "seed-json",
            "--root",
            str(PACKS),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    item = next(
        row for row in json.loads(seed.stdout)["capabilities"]
        if row["catalog_entry"]["id"] == "integrations/google-meet-brain"
    )
    assert item["catalog_entry"]["install_mode"] == "image"
    assert item["catalog_entry"]["backend_name"] == "gmeet_connector"
    assert item["capability_manifest"]["runtime"]["language"] == "brain_connector"
    assert item["capability_manifest"]["brain_source"]["config"]["acl_mode"] == (
        "calendar_invitees_strict"
    )


def test_deploy_manifest_preserves_smoke_probe_inputs():
    pack = PACKS / "tabular" / "wine-quality-sklearn"
    proc = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "render",
            "--part",
            "deploy",
            str(pack),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    assert '"smoke"' in proc.stdout
    assert "fixed acidity" in proc.stdout


def test_scaffold_includes_handler_extra_requirements(tmp_path):
    pack = PACKS / "extract" / "gliner-medium-v2.1"
    out = tmp_path / "gliner"
    subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "scaffold",
            str(pack),
            str(out),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    requirements = (out / "requirements.txt").read_text(encoding="utf-8")
    assert "gliner==0.2.16" in requirements


def test_builtin_packs_have_acceptance_sql():
    missing: list[str] = []
    invalid: list[str] = []
    for path, pack in _pack_docs():
        acceptance = pack.get("acceptance") or {}
        tests = acceptance.get("tests") or []
        if not tests:
            missing.append(str(path.relative_to(PACKS.parent.parent)))
            continue
        for test in tests:
            name = test.get("name") if isinstance(test, dict) else None
            sql = test.get("sql") if isinstance(test, dict) else None
            if not name or not isinstance(sql, str) or not sql.strip():
                invalid.append(f"{path.relative_to(PACKS.parent.parent)}:{name}")

    assert not missing, "built-in packs without acceptance.tests: " + ", ".join(missing)
    assert not invalid, "invalid acceptance tests: " + ", ".join(invalid)


def test_acceptance_sql_covers_exported_operators():
    gaps: list[str] = []
    for path, pack in _pack_docs():
        operators = (pack.get("exports") or {}).get("operators") or []
        if not operators:
            continue
        acceptance = pack.get("acceptance") or {}
        haystack = "\n".join(
            test.get("sql", "")
            for test in acceptance.get("tests") or []
            if isinstance(test, dict)
        )
        missing = []
        for op in operators:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_])(?:rvbbit\.)?{re.escape(op)}\s*\(",
                re.IGNORECASE,
            )
            if not pattern.search(haystack):
                missing.append(op)
        if missing:
            gaps.append(f"{path.relative_to(PACKS.parent.parent)} missing={missing}")

    assert not gaps, "acceptance SQL should call every exported operator: " + "; ".join(gaps)


def test_acceptance_targets_are_explicit_selectors():
    bad: list[str] = []
    for path, pack in _pack_docs():
        acceptance = pack.get("acceptance") or {}
        target = acceptance.get("target_selector") or {}
        if not isinstance(target, dict):
            bad.append(str(path.relative_to(PACKS.parent.parent)))
            continue
        if not acceptance.get("tests"):
            continue
        if target.get("capability") is True:
            continue
        if target.get("sql") is True and target.get("capability") is False:
            continue
        bad.append(str(path.relative_to(PACKS.parent.parent)))

    assert not bad, "acceptance target_selector should be capability:true or sql:true/capability:false: " + ", ".join(bad)


def test_operator_runtime_packs_are_flagged():
    packs = {pack["id"]: (path, pack) for path, pack in _pack_docs()}
    expected = {"runtimes/python-runtime", "runtimes/mcp-gateway"}
    missing = expected - packs.keys()
    assert not missing, "missing operator runtime packs: " + ", ".join(sorted(missing))

    bad: list[str] = []
    for pack_id in expected:
        path, pack = packs[pack_id]
        tags = set(pack.get("tags") or [])
        if (
            pack.get("system_runtime") is not True
            or pack.get("capability_role") != "operator_runtime"
            or "system-runtime" not in tags
            or "operator-runtime" not in tags
        ):
            bad.append(str(path.relative_to(PACKS.parent.parent)))

    assert not bad, "operator runtime packs missing system-runtime metadata: " + ", ".join(bad)


def test_huggingface_model_packs_publish_gpu_weight_estimates():
    missing: list[str] = []
    invalid: list[str] = []
    for path, pack in _pack_docs():
        source = pack.get("source") or {}
        runtime = pack.get("runtime") or {}
        if source.get("provider") != "huggingface" or runtime.get("device") == "cpu":
            continue
        gpu = ((pack.get("resources") or {}).get("gpu") or {})
        if not gpu:
            missing.append(str(path.relative_to(PACKS.parent.parent)))
            continue
        if (
            gpu.get("placement") != "single_gpu"
            or int(gpu.get("model_size_bytes") or 0) <= 0
            or int(gpu.get("vram_required_bytes") or 0) <= int(gpu.get("model_size_bytes") or 0)
        ):
            invalid.append(str(path.relative_to(PACKS.parent.parent)))

    assert not missing, "HF model packs missing resources.gpu estimates: " + ", ".join(missing)
    assert not invalid, "invalid resources.gpu estimates: " + ", ".join(invalid)


def test_catalog_json_flattens_gpu_weight_estimates():
    proc = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "catalog",
            "build",
            "--root",
            str(PACKS),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    doc = json.loads(proc.stdout)
    by_id = {entry["id"]: entry for entry in doc["capabilities"]}

    bad: list[str] = []
    for path, pack in _pack_docs():
        source = pack.get("source") or {}
        runtime = pack.get("runtime") or {}
        if source.get("provider") != "huggingface" or runtime.get("device") == "cpu":
            continue
        gpu = ((pack.get("resources") or {}).get("gpu") or {})
        entry = by_id.get(pack["id"])
        expected_required = bool(gpu.get("required")) or runtime.get("device") == "cuda"
        if not entry:
            bad.append(f"{path.relative_to(ROOT)} missing catalog entry")
            continue
        if (
            entry.get("gpu_required") != expected_required
            or entry.get("gpu_placement") != gpu.get("placement")
            or entry.get("model_size_bytes") != gpu.get("model_size_bytes")
            or entry.get("vram_required_bytes") != gpu.get("vram_required_bytes")
            or entry.get("vram_headroom_pct") != gpu.get("headroom_pct")
        ):
            bad.append(str(path.relative_to(ROOT)))

    assert not bad, "catalog JSON must expose flattened GPU weight estimates: " + "; ".join(bad)


def test_managed_clover_pack_ships_refreshable_offline_snapshot():
    proc = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "catalog",
            "seed-json",
            "--root",
            str(PACKS),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    doc = json.loads(proc.stdout)
    item = next(
        entry
        for entry in doc["capabilities"]
        if entry["catalog_entry"]["id"] == "managed/clover"
    )
    catalog = item["catalog_entry"]
    manifest = item["capability_manifest"]

    assert "catalog_url" not in catalog
    assert "catalog_refresh_seconds" not in catalog
    assert catalog["catalog_seed_snapshot"] is True
    assert "clover_embed" in catalog["operators"]
    assert any(
        "clover_embed" in statement
        for statement in manifest["managed"]["install"]["sql"]
    )

    pack = PACKS / "managed" / "clover"
    register = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "render",
            "--part",
            "register",
            str(pack),
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    compose = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "render",
            "--part",
            "compose",
            str(pack),
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert "create_operator('clover_embed'" in register
    assert "services:" not in compose
    assert "no Docker sidecar" in compose


def test_managed_clover_pack_function_copies_match_latest_migrations():
    # The Clover installer runs after the schema, so any pack copy of an
    # extension-owned function must equal its latest migration or it silently
    # reverts that migration on install (this lost decision-tree `inputs`).
    spec = importlib.util.spec_from_file_location(
        "pack_functions", ROOT / "scripts" / "clover" / "pack_functions.py"
    )
    pack_functions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pack_functions)
    rows = pack_functions.report()
    stale = [f"{name}{types} (latest {source})" for name, types, state, source in rows if state == "stale"]
    assert not stale, "run scripts/clover/pack_functions.py sync, then make capability-catalog: " + ", ".join(stale)
    assert any(name == "decision_tree" and state == "current" for name, _, state, _ in rows)


def test_managed_clover_sentiment_returns_the_label():
    # The self-hosted sentiment route answers with a whole judgment (label,
    # score, model, contract, usage); the text operator must project the label
    # like clover_classify / clover_language, not return the JSON blob.
    pack = json.loads((PACKS / "managed" / "clover" / "capability.json").read_text())
    create = next(s for s in pack["managed"]["install"]["sql"]
                  if s.startswith("SELECT rvbbit.create_operator('clover_sentiment'"))
    steps = json.loads(re.search(r"op_steps := '(.*)'::jsonb", create).group(1))
    assert steps[-1]["fn"] == "json_get" and steps[-1]["inputs"]["path"] == "label"
    tests = next(s for s in pack["managed"]["install"]["sql"]
                 if "WHERE name = 'clover_sentiment'" in s and s.startswith("UPDATE rvbbit.operators SET tests"))
    assert '"type": "exact", "value": "positive"' in tests and '"contains"' not in tests


def test_release_catalog_can_emit_image_mode_runtimes():
    proc = subprocess.run(
        [
            str(ROOT / "capabilities" / "tools" / "rvbbit-capability"),
            "catalog",
            "seed-json",
            "--root",
            str(PACKS),
            "--image-prefix",
            "ghcr.io/ryrobes",
            "--image-tag",
            "9.9.9",
            "--default-install-mode",
            "image",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    doc = json.loads(proc.stdout)
    by_id = {entry["catalog_entry"]["id"]: entry for entry in doc["capabilities"]}

    python_runtime = by_id["runtimes/python-runtime"]
    assert python_runtime["catalog_entry"]["runtime_mode"] == "image"
    assert python_runtime["catalog_entry"]["runtime_image"] == (
        "ghcr.io/ryrobes/rvbbit-python-runtime:9.9.9"
    )
    assert python_runtime["capability_manifest"]["runtime"]["mode"] == "image"
    assert python_runtime["capability_manifest"]["runtime"]["image"] == (
        "ghcr.io/ryrobes/rvbbit-python-runtime:9.9.9"
    )

    reranker = by_id["rerank/bge-reranker-base"]
    assert reranker["catalog_entry"]["runtime_image"] == (
        "ghcr.io/ryrobes/rvbbit-capability-bge-reranker-base:9.9.9"
    )
    assert reranker["capability_manifest"]["runtime"]["image"] == (
        "ghcr.io/ryrobes/rvbbit-capability-bge-reranker-base:9.9.9"
    )

    gemma = by_id["llm/gemma-4-12b-it-vllm"]
    assert gemma["catalog_entry"]["runtime_image"] == "vllm/vllm-openai:latest"
    assert gemma["capability_manifest"]["runtime"]["image"] == "vllm/vllm-openai:latest"
    assert gemma["catalog_entry"]["provider_name"] == "gemma_4_12b_it"
    assert gemma["catalog_entry"]["provider_model"] == "google/gemma-4-12B-it"


def test_mcp_gateway_pack_is_self_contained():
    pack_dir = PACKS / "runtimes" / "mcp-gateway"
    required = {
        "rvbbit-pack.yaml",
        "capability.yaml",
        "Dockerfile",
        "main.py",
        "requirements.txt",
        "mcp-test-server/main.py",
    }
    missing = [
        rel
        for rel in sorted(required)
        if not (pack_dir / rel).exists()
    ]
    assert not missing, "mcp-gateway pack is missing files: " + ", ".join(missing)


def test_uber_compose_bootstraps_baseline_capabilities():
    compose_path = ROOT / "docker" / "docker-compose.uber.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]

    assert {"postgres", "lens", "warren", "bootstrap"} <= set(services)
    bootstrap_env = services["bootstrap"]["environment"]
    assert bootstrap_env["RVBBIT_UBER_BOOTSTRAP_CAPABILITIES"] == (
        "${RVBBIT_UBER_BOOTSTRAP_CAPABILITIES:-"
        "smoke/warren-echo,runtimes/python-runtime,runtimes/mcp-gateway,"
        "data/dlt-mirror}"
    )
    assert services["bootstrap"]["command"] == ["rvbbit-uber-bootstrap"]

    postgres_health = services["postgres"]["healthcheck"]["test"]
    assert postgres_health[0] == "CMD-SHELL"
    assert "grep -qx postgres /proc/1/comm" in postgres_health[1]
    assert "pg_isready" in postgres_health[1]

    duck = services["duck"]
    assert duck["depends_on"]["postgres"]["condition"] == "service_healthy"
    duck_start = "\n".join(duck["command"])
    assert "until pg_isready -h postgres" in duck_start
    assert 'exec rvbbit-duck' in duck_start
    assert '--dsn "$${RVBBIT_DSN}"' in duck_start
    duck_health = duck["healthcheck"]["test"]
    assert duck_health[0] == "CMD-SHELL"
    assert "/proc/1/task/*/comm" in duck_health[1]
    assert "RVBBIT_DUCK_WORKERS" in duck_health[1]

    socket_mount = "${RVBBIT_DOCKER_SOCKET:-/var/run/docker.sock}:/var/run/docker.sock"
    for service_name in ("lens", "warren"):
        service = services[service_name]
        assert service["environment"]["DOCKER_HOST"] == (
            "${RVBBIT_DOCKER_HOST:-unix:///var/run/docker.sock}"
        )
        assert socket_mount in service["volumes"]

    lens = services["lens"]
    assert lens["environment"]["GMEET_SA_KEY_FILE"] == (
        "/run/secrets/gmeet-service-account.json"
    )
    assert lens["environment"]["GMEET_ADMIN_SUBJECT"] == "${GMEET_ADMIN_SUBJECT:-}"
    assert (
        "${GMEET_SA_KEY_FILE:-/dev/null}:/run/secrets/gmeet-service-account.json:ro"
        in lens["volumes"]
    )

    dockerfile = (ROOT / "docker" / "Dockerfile.rvbbit").read_text(encoding="utf-8")
    assert "docker/uber/bootstrap.sh /usr/local/bin/rvbbit-uber-bootstrap" in dockerfile


def test_calliope_compose_is_a_pinned_hosted_full_stack():
    class ComposeLoader(yaml.SafeLoader):
        pass

    def compose_tag(loader, node):
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_scalar(node)

    ComposeLoader.add_constructor("!reset", compose_tag)
    ComposeLoader.add_constructor("!override", compose_tag)
    compose_path = ROOT / "docker" / "docker-compose.calliope.yml"
    compose = yaml.load(compose_path.read_text(encoding="utf-8"), Loader=ComposeLoader)
    services = compose["services"]

    assert {
        "postgres",
        "duck",
        "datarabbit",
        "warren",
        "bootstrap",
        "doc-extract",
        "hindsight",
        "hermes",
        "calliope",
        "origin",
    } <= set(services)
    assert services["calliope"]["profiles"] == []
    assert services["origin"]["profiles"] == []

    lens_env = services["datarabbit"]["environment"]
    assert lens_env["RVBBIT_MODE"] == "burrow"
    assert lens_env["RVBBIT_AUTH_INTERNAL"] == "http://calliope:8765"
    assert lens_env["RVBBIT_APP_BASE_INTERNAL"] == "http://calliope:8765"
    assert services["datarabbit"]["ports"] == [
        "${RVBBIT_LENS_PORT:-127.0.0.1:3000}:3000"
    ]
    assert services["calliope"]["environment"]["WAREHOUSE_LOGIN_UI"] == "lens"
    origin = services["origin"]
    # The customer door's site address is env-driven: blank keeps plain :80,
    # a public hostname makes Caddy the ACME/TLS terminator on :443.
    assert origin["environment"]["ORIGIN_LISTEN"] == "${CALLIOPE_PUBLIC_HOST:-:80}"
    assert origin["environment"]["CALLIOPE_ACME_EMAIL"] == "${CALLIOPE_ACME_EMAIL:-}"
    assert origin["environment"]["BURROW_LISTEN"] == ":81"
    assert origin["ports"] == [
        "${RVBBIT_ORIGIN_PORT:-8080}:80",
        "${RVBBIT_ORIGIN_TLS_PORT:-127.0.0.1:8443}:443",
        "${RVBBIT_BURROW_PORT:-127.0.0.1:3001}:81",
    ]
    assert origin["volumes"] == [
        "./origin/Caddyfile.calliope:/etc/caddy/Caddyfile:ro",
        "rvbbit_origin_data:/data",
        "rvbbit_origin_config:/config",
    ]
    assert "rvbbit_origin_data" in compose["volumes"]
    assert "rvbbit_origin_config" in compose["volumes"]

    hosted_caddy = (
        ROOT / "docker" / "origin" / "Caddyfile.calliope"
    ).read_text(encoding="utf-8")
    assert "{$ORIGIN_LISTEN::80}" in hosted_caddy
    assert "{$BURROW_LISTEN::81}" in hosted_caddy
    assert 'email "{$CALLIOPE_ACME_EMAIL:}"' in hosted_caddy
    # The Floor is the appliance's front door (9fe1dd16).
    assert "redir * /floor 302" in hosted_caddy
    assert "reverse_proxy {$LENS_UPSTREAM:datarabbit:3000}" in hosted_caddy
    assert "reverse_proxy {$WAREHOUSE_UPSTREAM:calliope:8765}" in hosted_caddy
    assert "X-Desktop-Authorization" not in hosted_caddy

    postgres_env = services["postgres"]["environment"]
    assert postgres_env["RVBBIT_CLOVER_KEY"].startswith("${RVBBIT_CLOVER_KEY:?")
    assert postgres_env["RVBBIT_CLOVER_INSTALL_SOURCE"] == "shipped"

    bootstrap_env = services["bootstrap"]["environment"]
    assert bootstrap_env["RVBBIT_CLOVER_REQUIRED"] == "true"
    assert bootstrap_env["RVBBIT_CLOVER_VERIFY_REMOTE"] == "true"
    assert bootstrap_env["RVBBIT_CLOVER_INSTALL_SOURCE"] == "shipped"
    assert bootstrap_env["RVBBIT_CLOVER_REQUIRED_MODEL"].endswith(":-calliope}")
    assert bootstrap_env["RVBBIT_HOSTED_SERVICES"] == "true"
    assert bootstrap_env["RVBBIT_UBER_BOOTSTRAP_CAPABILITIES"].endswith(
        ":-runtimes/python-runtime,runtimes/mcp-gateway,data/dlt-mirror}"
    )

    hindsight = services["hindsight"]
    assert "hindsight-api:1.2.1-slim@sha256:" in hindsight["image"]
    assert hindsight["environment"]["HINDSIGHT_API_DATABASE_SCHEMA"] == "hindsight"
    # Memory work runs on the small model, apart from the chat model's setting.
    assert hindsight["environment"]["HINDSIGHT_API_LLM_MODEL"] == (
        "${RVBBIT_HINDSIGHT_LLM_MODEL:-calliope-luna}"
    )
    # One memory language; the small model drifted into others without it.
    assert hindsight["environment"]["HINDSIGHT_API_LLM_OUTPUT_LANGUAGE"] == (
        "${RVBBIT_HINDSIGHT_OUTPUT_LANGUAGE:-English}"
    )
    assert hindsight["environment"]["HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL"] == "embed"

    hermes = services["hermes"]
    assert services["datarabbit"]["build"] == {
        "context": "..",
        "dockerfile": "datarabbit/Dockerfile.calliope",
        "labels": {
            "org.opencontainers.image.revision": "${CALLIOPE_REVISION:-unknown}",
            "org.opencontainers.image.title": "Calliope DataRabbit",
            "org.opencontainers.image.version": f"${{CALLIOPE_VERSION:-{CALLIOPE_VERSION}}}",
        },
    }
    assert hermes["build"]["dockerfile"] == "docker/Dockerfile.calliope-hermes"
    assert "nousresearch/hermes-agent@sha256:" in hermes["build"]["args"][
        "HERMES_BASE_IMAGE"
    ]
    # Hermes' durable model choice must govern both browser and headless API
    # sessions. API_SERVER_MODEL_NAME would override it inside upstream.
    assert "API_SERVER_MODEL_NAME" not in hermes["environment"]
    assert hermes["environment"]["HINDSIGHT_API_URL"] == "http://hindsight:8888"
    assert hermes["depends_on"]["hindsight"]["condition"] == "service_started"
    assert hermes["depends_on"]["calliope"]["condition"] == "service_healthy"
    assert hermes["volumes"] == ["rvbbit_hermes:/opt/data"]
    assert "hermes" not in services["calliope"]["depends_on"]
    assert services["calliope"]["environment"]["WAREHOUSE_HERMES_URL"] == (
        "http://hermes:8642"
    )
    assert services["calliope"]["environment"]["WAREHOUSE_CALLIOPE_EXPORT_ROOTS"] == (
        "/opt/data"
    )
    assert services["calliope"]["volumes"] == [
        "rvbbit_warehouse_mcp:/app/data",
        "rvbbit_hermes:/opt/data:ro",
        "rvbbit_desktop:/var/lib/calliope-desktop:ro",
    ]

    product_images = {
        name: services[name]["image"]
        for name in ("postgres", "datarabbit", "warren", "doc-extract", "hermes", "calliope")
    }
    assert all(f"${{CALLIOPE_VERSION:-{CALLIOPE_VERSION}}}" in image for image in product_images.values())
    assert product_images["calliope"].endswith(f"/calliope-server:${{CALLIOPE_VERSION:-{CALLIOPE_VERSION}}}")
    assert services["origin"]["depends_on"] == {
        "datarabbit": {"condition": "service_started"},
        "calliope": {"condition": "service_healthy"},
    }

    hermes_dockerfile = (
        ROOT / "docker" / "Dockerfile.calliope-hermes"
    ).read_text(encoding="utf-8")
    # Hermes 0.21.4 split mcp_tool.py; the session-identity patch targets the handlers.
    assert "git apply --check --include=tools/mcp_tool_handlers.py" in hermes_dockerfile
    assert "git apply --check --include=agent/prompt_builder.py" in hermes_dockerfile
    assert "git apply --check --include=gateway/platforms/api_server.py" in hermes_dockerfile
    assert "    docker/calliope/SOUL.md \\\n" in hermes_dockerfile
    assert "COPY docker/calliope/hermes-skills/" in hermes_dockerfile
    assert "prettier@3.9.6" in hermes_dockerfile
    assert "eslint@10.8.1" in hermes_dockerfile
    assert "html-validate@11.6.2" in hermes_dockerfile
    assert "calliope-web-check" in hermes_dockerfile
    assert "hermes-deepinfra-output-cap.patch" in hermes_dockerfile
    assert "hermes-explicit-small-context.patch" in hermes_dockerfile
    assert "hermes-tool-result-wire-name.patch" in hermes_dockerfile
    assert "test-hermes-tool-result-wire-name.py" in hermes_dockerfile
    assert "hermes-deferred-tool-argument-unwrap.patch" in hermes_dockerfile
    assert "test-hermes-deferred-tool-argument-unwrap.py" in hermes_dockerfile
    assert "reconcile-hermes-config.py" in hermes_dockerfile
    assert "d337b736aa1e8ebecfab043842d13e4a2d2f48a3" in hermes_dockerfile
    assert "sha256:6bece0644e29a347e5ae17db43c36938c86f171c6f5e0cef18aa2075d331f3a3" in hermes_dockerfile
    assert "hermes-runs-session-history.patch" not in hermes_dockerfile
    assert "gateway/platforms/api_server_runs.py" in hermes_dockerfile
    # Hindsight reads the person's words and recalls only what everyone present could see.
    assert "apply-hermes-memory-scope.py" in hermes_dockerfile
    assert "test-hermes-memory-scope.py" in hermes_dockerfile

    web_check = ROOT / "docker" / "calliope" / "bin" / "calliope-web-check"
    assert web_check.is_file()
    assert web_check.stat().st_mode & 0o111
    assert "EmbeddedSourceParser" in web_check.read_text(encoding="utf-8")

    hermes_config = (ROOT / "docker" / "calliope" / "hermes-config.yaml").read_text(
        encoding="utf-8"
    )
    # Working budget sized to the measured base (334fde3a).
    assert "context_length: 262144" in hermes_config
    assert "max_tokens: 32768" in hermes_config
    output_cap_patch = (
        ROOT / "docker" / "calliope" / "hermes-deepinfra-output-cap.patch"
    ).read_text(encoding="utf-8")
    assert "cannot be greater than" in output_cap_patch
    assert "max_total_tokens" in output_cap_patch
    small_context_patch = (
        ROOT / "docker" / "calliope" / "hermes-explicit-small-context.patch"
    ).read_text(encoding="utf-8")
    assert 'in {"lmstudio", "custom"}' in small_context_patch
    assert "_allow_lmstudio_explicit_below_floor" in small_context_patch
    tool_result_name_patch = (
        ROOT / "docker" / "calliope" / "hermes-tool-result-wire-name.patch"
    ).read_text(encoding="utf-8")
    # Re-ported to Hermes 0.21.4: the wire omits ``name`` and the provider
    # resolves the result by ``tool_call_id`` (a projection, not a rewrite).
    assert "+++ b/agent/transports/chat_completions.py" in tool_result_name_patch
    assert "resolves the result by ``tool_call_id``" in tool_result_name_patch
    argument_unwrap_patch = (
        ROOT / "docker" / "calliope" / "hermes-deferred-tool-argument-unwrap.patch"
    ).read_text(encoding="utf-8")
    assert "_unwrap_redundant_arguments_object" in argument_unwrap_patch
    assert 'set(raw_args) != {"arguments"}' in argument_unwrap_patch
    assert '"arguments" in properties' in argument_unwrap_patch
    calliope_init = (ROOT / "docker" / "calliope" / "013-calliope-config").read_text(
        encoding="utf-8"
    )
    assert '"${hermes_home}/config.yaml"' in calliope_init

    soul = (ROOT / "docker" / "calliope" / "SOUL.md").read_text(encoding="utf-8")
    assert "You are Calliope" in soul
    assert "Hermes is your agent runtime, not your name" in soul

    seed_hook = (
        ROOT / "docker" / "calliope" / "013-calliope-config"
    ).read_text(encoding="utf-8")
    assert "cmp -s \"$soul_path\" /opt/hermes/docker/SOUL.md" in seed_hook
    assert "preserved locally learned Hermes skill" in seed_hook
    # Synchronous recall: the API server builds a fresh agent per request.
    assert '("recall_sync", True)' in seed_hook
    hindsight_seed = json.loads(
        (ROOT / "docker" / "calliope" / "hindsight-config.json").read_text(encoding="utf-8")
    )
    assert hindsight_seed["recall_sync"] is True
    # Under Hermes' 10K-char prefetch spill; the block is replayed with history.
    assert hindsight_seed["recall_max_tokens"] == 1000
    assert '("recall_max_tokens", 1000)' in seed_hook
    for skill_name in (
        "calliope-hosted-setup",
        "calliope-hosted-operations",
        "calliope-live-dashboards",
    ):
        skill = (
            ROOT / "docker" / "calliope" / "hermes-skills" / skill_name / "SKILL.md"
        )
        assert skill.is_file()
        assert f"name: {skill_name}" in skill.read_text(encoding="utf-8")

    dashboard_skill = (
        ROOT / "docker" / "calliope" / "hermes-skills"
        / "calliope-live-dashboards" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "/opt/data/sales-dashboard.html" in dashboard_skill
    assert "Never send the user to the scratch HTML file" in dashboard_skill
    assert "system-wide inventory" in dashboard_skill
    assert "Do not begin by listing schemas/tables" in dashboard_skill
    assert "search only when its identity is unknown" in dashboard_skill

    warehouse_server = (ROOT / "services" / "calliope" / "server.py").read_text(
        encoding="utf-8"
    )
    assert "USE_RVBBIT_DISCOVERY" in warehouse_server
    assert "capability_search is the system-wide Library" in warehouse_server

    skill_patch = (
        ROOT / "docker" / "calliope" / "hermes-skill-index.patch"
    ).read_text(encoding="utf-8")
    assert "Skills (on demand)" in skill_patch
    assert "capability_search returns compact cap_skill results" in skill_patch
    assert "do not call skills_list or skill_view merely because" in skill_patch
    assert "even partially relevant" not in skill_patch.split('return (', 1)[-1]

    progressive_sql_patch = (
        ROOT / "docker" / "calliope" / "hermes-progressive-sql-results.patch"
    ).read_text(encoding="utf-8")
    assert 'tool_complete_callback=_tool_complete' in progressive_sql_patch
    # Results reach the Stage as receipt fields; generic results stay private.
    assert "payload.update(receipt_fields(result_name, result_args, function_result))" in progressive_sql_patch
    assert "Generic tool results are deliberately not" in progressive_sql_patch


def test_calliope_floor_is_routed_by_both_hosted_doors():
    hosted_caddy = (
        ROOT / "docker" / "origin" / "Caddyfile.calliope"
    ).read_text(encoding="utf-8")
    # The customer door currently has a Warehouse fallback, but the Burrow
    # door correctly falls back to Lens and would otherwise turn /floor into
    # a 404. Keep every built-in Warehouse shell explicit on both doors.
    assert hosted_caddy.count("/floor /floor/*") == 2


def test_calliope_and_pg_rvbbit_have_independent_version_lines():
    manifest = json.loads(
        (ROOT / "release" / "calliope-release.json").read_text(encoding="utf-8")
    )
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    engine = manifest["rvbbit_engine_version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version)
    assert manifest["calliope_version"] == version
    # The product and the engine move on their own version lines.
    assert re.fullmatch(r"\d+\.\d+\.\d+", engine)
    assert engine != version

    cargo = (ROOT / "Cargo.toml").read_text(encoding="utf-8")
    control = (ROOT / "crates" / "pg_rvbbit" / "pg_rvbbit.control").read_text(
        encoding="utf-8"
    )
    assert f'version = "{engine}"' in cargo
    assert f"default_version = '{engine}'" in control

    datarabbit_dockerfile = (
        ROOT / "datarabbit" / "Dockerfile.calliope"
    ).read_text(encoding="utf-8")
    assert "COPY capabilities/catalog.json" in datarabbit_dockerfile
    assert "COPY capabilities/packs/" in datarabbit_dockerfile
    assert "COPY capabilities/templates/" in datarabbit_dockerfile
    assert "COPY capabilities/tools/" in datarabbit_dockerfile


def test_calliope_image_release_builds_each_product_on_an_isolated_runner():
    workflow_path = ROOT / ".github" / "workflows" / "calliope-images.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    # release/services.json is the single build inventory: the prepare job turns
    # it into the matrix, one isolated runner per image plus the runtimes.
    release_step = next(
        step for step in jobs["prepare"]["steps"]
        if step.get("name") == "Validate release contract"
    )
    assert 'matrix="$(python3 scripts/release/shipping.py matrix)"' in release_step["run"]
    matrix = json.loads(subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release" / "shipping.py"), "matrix"],
        check=True, capture_output=True, text=True,
    ).stdout)
    inventory = json.loads((ROOT / "release" / "services.json").read_text(encoding="utf-8"))
    assert matrix["service"] == [*inventory, "runtimes"]

    publish = jobs["publish"]
    assert publish["needs"] == "prepare"
    assert publish["strategy"]["fail-fast"] is False
    assert publish["strategy"]["matrix"] == "${{ fromJSON(needs.prepare.outputs.matrix) }}"

    steps = publish["steps"]
    reclaim_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Reclaim hosted-runner build space"
    )
    buildx_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("uses") == "docker/setup-buildx-action@v3"
    )
    assert reclaim_index < buildx_index

    build_step = next(
        step
        for step in steps
        if step.get("name") == "Build and push versioned images"
    )
    assert build_step["env"]["CALLIOPE_SERVICE"] == "${{ matrix.service }}"
    assert 'args=(--service "$CALLIOPE_SERVICE")' in build_step["run"]
    assert 'scripts/release/build-calliope.sh "${args[@]}"' in build_step["run"]

    build_script = (
        ROOT / "scripts" / "release" / "build-calliope.sh"
    ).read_text(encoding="utf-8")
    assert "--service SERVICE" in build_script
    assert 'shipping.py" build --service "$service"' in build_script
    assert 'shipping.py" build-runtimes' in build_script


def test_hosted_bootstrap_reapplies_shipped_clover_on_existing_volumes():
    bootstrap = (ROOT / "docker" / "uber" / "bootstrap.sh").read_text(
        encoding="utf-8"
    )
    init = (ROOT / "docker" / "init" / "02-install-clover.sh").read_text(
        encoding="utf-8"
    )

    assert "install_managed_clover" in bootstrap
    assert "refreshing managed/clover from the pinned image snapshot" in bootstrap
    assert "pg_read_file('/usr/share/rvbbit/capabilities/catalog.json')" in bootstrap
    assert "capabilities/packs/managed/clover/capability.json" in bootstrap
    assert "rvbbit.upsert_capability_catalog_entry" in bootstrap
    postgres_dockerfile = (ROOT / "docker" / "Dockerfile.rvbbit").read_text(
        encoding="utf-8"
    )
    assert "chmod -R a+rX /usr/share/rvbbit/capabilities" in postgres_dockerfile
    assert "jsonb_array_elements_text(c.manifest #> '{managed,install,sql}')" in bootstrap
    assert "rvbbit.backend_probe('embed')" in bootstrap
    assert "name='forecast' AND auth_header_env='RVBBIT_CLOVER_KEY'" in bootstrap
    assert "name='clover_forecast'" in bootstrap
    assert "rvbbit.clover_forecast(text,text,jsonb)" in bootstrap
    assert 'RVBBIT_CLOVER_REQUIRED_MODEL:-' in bootstrap
    assert "configure_hosted_clover_defaults" in bootstrap
    assert "rvbbit.bind_extract_entities_to_clover()" in bootstrap
    assert "rvbbit.extract_entities(text,text,jsonb)" in bootstrap
    assert "prepare_hosted_services" in bootstrap
    assert "CREATE EXTENSION IF NOT EXISTS vector" in bootstrap
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public" in bootstrap
    assert "ALTER EXTENSION pg_trgm SET SCHEMA public" in bootstrap
    assert "rvbbit.register_memory_service" in bootstrap
    assert "refresh_hosted_capability_index" in bootstrap
    assert "SELECT rvbbit.capability_crawl();" in bootstrap
    assert "signature: rvbbit.clover_forecast(" in bootstrap
    assert 'RVBBIT_CLOVER_INSTALL_SOURCE:-shipped' in init
    assert 'rvbbit.ai' not in init
    assert 'CLOVER_INSTALL_SOURCE" = "shipped"' in init


def test_operator_capability_documents_are_schema_qualified():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0294_schema_qualified_operator_capabilities.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )

    assert "CREATE OR REPLACE FUNCTION rvbbit.capability_doc" in migration
    assert "THEN 'rvbbit.' || p_signature" in migration
    assert "UPDATE rvbbit.catalog_docs" in migration
    assert "0294_schema_qualified_operator_capabilities" in registry


def test_business_topology_scope_excludes_appliance_internal_relations():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0295_business_topology_bounded_scope.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )
    worker = (ROOT / "services" / "calliope" / "business_topology_jobs.py").read_text(
        encoding="utf-8"
    )

    assert "_business_topology_relation_allowed" in migration
    assert "'pg_catalog','information_schema','rvbbit','hindsight'" in migration
    assert "!~* '^_dlt_'" in migration
    assert "appliance-internal and cannot be excavated" in migration
    assert "0295_business_topology_bounded_scope" in registry
    assert "_select_work_for_workflow" in worker
    assert 'include_bridges = bool(params.get("include_bridges", False))' in worker


def test_business_topology_fresh_runs_are_domain_batched_and_overlap_is_optional():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0340_business_topology_scale_resilience.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )
    worker = (ROOT / "services" / "calliope" / "business_topology_jobs.py").read_text(
        encoding="utf-8"
    )

    assert "automatic_domain_batch" in migration
    assert "v_automatic_batch_limit integer := 24" in migration
    assert "p_model text DEFAULT 'calliope'" in migration
    assert "btrim(p_schema) !~* '^_dlt_'" in migration
    assert "prior.last_selected_at NULLS FIRST" in migration
    assert "0340_business_topology_scale_resilience" in registry
    assert "_optional_overlap_evidence" in worker
    assert "overlap-audit.private.json" in worker
    assert "WAREHOUSE_BUSINESS_TOPOLOGY_GLOBAL_OVERLAP_POPULATIONS" in worker


def test_business_topology_stages_only_proposed_synthesis_receipts():
    bundles = (ROOT / "bench" / "business_topology" / "bundles.py").read_text(
        encoding="utf-8"
    )

    assert 'if result.get("status") == "proposed":' in bundles
    assert 'and result.get("status") == "proposed"' in bundles


def test_business_value_graph_is_executable_reconciled_and_registered():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0296_business_value_graph.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )

    assert "business_value_graph_proposals" in migration
    assert "business_value_stage_graph" in migration
    assert "business_value_metric_series" in migration
    assert "business_value_metric_contributions" in migration
    assert "business_value_metric_change" in migration
    assert "rvbbit._synth_validate" in migration
    assert "'currency','count','quantity'" in migration
    assert "0296_business_value_graph" in registry


def test_business_semantic_topology_allows_overlap_and_folds_derived_cubes():
    root = Path(__file__).resolve().parents[1]
    migration_path = (
        root
        / "crates/pg_rvbbit/sql/migrations/0347_business_semantic_topology.sql"
    )
    migration = migration_path.read_text(encoding="utf-8")
    registry = (root / "crates/pg_rvbbit/src/migrations.rs").read_text(encoding="utf-8")

    assert '"0347_business_semantic_topology"' in registry
    assert "'pg_catalog','information_schema','rvbbit','hindsight','cubes'" in migration
    assert "business_semantic_topology" in migration
    assert "source_memberships" in migration
    assert "semantic_relationships" in migration
    assert "DISTINCT ON (candidate.family_id)" in migration
    assert "contains_value_system" in migration
    assert "business_value_system_topology" in migration


def test_business_semantic_ledger_is_assertion_level_and_watchable():
    root = Path(__file__).resolve().parents[1]
    migration = (
        root
        / "crates/pg_rvbbit/sql/migrations/0350_business_semantic_ledger.sql"
    ).read_text(encoding="utf-8")
    registry = (root / "crates/pg_rvbbit/src/migrations.rs").read_text(encoding="utf-8")

    assert '"0350_business_semantic_ledger"' in registry
    assert "business_semantic_assertions" in migration
    assert "business_semantic_revision_diffs" in migration
    assert "business_semantic_watch_specs" in migration
    assert "business_semantic_dossiers" in migration
    assert "business_semantic_dossier_current" in migration
    assert "business_semantic_capture_graph" in migration
    assert "'source_presence','join_coverage','metric_reconciliation'" in migration
    assert "assertion.predicate='joins_to'" in migration
    assert "event_sql_fingerprint" in migration
    assert "v_evaluation-'series'-'changes'-'cube_recommendation'" in migration
    assert "source event SQL is retained only by its owning graph" in migration


def test_business_semantic_watches_append_evidence_without_rewriting_assertions():
    root = Path(__file__).resolve().parents[1]
    migration = (
        root
        / "crates/pg_rvbbit/sql/migrations/0351_business_semantic_watch_execution.sql"
    ).read_text(encoding="utf-8")
    registry = (root / "crates/pg_rvbbit/src/migrations.rs").read_text(encoding="utf-8")
    seed = (root / "docker/init/03-seed-scheduled-tasks.sh").read_text(encoding="utf-8")
    bootstrap = (root / "docker/uber/bootstrap.sh").read_text(encoding="utf-8")

    assert '"0351_business_semantic_watch_execution"' in registry
    assert "business_semantic_watch_observations" in migration
    assert "business_semantic_assertion_states" in migration
    assert "business_semantic_run_watch" in migration
    assert "business_semantic_run_dossier_watches" in migration
    assert "business_semantic_watch_tick" in migration
    assert "schedule_business_semantic_watch_tick" in migration
    assert "FOR UPDATE OF watch SKIP LOCKED" in migration
    assert "observations are append-only" in migration
    assert "NOT measurements ? 'event_sql'" in migration
    assert "needs_decision" in migration
    assert "needs_revision" in migration
    # Retired by docs/MEANING_PLAN.md §5 (2026-09-19): the seeds unschedule the watch
    # tick instead of scheduling it; the migration keeps the helper for a by-hand install.
    for schedule_source in (seed, bootstrap):
        assert "rvbbit_business_semantic_watch_tick" in schedule_source
        assert "schedule_in_database('rvbbit_business_semantic_watch_tick'" not in schedule_source
        assert "cron.unschedule" in schedule_source


def test_business_semantic_dimension_census_is_explicit_and_append_only():
    root = Path(__file__).resolve().parents[1]
    migration = (
        root
        / "crates/pg_rvbbit/sql/migrations/0352_business_semantic_dimension_census.sql"
    ).read_text(encoding="utf-8")
    registry = (root / "crates/pg_rvbbit/src/migrations.rs").read_text(encoding="utf-8")
    ui = (
        root / "datarabbit/src/components/desktop/business-topology-window.tsx"
    ).read_text(encoding="utf-8")

    assert '"0352_business_semantic_dimension_census"' in registry
    assert "business_semantic_dimension_census_snapshots" in migration
    assert "business_semantic_run_dimension_census" in migration
    assert "business_semantic_dimension_census_current" in migration
    assert "coverage_complete" in migration
    assert "member_list_complete" in migration
    assert "taxonomy_changed" in migration
    assert "bounded_sample" in migration
    assert "business_semantic_dimension_census_immutable" in migration
    assert "All observed history" in ui
    assert "period-local" in ui


def test_business_semantic_family_consolidation_is_typed_and_non_destructive():
    root = Path(__file__).resolve().parents[1]
    migration = (
        root
        / "crates/pg_rvbbit/sql/migrations/0353_business_semantic_family_consolidation.sql"
    ).read_text(encoding="utf-8")
    registry = (root / "crates/pg_rvbbit/src/migrations.rs").read_text(encoding="utf-8")
    worker = (root / "services/calliope/business_topology_jobs.py").read_text(encoding="utf-8")
    ui = (
        root / "datarabbit/src/components/desktop/business-topology-window.tsx"
    ).read_text(encoding="utf-8")

    assert '"0353_business_semantic_family_consolidation"' in registry
    assert "business_semantic_family_relationship_proposals" in migration
    assert "business_semantic_family_relationship_decisions" in migration
    assert "same_family" in migration
    assert "parallel_population" in migration
    assert "candidate_fingerprint text NOT NULL UNIQUE" in migration
    assert "proposals never merge or rewrite a family by themselves" in migration
    assert "business-topology-appliance-worker-v8" in worker
    assert "Comparing Value System families" in worker
    assert "Cross-family consolidation" in ui
    assert "no automatic merge" in ui
    assert "Accept classification" in ui
    assert "decideBusinessSemanticFamilyRelationship" in ui


def test_business_topology_retries_failed_family_packets_and_reaps_cancelled_leases():
    root = Path(__file__).resolve().parents[1]
    migration = (
        root
        / "crates/pg_rvbbit/sql/migrations/0354_business_topology_retry_recovery.sql"
    ).read_text(encoding="utf-8")
    registry = (root / "crates/pg_rvbbit/src/migrations.rs").read_text(encoding="utf-8")
    worker = (root / "services/calliope/business_topology_jobs.py").read_text(
        encoding="utf-8"
    )

    assert '"0354_business_topology_retry_recovery"' in registry
    assert "status='cancel_requested'" in migration
    assert "cancel_reconciled_reason" in migration
    assert "worker_lease_expired" in migration
    assert "business-topology-appliance-worker-v8" in worker
    assert "Retrying failed Value System family comparisons" in worker
    assert "family_consolidation_retry_packets" in worker
    assert 'attempts=1' in worker
    assert '"family_consolidation_error": None' in worker
    assert '"current_value_domain_error": None' in worker


def test_business_value_graph_promotion_uses_native_cube_and_metric_primitives():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0297_business_value_promotion.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )

    assert "business_value_graph_promotions" in migration
    assert "business_value_promotion_plan" in migration
    assert "business_value_promote_graph" in migration
    assert "rvbbit.define_cube" in migration
    assert "rvbbit.define_metric" in migration
    assert "_business_value_reconcile_cube" in migration
    assert "business_value_metric_runtime_sql" in migration
    assert "proposal_graph" in migration
    assert "proposal_receipt" in migration
    assert "0297_business_value_promotion" in registry


def test_business_context_projection_is_searchable_and_self_healing():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0298_business_context_retrieval.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )
    warehouse = (ROOT / "services" / "calliope" / "server.py").read_text(
        encoding="utf-8"
    )

    assert "business_context_crawl" in migration
    assert "business_context_search_stale" in migration
    assert "business_context_search" in migration
    assert "business_value_system" in migration
    assert "business_metric" in migration
    assert "business_object" in migration
    assert "business_value_graph_promotions" in migration
    assert "capability_mcp_tools" in migration
    assert "search_business_context" in warehouse
    assert "get_business_value_system" in warehouse
    assert "business_metric_change" in warehouse
    assert "search_business_context" in migration
    assert "get_business_value_system" in migration
    assert "business_metric_change" in migration
    assert "0298_business_context_retrieval" in registry


def test_business_context_projection_keeps_and_repairs_embeddings():
    """0322: the cheap no-embed pre-warm must not destroy vectors, and both the
    cron lane and Warehouse reads must converge on an embedded projection."""
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0322_business_context_embedding_repair.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )
    warehouse = (ROOT / "services" / "calliope" / "server.py").read_text(
        encoding="utf-8"
    )
    notebook = (ROOT / "services" / "calliope" / "calliope.py").read_text(
        encoding="utf-8"
    )

    # The upsert preserves an existing vector for unchanged text instead of
    # blindly writing EXCLUDED.embedding (which is NULL on a no-embed crawl).
    assert "existing.doc IS NOT DISTINCT FROM EXCLUDED.doc THEN existing.embedding" in migration
    assert "\n        embedding=EXCLUDED.embedding," not in migration
    assert "business_context_embed_pending" in migration
    assert "business_context_embed_missing" in migration
    # The frequent lane still pre-warms lexically, then embeds a bounded slice.
    assert "rvbbit.business_context_crawl(false)" in migration
    assert "rvbbit.business_context_embed_missing(200)" in migration
    assert "0322_business_context_embedding_repair" in registry
    assert registry.index("0321_calliope_provider_control") < registry.index(
        "0322_business_context_embedding_repair"
    )
    # Warehouse and the notebook packet both repair missing vectors on read.
    assert "business_context_embed_pending" in warehouse
    assert "business_context_embed_missing(200)" in warehouse
    assert "business_context_embed_missing(200)" in notebook


def test_newly_registered_tables_are_cataloged_promptly():
    """0323: registration enqueues a debounced schema crawl; a 2-minute cron
    tick drains it; registered relations are readable by name."""
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0323_catalog_registration_crawl.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )
    seed = (ROOT / "docker" / "init" / "03-seed-scheduled-tasks.sh").read_text()
    bootstrap = (ROOT / "docker" / "uber" / "bootstrap.sh").read_text()

    assert "CREATE TABLE IF NOT EXISTS rvbbit.catalog_crawl_requests" in migration
    assert "rvbbit.request_catalog_crawl" in migration
    assert "AFTER INSERT ON rvbbit.tables" in migration
    assert "rvbbit.catalog_crawl_requests_tick" in migration
    # It reuses the existing schema-scoped crawl rather than inventing one.
    assert "rvbbit.catalog_crawl(ARRAY[v_req.schema_name], 'db_catalog'" in migration
    assert "waiting_for_rows" in migration
    assert "CREATE OR REPLACE VIEW rvbbit.accelerated_tables" in migration
    assert "'backfill_uncataloged'" in migration
    assert "0323_catalog_registration_crawl" in registry
    assert registry.index("0322_business_context_embedding_repair") < registry.index(
        "0323_catalog_registration_crawl"
    )
    for schedule_source in (seed, bootstrap):
        assert "rvbbit_catalog_registration_crawl" in schedule_source
        assert "rvbbit.catalog_crawl_requests_tick();" in schedule_source
        assert "*/2 * * * *" in schedule_source


def test_orphaned_acceleration_files_are_reaped_hourly_by_default():
    """maintain() reaps 1,000 files per 15-minute pass; a churny appliance can
    outrun that, so every install also schedules an hourly sweep with a large
    ceiling and a 1-hour grace."""
    seed = (ROOT / "docker" / "init" / "03-seed-scheduled-tasks.sh").read_text()
    bootstrap = (ROOT / "docker" / "uber" / "bootstrap.sh").read_text()
    for schedule_source in (seed, bootstrap):
        assert "rvbbit_reap_orphaned_files" in schedule_source
        assert "rvbbit.reap_orphaned_files(interval ''1 hour'', 100000);" in schedule_source
        assert "13 * * * *" in schedule_source


def test_hosted_bootstrap_tunes_autovacuum_for_appliance_tables():
    bootstrap = (ROOT / "docker" / "uber" / "bootstrap.sh").read_text()
    assert "ALTER SYSTEM SET log_autovacuum_min_duration = '30s';" in bootstrap
    assert "autovacuum_vacuum_scale_factor = 0.05, autovacuum_analyze_scale_factor = 0.02" in bootstrap
    for rel in ("rvbbit.mcp_activity", "rvbbit.catalog_docs", "rvbbit.kg_nodes", "rvbbit.brain_chunks"):
        assert f"'{rel}'" in bootstrap
    # Additive only: ordinary relations that exist and carry no reloptions yet —
    # the extension tunes its own storage/receipt tables tighter than this.
    assert "c.relkind = 'r' AND c.reloptions IS NULL" in bootstrap
    assert "'rvbbit.receipts'" not in bootstrap.split("configure_hosted_autovacuum() {")[1].split("configure_hosted_schedules() {")[0]
    assert bootstrap.index("configure_hosted_autovacuum\n") < bootstrap.index("configure_hosted_schedules\nconfigure_hosted_autopilot\nbootstrap_lens_connection")


def test_accel_autopilot_actor_is_scheduled_after_the_observer_and_hosted_opts_into_act():
    """0326 adds a deterministic promote/park/reactivate actor. Every install schedules
    it after the hourly observer; only the hosted bootstrap flips observe -> act."""
    seed = (ROOT / "docker" / "init" / "03-seed-scheduled-tasks.sh").read_text()
    bootstrap = (ROOT / "docker" / "uber" / "bootstrap.sh").read_text()
    for schedule_source in (seed, bootstrap):
        assert "rvbbit_accel_autopilot_act" in schedule_source
        assert "rvbbit.accel_autopilot_act(''scheduler'')" in schedule_source
        assert "37 * * * *" in schedule_source
        assert schedule_source.index("rvbbit_accel_observer") < schedule_source.index("rvbbit_accel_autopilot_act")
    assert "SET mode = 'act'" in bootstrap and "mode = 'observe'" in bootstrap      # only observe -> act, never off -> act
    # The flip must run against the rvbbit database, not inside the pg_cron
    # (postgres-db) schedule block — that exact mistake failed a bootstrap.
    schedules_block = bootstrap.split("configure_hosted_schedules() {")[1].split("\n}\n")[0]
    assert "accel_autopilot_config" not in schedules_block
    autopilot_block = bootstrap.split("configure_hosted_autopilot() {")[1].split("\n}\n")[0]
    assert 'psql "$dsn"' in autopilot_block and "SET mode = 'act'" in autopilot_block
    assert "SET mode = 'act'" not in seed
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text()
    assert '"0325_accel_budget_soft"' in registry and '"0326_accel_autopilot_act"' in registry
    migration = (ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations" / "0326_accel_autopilot_act.sql").read_text()
    for guard in ("act_park_idle_days", "act_park_min_age_days", "act_park_min_daily_cost_ms",
                  "act_park_min_world_calls", "act_max_parks_per_run", "act_max_parks_per_day",
                  "observer_watching", "world_awake", "managed_by = 'autopilot'"):
        assert guard in migration, guard
    budget = (ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations" / "0325_accel_budget_soft.sql").read_text()
    assert "accel_budget_cheap_refresh_ms" in budget and "accel_budget_overdue_factor" in budget
    assert "refreshing anyway" in budget                       # exhausted budgets defer, never deny
    routing = (ROOT / "datarabbit" / "src" / "lib" / "rvbbit" / "routing.ts").read_text()
    assert "daily_refresh_budget => 12" not in routing         # presets no longer cap refreshes per day


def test_provider_state_credential_check_admits_hermes_held_keys():
    """0324: a configured api_key provider may be held by Hermes' own pool; the
    server image ships the file so provider_settings.ensure() applies it at
    startup, in order after 0321."""
    migration = (
        ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations"
        / "0324_calliope_provider_state_hermes_holder.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(encoding="utf-8")
    dockerfile = (ROOT / "services" / "calliope" / "Dockerfile").read_text(encoding="utf-8")
    assert "DROP CONSTRAINT IF EXISTS calliope_provider_state_credential_check" in migration
    assert "metadata->>'credential_holder' = 'hermes'" in migration
    assert registry.index("0323_catalog_registration_crawl") < registry.index(
        "0324_calliope_provider_state_hermes_holder"
    )
    assert "0324_calliope_provider_state_hermes_holder.sql ./sql/" in dockerfile


def test_business_context_steward_is_drift_gated_and_change_aware():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0299_business_context_steward.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )
    schedules = (ROOT / "docker" / "init" / "03-seed-scheduled-tasks.sh").read_text(
        encoding="utf-8"
    )

    assert "business_context_domain_fingerprint" in migration
    assert "business_context_domain_observations" in migration
    assert "business_context_changes" in migration
    assert "business_value_graph_diff" in migration
    assert "business_context_steward_tick" in migration
    assert "business_context_mirror_finished" in migration
    assert "business_context_catalog_finished" in migration
    assert "business_context_workflow_changed" in migration
    assert "source_event_sql_exposed',false" in migration
    assert "business_context_status" in migration
    assert "refresh_business_context" in migration
    assert "business_context_changes" in migration
    assert "0299_business_context_steward" in registry
    assert "rvbbit_business_context_steward" in schedules


def test_business_context_automatic_steward_refreshes_semantic_systems_only():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0348_business_context_semantic_only_steward.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )

    assert "proposal.domain_key=domain.domain_key" in migration
    assert "p_force AND EXISTS" in migration
    assert (
        "physical schema excavation requires an explicit forced request" in migration
    )
    assert "0348_business_context_semantic_only_steward" in registry


def test_business_context_adopts_a_completed_semantic_graph_as_its_baseline():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0349_business_context_adopt_semantic_baseline.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )

    assert "_business_context_adopt_semantic_baseline" in migration
    assert "proposal.workflow_run_id=NEW.last_completed_workflow_run_id" in migration
    assert "NEW.modeled_fingerprint := NEW.observed_fingerprint" in migration
    assert "0349_business_context_adopt_semantic_baseline" in registry


def test_business_context_feedback_loop_is_durable_and_normalized():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0300_business_context_feedback_loop.sql"
    ).read_text()
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text()

    assert "business_context_turn_receipts" in migration
    assert "business_context_turn_evidence" in migration
    assert "business_context_evidence_summary" in migration
    assert "automatic_context" in migration
    assert "user_feedback" in migration
    assert "question_hash" in migration
    assert "user_message" not in migration
    assert "assistant_message" not in migration
    assert "sql text" in migration.lower()
    assert "0300_business_context_feedback_loop" in registry


def test_business_context_automation_repairs_and_debounces_mirror_refreshes():
    migration = (
        ROOT
        / "crates"
        / "pg_rvbbit"
        / "sql"
        / "migrations"
        / "0301_business_context_automation.sql"
    ).read_text()
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text()
    seed = (ROOT / "docker" / "init" / "03-seed-scheduled-tasks.sh").read_text()
    bootstrap = (ROOT / "docker" / "uber" / "bootstrap.sh").read_text()

    assert "data_dirty_since" in migration
    assert "last_mirror_data_at" in migration
    assert "last_data_modeled_at" in migration
    assert "business_context_catalog_repair_tick" in migration
    assert "business_context_data_refresh_tick" in migration
    assert "business_context_maintenance_tick" in migration
    assert "tables_succeeded>0" in migration
    assert "status IS DISTINCT FROM 'pending_catalog'" in migration
    assert "p_min_interval" in migration
    assert "0301_business_context_automation" in registry

    # Retired by docs/MEANING_PLAN.md §5 (2026-09-19): every business-context lane is
    # unscheduled by the seeds (the steward and safety ticks first, then the catalog
    # repair and data refresh lanes that enqueued excavator runs). Meaning's sweep
    # is the one scheduled successor.
    for schedule_source in (seed, bootstrap):
        for retired in ("rvbbit_business_context_steward", "rvbbit_business_context_catalog_repair",
                        "rvbbit_business_context_data_refresh", "rvbbit_business_context_safety",
                        "rvbbit_knowledge_loop", "rvbbit_topology_nightly_sweep", "rvbbit_topology_sweep_continue"):
            assert retired in schedule_source
            assert f"schedule_in_database('{retired}'" not in schedule_source
        assert "schedule_in_database('rvbbit_meaning_sweep'" in schedule_source
        # 0445: the desire path runs daily — dossier candidates become implied metrics, use
        # promotes them into the proposal inbox, silence retires them.
        assert "schedule_in_database('rvbbit_meaning_desire_path'" in schedule_source
        assert "rvbbit.meaning_desire_path_tick()" in schedule_source


def test_product_images_and_repository_pin_the_same_rust_toolchain():
    toolchain = (ROOT / "rust-toolchain.toml").read_text(encoding="utf-8")
    postgres = (ROOT / "docker" / "Dockerfile.rvbbit").read_text(encoding="utf-8")
    warren = (ROOT / "docker" / "Dockerfile.warren-agent").read_text(encoding="utf-8")

    toolchain_version = re.search(r'^channel = "([0-9]+\.[0-9]+\.[0-9]+)"$', toolchain, re.M)
    postgres_version = re.search(r"--default-toolchain ([0-9]+\.[0-9]+\.[0-9]+)", postgres)
    warren_version = re.search(r"^FROM rust:([0-9]+\.[0-9]+)-", warren, re.M)

    assert toolchain_version
    assert postgres_version
    assert warren_version
    assert toolchain_version.group(1) == postgres_version.group(1)
    assert toolchain_version.group(1).startswith(f"{warren_version.group(1)}.")


def test_release_compose_exposes_configurable_docker_socket_to_capability_runners():
    compose_path = ROOT / "docker" / "docker-compose.release.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]
    socket_mount = "${RVBBIT_DOCKER_SOCKET:-/var/run/docker.sock}:/var/run/docker.sock"

    for service_name in ("lens", "warren"):
        service = services[service_name]
        assert service["environment"]["DOCKER_HOST"] == (
            "${RVBBIT_DOCKER_HOST:-unix:///var/run/docker.sock}"
        )
        assert socket_mount in service["volumes"]

    lens = services["lens"]
    assert lens["environment"]["GMEET_SA_KEY_FILE"] == (
        "/run/secrets/gmeet-service-account.json"
    )
    assert lens["environment"]["GMEET_ADMIN_SUBJECT"] == "${GMEET_ADMIN_SUBJECT:-}"
    assert (
        "${GMEET_SA_KEY_FILE:-/dev/null}:/run/secrets/gmeet-service-account.json:ro"
        in lens["volumes"]
    )


def test_calliope_computer_routes_through_the_application_gateway():
    """The shared desktop is served by the application gateway, which revokes
    open control sockets on handoff (91458228). Neither door proxies the
    desktop stream around it or forwards a private desktop header."""
    hosted_caddy = (
        ROOT / "docker" / "origin" / "Caddyfile.calliope"
    ).read_text(encoding="utf-8")
    assert hosted_caddy.count("handle /computer* {\n\t\tredir * /calliope 302") == 2
    assert "X-Desktop-Authorization" not in hosted_caddy


def test_managed_clover_install_keeps_the_embedding_cache():
    """Reinstalling the pack (every bundle upgrade does) must not wipe cached embeddings.

    apply_inference_profile purges the cache when the embedding model or
    dimensions actually change; an unconditional DELETE in the install steps
    threw away 1.5M same-model vectors on the AWS pilot's upgrade.
    """
    for path in (PACKS / "managed" / "clover" / "capability.json",
                 ROOT / "crates" / "pg_rvbbit" / "src" / "capability_catalog_seed.json"):
        assert "DELETE FROM rvbbit.embedding_cache" not in path.read_text(encoding="utf-8"), path
