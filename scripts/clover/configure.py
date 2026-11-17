#!/usr/bin/env python3
"""Generate a private Clover configuration. No provider calls."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets

# A customer-owned gateway must not inherit a shared-GPU subscription quota.
DEFAULT_LANES = 4096

SPECIALISTS = "sentiment rerank nli nli3 classify toxicity language extract ocr document_parse transcribe forecast timeseries_anomalies timeseries_impute timeseries_embed timeseries_similarity image_embed tabular_fit tabular_predict tabular_explain anomaly_fit anomaly_score relations cluster web_scrape web_research computer_decide".split()
ARCTIC = "Snowflake/snowflake-arctic-embed-l-v2.0"
ARCTIC_REVISION = "ac6544c8a46e00af67e330e85a9028c66b8cfd9a"
TEXT_OPERATORS = {
    "sentiment": ["clover_sentiment", "clover_sentiment_score"],
    "nli": ["clover_entails"],
    "nli3": ["clover_nli", "clover_contradicts"],
    "classify": ["clover_classify", "clover_classify_scores"],
    "toxicity": ["clover_toxic", "clover_moderate"],
}
# Three-way NLI is implemented for evaluation, but remains opt-in: the current
# hosted models differ on a legacy schedule/cancellation contradiction fixture.
HOSTED_TEXT_ROUTES = ("sentiment", "nli", "classify", "toxicity")
ANALYTICS_OPERATORS = {"cluster": ["clover_cluster"], "anomaly_fit": ["clover_anomaly_fit"],
                       "anomaly_score": ["clover_anomaly_score"]}
ANALYTICS_CONTRACT = "clover-cpu-analytics-v1/sklearn-1.6.1"
UTILITY_OPERATORS = {"language": ["clover_language", "clover_language_info"],
                     "transcribe": ["clover_transcribe"],
                     "web_scrape": ["clover_web_scrape", "clover_web_markdown"]}
RESEARCH_OPERATORS = {"web_research": ["clover_web_research", "clover_web_research_deep", "clover_web_watch"]}
# Typed-field extraction (OpenRouter Extractions API): one forward pass returns
# every field with a confidence, a null probability and source spans.
EXTRACTION_OPERATORS = {"extractions": ["clover_extract_fields", "_clover_extract_typed"]}
MODEL_OPERATORS = {
    "extract": ["clover_extract", "clover_pii", "extract_entities"],
    "timeseries_anomalies": ["clover_series_anomalies"],
    "timeseries_impute": ["clover_series_impute"],
    "timeseries_embed": ["clover_series_embed"],
    "timeseries_similarity": ["clover_series_similarity"],
}
GLINER_CONTRACT = "clover-extract-v1/urchade/gliner_medium-v2.1@40ec419335d09393f298636f471328b722c6da9e/gliner-0.2.16"
TSPULSE_CONTRACT = "clover-tspulse-v2-cpu/2e64fcdc2a06d3565dfadaf0065c0ab5055f80f2/b12164578f7b893ada0028c00d292ba10383d25a/tsfm-0.3.2"
OPTIONS = json.loads(Path(__file__).with_name("capability_options.json").read_text())


def route_catalog_sql(cfg):
    """Publish non-secret route facts into the existing SQL backend registry."""
    from urllib.parse import urlsplit
    def literal(value):
        return "'" + value.replace("'", "''") + "'"
    statements = []
    # Upstreams that are an older remote Clover deployment rather than a local worker.
    legacy_hosts = {h.strip().lower() for h in os.environ.get('CLOVER_LEGACY_UPSTREAM_HOSTS', '').split(',') if h.strip()}
    for route in cfg['backends'] + [r for r in cfg['llms'] if r['name'] in ('clover','clover-extract')]:
        host = (urlsplit(route.get('upstream_base','')).hostname or '').lower()
        enabled = route.get('enabled', True)
        version = route.get('model_version','')
        origin = ('unconfigured' if not enabled else 'legacy-clover' if host in legacy_hosts
                  else 'hosted-provider' if host=='openrouter.ai' or version.startswith(('openrouter/','clover-decisions-v1/','clover-text-judgments-v1/','clover-transcribe-v1/','clover-research-'))
                  else 'worker')
        facts = {'id':route['name'],'enabled':enabled,'source':origin,'model_version':version}
        if not enabled:
            facts['reason'] = OPTIONS.get(route['name'],{}).get('reason','Configure a compatible worker.')
        names = [{'clover':'clover_llm','clover-extract':'clover_extract_llm'}.get(route['name'],route['name'])]
        statements.append("UPDATE rvbbit.backends SET transport_opts=coalesce(transport_opts,'{}'::jsonb) || jsonb_build_object('clover_route', "
            + literal(json.dumps(facts)) + "::jsonb) WHERE name IN (" + ','.join(literal(n) for n in names)
            + ") AND auth_header_env='RVBBIT_CLOVER_KEY';")
    if not any(r['name']=='clover-extract' for r in cfg['llms']):
        statements.append("UPDATE rvbbit.backends SET transport_opts=coalesce(transport_opts,'{}'::jsonb) || "
            "jsonb_build_object('clover_route', '{\"id\":\"clover-extract\",\"enabled\":false,\"source\":\"unconfigured\",\"reason\":\"NuExtract chat route is not configured.\"}'::jsonb) "
            "WHERE name='clover_extract_llm' AND auth_header_env='RVBBIT_CLOVER_KEY';")
    return statements


def configuration(
    embedding="openrouter",
    embedding_model="voyageai/voyage-4",
    chat="openai/gpt-6-luna",
    overrides=None,
    embedding_dimensions=1024,
    decision_model="typesafe/jev-1.13",
    # rerank-3-lite shares one busy Voyage rate limit across OpenRouter: it refused
    # about one 300-document call in six (429). rerank-3 refused none in 60.
    rerank_model="voyageai/rerank-3",
    analytics_worker=None,
    transcription_model="openai/gpt-transcribe",
    research=False,
    research_model="openai/gpt-6-luna",
    gliner_worker=None,
    tspulse_worker=None,
    sql_model="openai/gpt-6-luna",
    service_host=None,
    bind=None,
    extraction_model="cloudflare/clairic",
):
    """`chat` serves the Calliope agent (`calliope`); `sql_model` serves every
    SQL LLM operator (`clover`). They are separate so a strong agent model does
    not silently make each row-level SQL call that expensive. `service_host`
    replaces the Compose service names in worker URLs for installs whose
    workers listen on one host (for example Docker host networking on
    127.0.0.1)."""
    if not 1 <= embedding_dimensions <= 4096:
        raise ValueError("Embedding dimensions must be between 1 and 4096")
    if service_host is not None and (not service_host or any(c in service_host for c in "/:@?#[] \t\r\n")):
        raise ValueError("Service host must be a bare hostname or IPv4 address")

    def service_url(service, port):
        return f"http://{service_host or service}:{port}"

    routes = [
        dict(
            name=n,
            entitlement="clover",
            enabled=False,
            model_version="unconfigured",
            unavailable_reason=OPTIONS.get(n, {}).get("reason", "No compatible worker configured. Enable this route in Clover with a local worker or hosted endpoint."),
        )
        for n in SPECIALISTS
    ]
    routes += [
        dict(
            name="decisions",
            entitlement="clover",
            adapter="predict",
            upstream_base=service_url("clover-decisions", 8093),
            upstream_path="/predict",
            health_url=service_url("clover-decisions", 8093) + "/health",
            model_version="clover-decisions-v1/" + decision_model,
            timeout_ms=40000,
            unlaned=True,
        )
    ]
    embed = dict(
        name="embed",
        entitlement="clover",
        adapter="openai-embeddings",
        upstream_path="/v1/embeddings",
        unlaned=True,
        timeout_ms=120000,
    )
    if embedding == "cpu":
        embed.update(
            upstream_base=service_url("clover-embeddings", 8085),
            health_url=service_url("clover-embeddings", 8085) + "/health",
            model_version="clover-v1/arctic-embed-l-v2.0",
        )
    else:
        embed.update(
            upstream_base="https://openrouter.ai/api",
            upstream_bearer_token_env="OPENROUTER_API_KEY",
            upstream_params={"model": embedding_model, "dimensions": embedding_dimensions, "encoding_format": "float"},
            model_version=f"openrouter/{embedding_model}@{embedding_dimensions}-float-v1",
        )
    routes.append(embed)
    # Hosted like rerank and embeddings: it needs only the OpenRouter key every
    # Clover install already has. None turns it off; extraction then uses the
    # generalist LLM, as before.
    if extraction_model:
        routes.append(dict(
            name="extractions",
            entitlement="clover",
            adapter="hosted-extractions",
            upstream_base="https://openrouter.ai/api",
            upstream_path="/alpha/extractions",
            upstream_bearer_token_env="OPENROUTER_API_KEY",
            upstream_params={"model": extraction_model},
            model_version="openrouter/" + extraction_model,
            timeout_ms=60000,
            parallel_requests=8,
            unlaned=True,
        ))
    else:
        routes.append(dict(
            name="extractions",
            entitlement="clover",
            enabled=False,
            model_version="unconfigured",
            unavailable_reason=OPTIONS.get("extractions", {}).get("reason", "Typed-field extraction is turned off."),
        ))
    for worker, names, version in [
        (gliner_worker, ["extract"], GLINER_CONTRACT),
        (tspulse_worker, [n for n in MODEL_OPERATORS if n.startswith("timeseries_")], TSPULSE_CONTRACT),
    ]:
        if not worker:
            continue
        from urllib.parse import urlsplit
        url = urlsplit(worker)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise ValueError("Model worker must be an HTTP(S) origin without credentials or a path")
        for name in names:
            next(r for r in routes if r["name"] == name).update(
                enabled=True, unavailable_reason=None, adapter="predict", upstream_base=worker.rstrip("/"),
                upstream_path=f"/specialists/{name}/predict", health_url=worker.rstrip("/") + "/health",
                model_version=version, timeout_ms=300000, unlaned=True)
    if analytics_worker:
        from urllib.parse import urlsplit
        url = urlsplit(analytics_worker)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise ValueError("Analytics worker must be an HTTP(S) origin without credentials or a path")
        for name in ANALYTICS_OPERATORS:
            route = next(r for r in routes if r["name"] == name)
            version = ANALYTICS_CONTRACT + ("/" + embed["model_version"] if name == "cluster" else "")
            route.update(enabled=True, unavailable_reason=None, adapter="predict",
                         upstream_base=analytics_worker.rstrip("/"),
                         upstream_path=f"/specialists/{name}/predict", health_url=analytics_worker.rstrip("/") + "/health",
                         model_version=version, timeout_ms=300000, unlaned=True)
    rerank = next(r for r in routes if r["name"] == "rerank")
    rerank.update(enabled=True, unavailable_reason=None, adapter="hosted-rerank",
                  upstream_base="https://openrouter.ai/api", upstream_path="/v1/rerank",
                  upstream_bearer_token_env="OPENROUTER_API_KEY",
                  upstream_params={"model":rerank_model},
                  model_version="openrouter/" + rerank_model, timeout_ms=60000, unlaned=True)
    for name in HOSTED_TEXT_ROUTES:
        route = next(r for r in routes if r["name"] == name)
        route.update(enabled=True, unavailable_reason=None, adapter="predict",
                     upstream_base=service_url("clover-decisions", 8093),
                     upstream_path=f"/specialists/{name}/predict",
                     health_url=service_url("clover-decisions", 8093) + "/health",
                     model_version="clover-text-judgments-v1/" + decision_model,
                     timeout_ms=180000, unlaned=True)
    for name, version, origin, path, health, timeout in [
        ("language", "clover-language-v1/lingua-2.1.1-low", service_url("clover-utilities", 8094), "/specialists/language/predict", "/health", 30000),
        ("transcribe", "clover-transcribe-v1/" + transcription_model, service_url("clover-utilities", 8094), "/specialists/transcribe/predict", "/health/transcribe", 240000),
        ("web_scrape", "clover-web-v0.3/firecrawl-html-extractor-0a2b743/anydoc-0.1.3", service_url("clover-web", 8091), "/v1/scrape", "/health", 90000),
    ]:
        next(r for r in routes if r["name"] == name).update(
            enabled=True, unavailable_reason=None, adapter="predict", upstream_base=origin,
            upstream_path=path, health_url=origin + health, model_version=version,
            timeout_ms=timeout, unlaned=name != "transcribe")
    if research:
        next(r for r in routes if r["name"] == "web_research").update(
            enabled=True, unavailable_reason=None, adapter="predict",
            upstream_base=service_url("clover-research", 8092), upstream_path="/v1/research",
            health_url=service_url("clover-research", 8092) + "/health", forward_tenant_scope=True,
            model_version="clover-research-v0.3/" + research_model + "/exa/postgres-v1",
            timeout_ms=300000)
    for name, values in (overrides or {}).items():
        route = next((r for r in routes if r["name"] == name), None)
        if (
            route is None
            or not isinstance(values, dict)
            or set(values)
            - {
                "adapter",
                "upstream_base",
                "upstream_path",
                "upstream_params",
                "upstream_bearer_token_env",
                "model_version",
                "timeout_ms",
                "health_url",
                "forward_tenant_scope",
                "enabled",
                "unavailable_reason",
            }
        ):
            raise ValueError("Unknown route or unsupported route fields: " + name)
        if values.get("enabled", True):
            if (
                not values.get("model_version")
                or not values.get("upstream_base")
                or not values.get("upstream_path")
            ):
                raise ValueError(
                    "Enabled route overrides require upstream_base, upstream_path and model_version: "
                    + name
                )
            # Standalone HF endpoints and remote Clover workers can expose Predict.
            for field in list(route):
                if field.startswith("upstream_") or field == "health_url":
                    route.pop(field)
            route.update(adapter="predict", enabled=True, unavailable_reason=None)
        route.update(values)
    # Route overrides may choose another embedding worker; keep clustering's
    # durable SQL cache identity tied to the actual selected vector profile.
    if analytics_worker and "cluster" not in (overrides or {}):
        next(r for r in routes if r["name"] == "cluster")["model_version"] = ANALYTICS_CONTRACT + "/" + embed["model_version"]
    llms = []
    for name, model in [
        ("calliope", chat),
        ("calliope-luna", "openai/gpt-6-luna"),
        ("clover", sql_model),
    ]:
        route = dict(
            name=name,
            entitlement="clover_llm",
            upstream_base="https://openrouter.ai/api",
            provider="openrouter",
            upstream_bearer_token_env="OPENROUTER_API_KEY",
            upstream_model=model,
            model_version="openrouter/" + model,
            timeout_ms=180000,
        )
        if name == "clover":
            route.update(request_defaults={"temperature": 0})
            if model == "openai/gpt-6-luna":
                # Short SQL classifiers often have 16-token answer budgets.
                # Leave reasoning opt-in so it cannot consume that whole budget.
                route["request_defaults"]["reasoning"] = {"effort": "none"}
        llms.append(route)
    return dict(
        bind=bind or (f"{service_host}:8090" if service_host else "0.0.0.0:8090"),
        tenants_file="tenants.json",
        meter_db="/var/lib/hutch/hutch_meter.sqlite",
        max_body_bytes=8000000,
        upstream={"mode": "proxy", "base_url": embed["upstream_base"]},
        backends=routes,
        llms=llms,
        speech=[
            dict(
                name="calliope-voice",
                entitlement="clover_llm",
                provider="openrouter",
                upstream_base="https://openrouter.ai/api/v1",
                upstream_bearer_token_env="OPENROUTER_API_KEY",
                upstream_model="fish-audio/s2.1-pro",
                default_voice="40c20f2514ff4869a09ffcc840127f66",
                model_version="fish-s2.1-pro-v1",
                max_input_characters=30000,
                allow_voice_override=True,
            )
        ],
        media=[
            dict(
                name=name,
                capability=capability,
                entitlement="clover_llm",
                provider="openrouter",
                upstream_base="https://openrouter.ai/api/v1",
                upstream_path=path,
                upstream_bearer_token_env="OPENROUTER_API_KEY",
                upstream_model=model,
                model_version="openrouter/" + model + "@media-v3",
            )
            for name, capability, path, model in [
                ("calliope-image", "image", "/images", "google/gemini-3.1-flash-image"),
                (
                    "calliope-stt",
                    "transcription",
                    "/audio/transcriptions",
                    transcription_model,
                ),
            ]
        ],
    )


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--embedding", choices=["cpu", "openrouter"], default="openrouter")
    parser.add_argument("--embedding-model", default="voyageai/voyage-4")
    parser.add_argument("--embedding-dimensions", type=int, default=1024)
    parser.add_argument("--decision-model", default="typesafe/jev-1.13")
    parser.add_argument("--rerank-model", default="voyageai/rerank-3")
    parser.add_argument("--extraction-model", default="cloudflare/clairic",
                        help="OpenRouter typed-field extraction model for clover_extract_json and clover_extract_fields; 'none' turns it off")
    parser.add_argument("--transcription-model", default="openai/gpt-transcribe")
    parser.add_argument("--research", action="store_true", help="Enable CPU Web Research; requires research.py bootstrap and Compose research profile")
    parser.add_argument("--research-model", default="openai/gpt-6-luna")
    parser.add_argument("--analytics-worker", help="CPU analytics origin, e.g. http://rvbbit-clover-analytics:8080 for a Warren installation")
    parser.add_argument("--gliner-worker", help="Optional Warren GLiNER origin, e.g. http://rvbbit-clover-gliner:8080")
    parser.add_argument("--tspulse-worker", help="Optional Warren TSPulse origin, e.g. http://rvbbit-clover-tspulse:8080")
    parser.add_argument("--provider-key-file", type=Path, help="Read OpenRouter key without exposing it in command arguments")
    parser.add_argument("--chat-model", default="openai/gpt-6-luna",
                        help="Calliope agent model (`calliope` route)")
    parser.add_argument("--sql-model", default="openai/gpt-6-luna",
                        help="Model for SQL LLM operators (`clover` route); keep it fast and cheap")
    parser.add_argument("--service-host",
                        help="Host every Clover worker listens on (e.g. 127.0.0.1 with Docker host networking) instead of Compose service names")
    parser.add_argument("--bind", help="Gateway listen address (default 0.0.0.0:8090, or <service-host>:8090)")
    parser.add_argument("--lanes", type=int, default=DEFAULT_LANES,
                        help="Instance-wide gateway concurrency ceiling (default: 4096); independent of worker/provider capacity")
    parser.add_argument(
        "--routes",
        type=Path,
        help="JSON object of compatible specialist route overrides",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace generated configuration; preserve existing local service/admin keys",
    )
    args = parser.parse_args()
    if args.lanes < 1:
        parser.error("--lanes must be at least 1")
    directory = args.output.expanduser().resolve()
    if (directory / "hutch.json").exists() and not args.force:
        parser.error("Configuration exists; edit it directly or explicitly use --force")
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    previous = (
        json.loads((directory / "local-keys.json").read_text())
        if (directory / "local-keys.json").exists()
        else {}
    )
    key = (
        os.environ.get("RVBBIT_CLOVER_KEY")
        or previous.get("service_key")
        or secrets.token_urlsafe(32)
    )
    admin = previous.get("admin_key") or secrets.token_urlsafe(32)
    computer = previous.get("computer_key") or secrets.token_urlsafe(32)
    cfg = configuration(
        args.embedding,
        args.embedding_model,
        args.chat_model,
        json.loads(args.routes.read_text()) if args.routes else None,
        args.embedding_dimensions, args.decision_model, args.rerank_model,
        args.analytics_worker, args.transcription_model, args.research, args.research_model,
        args.gliner_worker, args.tspulse_worker,
        sql_model=args.sql_model, service_host=args.service_host, bind=args.bind,
        extraction_model=None if args.extraction_model.lower() == "none" else args.extraction_model,
    )
    # Re-generating inference routes must not silently switch the ledger back to SQLite.
    if (directory / "hutch.json").exists():
        existing_cfg = json.loads((directory / "hutch.json").read_text())
        if existing_cfg.get("ledger_postgres"):
            cfg["ledger_postgres"] = existing_cfg["ledger_postgres"]
    cfg["admin_token"] = admin
    write_private(directory / "hutch.json", json.dumps(cfg, indent=2) + "\n")

    # Diagnostic catalog provenance; this does not migrate cached vectors or receipts.
    def sql_literal(value):
        return "'" + value.replace("'", "''") + "'"

    selected_embed = next(r for r in cfg["backends"] if r["name"] == "embed")
    local_arctic = selected_embed.get("model_version") == "clover-v1/arctic-embed-l-v2.0"
    embed_params = selected_embed.get("upstream_params", {})
    profile = {"embedding_model": embed_params.get("model") or (ARCTIC if local_arctic else selected_embed["model_version"]),
               "embedding_dimensions": embed_params.get("dimensions", 1024 if local_arctic else args.embedding_dimensions),
               "embedding_encoding": "float", "decision_model": args.decision_model, "rerank_model": args.rerank_model}
    write_private(directory / "inference.json", json.dumps(profile, indent=2) + "\n")
    (directory / "inference.json").chmod(0o644)
    catalog = route_catalog_sql(cfg)
    for route in cfg["backends"]:
        if not route.get("enabled", True):
            continue
        model = route.get("upstream_params", {}).get("model")
        if not model and route["model_version"].startswith("clover-text-judgments-v1/"):
            model = route["model_version"].split("/", 1)[1]
        if not model:
            model = (
                ARCTIC
                if route["name"] == "embed"
                and route.get("model_version") == "clover-v1/arctic-embed-l-v2.0"
                else route["model_version"]
            )
        catalog.append(
            "UPDATE rvbbit.backends SET source_provider='self-hosted-clover', source_model="
            + sql_literal(model)
            + ", source_revision="
            + sql_literal(route["model_version"])
            + " WHERE name="
            + sql_literal(route["name"])
            + " AND auth_header_env='RVBBIT_CLOVER_KEY';"
        )
        if route["name"] == "embed" and route.get("model_version") == "clover-v1/arctic-embed-l-v2.0":
            # The CPU worker embeds one request at a time and answers the rest
            # 429; a 32-text batch of long documents also outruns a 30 s timeout.
            catalog.append("UPDATE rvbbit.backends SET batch_size=8,max_concurrent=1,timeout_ms=300000"
                           " WHERE name='embed' AND auth_header_env='RVBBIT_CLOVER_KEY';")
        if route["name"] in {**TEXT_OPERATORS, **ANALYTICS_OPERATORS, **UTILITY_OPERATORS, **RESEARCH_OPERATORS, **MODEL_OPERATORS, **EXTRACTION_OPERATORS}:
            # Operator.model participates in durable result-cache identity.
            # Apply for explicit legacy overrides too, avoiding stale results
            # when moving in either direction or choosing a different model.
            names = ",".join(sql_literal(x) for x in {**TEXT_OPERATORS, **ANALYTICS_OPERATORS, **UTILITY_OPERATORS, **RESEARCH_OPERATORS, **MODEL_OPERATORS, **EXTRACTION_OPERATORS}[route["name"]])
            operator_guard = (" AND steps @> " + sql_literal(json.dumps([{"kind": "specialist", "specialist": route["name"]}]))
                              + "::jsonb") if route["name"] in MODEL_OPERATORS else ""
            catalog.append("UPDATE rvbbit.operators SET model=" + sql_literal(route["model_version"])
                           + ", updated_at=now() WHERE name IN (" + names + ")" + operator_guard + ";")
            if route["name"] in MODEL_OPERATORS:
                catalog.append("UPDATE rvbbit.backends SET batch_size=8,max_concurrent=1,timeout_ms=" + str(route["timeout_ms"])
                               + " WHERE name=" + sql_literal(route["name"]) + " AND auth_header_env='RVBBIT_CLOVER_KEY';")
            if route["model_version"].startswith("clover-text-judgments-v1/"):
                catalog.append("UPDATE rvbbit.backends SET batch_size=8,max_concurrent=1,timeout_ms=180000 WHERE name="
                               + sql_literal(route["name"]) + " AND auth_header_env='RVBBIT_CLOVER_KEY';")
            if route["name"] in EXTRACTION_OPERATORS:
                # Rows go out as concurrent single-pass requests; Hutch keeps
                # eight in flight, so a batch of 16 is two rounds.
                catalog.append("UPDATE rvbbit.backends SET batch_size=16,max_concurrent=4,timeout_ms=" + str(route["timeout_ms"])
                               + " WHERE name='extractions' AND auth_header_env='RVBBIT_CLOVER_KEY';")
            if route["name"] in RESEARCH_OPERATORS:
                catalog.append("UPDATE rvbbit.backends SET batch_size=2,max_concurrent=1,timeout_ms=" + str(route["timeout_ms"])
                               + " WHERE name='web_research' AND auth_header_env='RVBBIT_CLOVER_KEY';")
                catalog.append("UPDATE rvbbit.operators SET cache_policy='never' WHERE name IN (" + names + ");")
            if route["name"] in UTILITY_OPERATORS:
                catalog.append("UPDATE rvbbit.backends SET batch_size=" + ("1" if route["name"] == "transcribe" else "8") + ",max_concurrent=1,timeout_ms=" + str(route["timeout_ms"])
                               + " WHERE name=" + sql_literal(route["name"]) + " AND auth_header_env='RVBBIT_CLOVER_KEY';")
            if route["model_version"].startswith(ANALYTICS_CONTRACT):
                catalog.append("UPDATE rvbbit.backends SET batch_size=4,max_concurrent=1,timeout_ms=300000 WHERE name="
                               + sql_literal(route["name"]) + " AND auth_header_env='RVBBIT_CLOVER_KEY';")
    # The local decisions service batches 8 states per request; one request in
    # flight held Jev to ~8 decisions/s, four measured ~25/s with no errors.
    catalog.append("UPDATE rvbbit.backends SET max_concurrent=4 WHERE name='decisions' AND auth_header_env='RVBBIT_CLOVER_KEY';")
    for route in cfg["llms"]:
        if route["name"] == "clover":
            catalog.append(
                "UPDATE rvbbit.backends SET source_provider='self-hosted-clover', source_model="
                + sql_literal(route["upstream_model"])
                + ", source_revision="
                + sql_literal(route["model_version"])
                + " WHERE name='clover_llm' AND auth_header_env='RVBBIT_CLOVER_KEY';"
            )
    catalog.append("SELECT rvbbit.apply_inference_profile(" + sql_literal(json.dumps(profile)) + "::jsonb);")
    write_private(directory / "catalog.sql", "\n".join(catalog) + "\n")
    # This file contains only route metadata; the Postgres UID must read its bind mount.
    (directory / "catalog.sql").chmod(0o644)
    write_private(
        directory / "tenants.json",
        json.dumps(
            {
                "tenants": [
                    {
                        "id": "self-hosted",
                        "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
                        "status": "active",
                        "lanes": args.lanes,
                        "entitlements": ["clover", "clover_llm"],
                    }
                ]
            },
            indent=2,
        )
        + "\n",
    )
    write_private(
        directory / "local-keys.json",
        json.dumps({"service_key": key, "admin_key": admin, "computer_key": computer})
        + "\n",
    )
    inventory = [{"id": r["name"], "enabled": r.get("enabled", True), "model_version": r["model_version"],
                  "installation": OPTIONS.get(r["name"], {}), "inference_verified": False} for r in cfg["backends"]]
    write_private(directory / "capabilities.json", json.dumps(inventory, indent=2) + "\n")
    (directory / "capabilities.json").chmod(0o644)
    # Provider file is raw KEY=value, never sourced in a shell.
    existing = {}
    env_path = directory / "providers.env"
    if env_path.exists():
        existing = dict(
            line.split("=", 1)
            for line in env_path.read_text().splitlines()
            if "=" in line
        )
    values = {
        k: os.environ.get(k, existing.get(k, ""))
        for k in ["OPENROUTER_API_KEY", "HF_TOKEN", "CLOVER_REMOTE_KEY"]
    }
    if args.provider_key_file:
        values["OPENROUTER_API_KEY"] = args.provider_key_file.expanduser().read_text().strip()
    # The shared file is the runtime source of truth after UI rotation. A --force
    # regeneration must not overwrite it with the older bootstrap env value.
    secret_dir = directory / "credentials"
    secret_dir.mkdir(exist_ok=True)
    secret_dir.chmod(0o755)  # parent configuration directory remains 0700
    shared_key = secret_dir / "openrouter.key"
    if not shared_key.exists() or args.provider_key_file:
        supplied = values["OPENROUTER_API_KEY"]
        if "\n" in supplied or "\r" in supplied:
            parser.error("Credential contains a newline")
        shared_key.write_text(supplied + "\n")
        shared_key.chmod(0o644)  # different worker UIDs, read-only directory mounts
    values["OPENROUTER_API_KEY"] = shared_key.read_text().strip()
    values["CLOVER_DECISION_MODEL"] = args.decision_model
    values["CLOVER_TRANSCRIBE_MODEL"] = args.transcription_model
    values["CLOVER_RESEARCH_MODEL"] = args.research_model
    values["CLOVER_COMPUTER_KEY"] = computer
    if any("\n" in v or "\r" in v for v in values.values()):
        parser.error("Credential contains a newline")
    write_private(env_path, "".join(k + "=" + v + "\n" for k, v in values.items()))
    write_private(
        directory / "computer.env",
        "CLOVER_COMPUTER_KEY=" + computer + "\nHF_TOKEN=" + values["HF_TOKEN"] + "\n",
    )
    gateway = "http://" + (args.service_host or "clover") + ":8090"
    write_private(
        directory / "client.env",
        "RVBBIT_CLOVER_KEY="
        + key
        + "\nCLOVER_DECISION_MODEL=" + args.decision_model
        + "\nRVBBIT_CLOVER_OPENAI_BASE_URL=" + gateway + "/v1\nRVBBIT_CLOVER_BASE_URL=" + gateway + "\n",
    )
    print("Created private Clover configuration in " + str(directory))
    print(
        "Embeddings: "
        + args.embedding
        + ". Provider credential: "
        + ("present" if values["OPENROUTER_API_KEY"] else "missing; edit providers.env")
    )
    if args.embedding != "cpu":
        print(
            "Use a fresh database or explicitly re-embed existing data before selecting this different embedding model."
        )


if __name__ == "__main__":
    main()
