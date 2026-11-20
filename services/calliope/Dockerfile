FROM node:25.0.0-slim AS ui-app-web
WORKDIR /build/apps/calliope-canvas/business/web
COPY apps/calliope-canvas/business/web/package*.json ./
RUN npm ci
COPY apps/calliope-canvas/business/web/*.js apps/calliope-canvas/business/web/*.jsx apps/calliope-canvas/business/web/*.html apps/calliope-canvas/business/web/*.css ./
COPY apps/calliope-canvas/business/catalog.json ../catalog.json
COPY apps/calliope-canvas/business/assets/neighborhood.png ../assets/neighborhood.png
RUN npm run build -- --config vite.published.config.js

FROM python:3.12-slim
WORKDIR /app
COPY services/calliope/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "from mcp.server.fastmcp import FastMCP"
RUN python -m playwright install --with-deps chromium
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY services/calliope/avatar_colors.py ./avatar_colors.py
COPY services/calliope/tool_outcomes.py services/calliope/artifact_map_diagnostics.py ./
COPY services/calliope/capability_discovery.py services/calliope/artifact_runtime_help.py ./
COPY services/calliope/file_imports.py ./
COPY scripts/backfill-agent-avatar-colors.py ./backfill-agent-avatar-colors.py
COPY VERSION ./CALLIOPE_VERSION
COPY services/calliope/artifact_queries.py services/calliope/stage_context.py services/calliope/stage_search.py services/calliope/image_metadata.py services/calliope/specialist_stage_context.py services/calliope/evidence_ranking.py ./
COPY services/calliope/specialist_model_shadow.py services/calliope/specialist_assignment.py services/calliope/tool_arguments.py ./
COPY services/calliope/agent_result_delivery.py services/calliope/specialist_notices.py services/calliope/stage_delivery.py ./
COPY services/calliope/desktop_access.py services/calliope/desktop_runtime.py services/calliope/desktop_tools.py services/calliope/desktop_health.py ./
COPY services/calliope/shared_files.py services/calliope/shared_file_paths.py services/calliope/managed_images.py ./
COPY services/calliope/desktop_decisions.py services/calliope/desktop_decision_token.py ./
COPY services/calliope/desktop_routes.py services/calliope/desktop_viewer.py ./
COPY services/calliope/desktop_agents.py ./
COPY services/calliope/desktop_browser_auth.py ./
COPY services/calliope/desktop_memory.py ./
COPY services/calliope/desktop_learning.py ./
COPY crates/pg_rvbbit/sql/migrations/0461_calliope_computer_methods.sql ./sql/0461_calliope_computer_methods.sql
COPY services/calliope/desktop_evidence.py services/calliope/desktop_terminal.py ./
COPY services/calliope/desktop_storage.py services/calliope/desktop_replay.py ./
COPY services/calliope/metric_presentation.py ./
COPY services/calliope/system_console.py services/calliope/system_ai.py services/calliope/system_checks.py ./
COPY services/calliope/system_updates.py ./
COPY release/calliope-release.json ./calliope-release.json
COPY scripts/clover/capability_options.json ./system_capabilities.json
COPY services/calliope/data_completion.py ./
COPY services/calliope/inference_config.py services/calliope/data_sql.py services/calliope/html_app_capture.py ./
COPY services/calliope/meaning_data.py services/calliope/native_handoff.py services/calliope/ui_decisions.py ./
COPY services/calliope/mirror_actions.py services/calliope/local_sql_review.py services/calliope/workspace_sql.py services/calliope/app_actions.py services/calliope/artifact_preflight.py ./
COPY services/calliope/connection_forms.py services/calliope/connection_workflow.py ./
COPY services/calliope/correspondence.py services/calliope/correspondence_routes.py services/calliope/correspondence.sql ./
COPY services/calliope/sms.py services/calliope/sms.sql services/calliope/sms_simulator.py ./
COPY services/calliope/ops_work.py ./
COPY services/calliope/workspace_jobs.py services/calliope/gallery_jobs.py services/calliope/gallery_tables.py services/calliope/gallery_brain.py ./
COPY services/calliope/thread_export.py services/calliope/voice_hutch.py services/calliope/voice_cues.py services/calliope/voice_tagging.py services/calliope/take_parts.py services/calliope/stage_citations.py ./
COPY services/calliope/stage_capture.py services/calliope/stage_references.py services/calliope/stage_views.py ./
COPY services/calliope/sql/0432_workspace_jobs.sql ./sql/0432_workspace_jobs.sql
COPY crates/pg_rvbbit/sql/migrations/0431_workspace_ddl_housekeeping.sql ./sql/0431_workspace_ddl_housekeeping.sql
COPY crates/pg_rvbbit/sql/migrations/0411_calliope_input_requests.sql ./sql/0411_calliope_input_requests.sql
COPY crates/pg_rvbbit/sql/migrations/0427_calliope_page_refresh.sql ./sql/0427_calliope_page_refresh.sql
COPY crates/pg_rvbbit/sql/migrations/0428_calliope_page_tools.sql ./sql/0428_calliope_page_tools.sql
COPY services/calliope/server.py services/calliope/stage_boards.py services/calliope/stage_arrangements.py services/calliope/stage_values.py services/calliope/stage_subjects.py services/calliope/meaning_jobs.py services/calliope/semantic_contracts.py services/calliope/auth.py services/calliope/calliope.py services/calliope/calliope_pages.py services/calliope/floor_data.py services/calliope/dream_handoffs.py services/calliope/floor_presentations.py services/calliope/calliope_dreams.py services/calliope/calliope_observations.py services/calliope/calliope_decisions.py services/calliope/calliope_inputs.py services/calliope/calliope_rooms.py services/calliope/calliope_fork.py services/calliope/calliope_access.py services/calliope/application_teams.py services/calliope/artifact_access.py services/calliope/playbook_access.py services/calliope/playbook_home.py services/calliope/procedure_access.py services/calliope/workbench_access.py services/calliope/business_topology_jobs.py services/calliope/specialist_cron_reconcile.py services/calliope/specialist_followups.py services/calliope/hermes_docs.py services/calliope/hermes_config.py services/calliope/hermes_profiles.py services/calliope/hermes_skills.py services/calliope/provider_settings.py services/calliope/speech_settings.py services/calliope/hermes_mirror.py services/calliope/kanban_mirror.py services/calliope/domain_agent_factory.py services/calliope/briarpatch.py services/calliope/work_geography.py services/calliope/excalifont_metrics.json services/calliope/superior_font_metrics.json services/calliope/expert_portraits.py services/calliope/visual_intent.py services/calliope/stage_charts.py services/calliope/stage_answers.py services/calliope/stage_maps.py services/calliope/warehouse_theme.py services/calliope/dashboard_template.html services/calliope/dashboard_hosted_template.html services/calliope/dashboard_mode.py ./
COPY crates/pg_rvbbit/sql/migrations/0312_hermes_docs_store.sql ./sql/0312_hermes_docs_store.sql
COPY crates/pg_rvbbit/sql/migrations/0313_hermes_config_history.sql ./sql/0313_hermes_config_history.sql
COPY crates/pg_rvbbit/sql/migrations/0314_hermes_skill_capabilities.sql ./sql/0314_hermes_skill_capabilities.sql
COPY crates/pg_rvbbit/sql/migrations/0321_calliope_provider_control.sql ./sql/0321_calliope_provider_control.sql
COPY crates/pg_rvbbit/sql/migrations/0324_calliope_provider_state_hermes_holder.sql ./sql/0324_calliope_provider_state_hermes_holder.sql
COPY crates/pg_rvbbit/sql/migrations/0334_hermes_session_mirror.sql ./sql/0334_hermes_session_mirror.sql
COPY crates/pg_rvbbit/sql/migrations/0335_calliope_run_scorecard.sql ./sql/0335_calliope_run_scorecard.sql
COPY crates/pg_rvbbit/sql/migrations/0336_calliope_agent_messages.sql ./sql/0336_calliope_agent_messages.sql
# 0337 ships from the service until it joins the extension chain (the kanban
# mirror's ensure() applies it idempotently either way).
COPY services/calliope/sql/0337_hermes_kanban_mirror.sql ./sql/0337_hermes_kanban_mirror.sql
COPY docker/calliope/hermes-docs/source.json ./hermes-docs/source.json
COPY docker/calliope/hermes-docs/hermes-docs.bundle.json.gz ./hermes-docs/hermes-docs.bundle.json.gz
COPY services/calliope/backgrounds ./backgrounds
COPY services/calliope/calliope ./calliope
COPY services/calliope/system-dashboards ./system-dashboards
COPY services/calliope/factory_playbooks ./factory_playbooks
COPY services/calliope/theme ./theme
COPY services/calliope/media_ledger.py services/calliope/cost_accounting.py services/calliope/cost_accounting.sql ./
COPY services/calliope/data_explorer.py services/calliope/data_explorer.sql services/calliope/data_areas.py services/calliope/cube_shelf.py ./
COPY services/calliope/charts ./charts
# Dashboard runtime libraries (Vega/Vega-Lite/vega-embed, Mapbox GL, Grid.js),
# pinned by services/calliope/vendor/manifest.json and fetched at build time with
# sha256 verification. Served at /vendor/<lib>@<version>/<file>, immutable.
COPY services/calliope/vendor/manifest.json ./vendor/manifest.json
COPY scripts/vendor-dashboard-assets.py ./vendor-dashboard-assets.py
RUN python vendor-dashboard-assets.py --vendor-dir ./vendor && rm -f vendor-dashboard-assets.py
COPY services/calliope/examples ./examples
COPY bench/business_topology ./business_topology
# Fail the image build if a Computer module or one of its imports was omitted.
RUN python -c "import desktop_routes, desktop_evidence, desktop_storage, desktop_terminal, desktop_replay"
COPY services/calliope/app_revisions.py services/calliope/ui_apps.py services/calliope/ui_app_files.py services/calliope/ui_app_context.py services/calliope/ui_records.py ./
COPY services/calliope/app_review.py services/calliope/app_review.sql services/calliope/app_validation.py ./
COPY services/calliope/meaning_graph.py services/calliope/meaning_web.py ./
COPY services/calliope/account_management*.py services/calliope/account_management.sql ./
COPY services/calliope/business_logic*.py services/calliope/business_logic*.sql ./
COPY services/calliope/ui_charts.py services/calliope/ui_chart_render.py services/calliope/ui_chart_worker.py ./
COPY services/calliope/ui_composition.py ./
# Read-only route miner: python memory_routes.py --session <hermes session> | --since <time>
COPY services/calliope/memory_routes.py ./
# What people tell Callie, quoted: python memory_claims.py --tick | --turn <id> | --eval <cases>
COPY services/calliope/memory_claims.py ./
COPY services/calliope/ui_app_preview.py services/calliope/ui_app_captures.py ./
COPY services/calliope/artifact_progress.py services/calliope/artifact_work.py services/calliope/artifact_render.py services/calliope/artifact_document.py services/calliope/artifact_app_preview.py ./
COPY --from=ui-app-web /build/services/calliope/ui_app_assets ./ui_app_assets
COPY apps/calliope-canvas/business/catalog.json ./ui_app_assets/catalog.json
COPY services/calliope/demo_mode.py ./
# Validate the complete application import graph after every module is copied.
# This does not start workers or require a database connection.
RUN python -c "import server, calliope, demo_mode, ui_apps, ui_decisions, meaning_data, native_handoff, stage_capture, stage_references, correspondence, correspondence_routes, sms, sms_simulator, ops_work, file_imports, voice_tagging, take_parts, playbook_home, stage_views"
EXPOSE 8765
# Calliope web application and streamable-HTTP MCP service.
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('WAREHOUSE_MCP_PORT','8765')+'/health').read()" || exit 1
LABEL org.opencontainers.image.title="Calliope Server" \
      org.opencontainers.image.description="Hosted Calliope application, MCP surface, setup flow, and workers"
CMD ["python", "server.py", "--http"]
