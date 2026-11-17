"""Small runtime references for authors; no layout, palette or task ownership policy."""
from __future__ import annotations

TOPICS = ('overview', 'maps', 'queries', 'charts', 'theme', 'publication', 'verification')


def reference(topic, *, maps, charts, row_cap):
    if topic not in TOPICS:
        return {'error': {'code': 'INVALID_TOPIC', 'message': 'Choose a runtime reference topic.',
                          'topics': list(TOPICS)}}
    result = {'topic': topic, 'scope': 'Published HTML on this appliance; API reference, not a playbook.',
              'reference_version': 2}
    if topic == 'overview':
        result.update(topics={
            'maps': 'Mapbox creation, GeoJSON layers, click selection and rendered readiness.',
            'queries': 'Read-only warehouse queries, rows, limits and partial failures.',
            'charts': 'Vega-Lite charts, design tokens and selection callbacks.',
            'theme': 'Authored color scheme, design profiles and theme changes.',
            'publication': 'Upload, create/update and inspect exact artifact versions.',
            'verification': 'Capture and persistent desktop interaction checks.'},
            next={'tool': 'artifact_runtime_help', 'arguments': {'topic': 'queries'}},
            context='Hosted pages receive rvbbitQuery, rvbbitMaps, rvbbitCharts and rvbbitTheme. '
                    'Standalone local HTML does not have the hosted data bridge. '
                    'References do not choose a layout or load a design procedure.')
    elif topic == 'maps':
        result.update(maps())
        result['selection_example'] = (
            "// map is the mapboxgl.Map returned by await rvbbitMaps.map(container, opts).\n"
            "// Add sources/layers after style readiness. Keep stable cell IDs in properties.\n"
            "let selectedCell = null;\n"
            "map.on('click', 'cells', event => {\n"
            "  const feature = event.features?.[0]; if (!feature) return;\n"
            "  const id = feature.properties.cell_id;\n"
            "  selectedCell = selectedCell === id ? null : id;\n"
            "  // Update shared filter state, rerun affected queries and render all affected views.\n"
            "});")
        result['readiness'] = [
            'rvbbitMaps.diagnostics() reports sanitized map errors and missing authored layers. '
            'Query readiness alone does not certify a working map. After publication use '
            'browser_page(b, url, expected_version=published_version) before interaction checks.',
            'Wait for style readiness before adding sources/layers. Use map.isStyleLoaded() '
            'and map.once("style.load", ...); reattach custom layers if the style changes.',
            'Data loaded, source exists, and visible pixels rendered are different checks. '
            'For interaction tests wait for the expected layer, map.loaded(), and '
            'map.queryRenderedFeatures(undefined, {layers:["cells"]}).length > 0.',
            'Pick an unobstructed rendered feature, click it and verify the filter changes. '
            'Click again and verify the original values return. An empty slice can legitimately have zero features.']
    elif topic == 'charts':
        result.update(charts())
    elif topic == 'queries':
        result.update(api={
            'await rvbbitQuery(sql, {limit, as_of})': 'Read-only SELECT against the local warehouse mirror. '
                'Returns {columns, rows, row_count, truncated, applied_limit, ...}; rows are arrays.',
            'RVBBIT_DASHBOARD.queryTrace()': 'Recent query results and errors for this document; useful for inspection.'},
            limits={'default_rows': 1000, 'maximum_rows': row_cap},
            example="""const result = await rvbbitQuery(sql, {limit: 2500});
const names = result.columns.map(c => typeof c === 'string' ? c : c.name);
const rows = result.rows.map(row => Array.isArray(row)
  ? Object.fromEntries(names.map((name, i) => [name, row[i]])) : row);
if (result.truncated) { /* disclose the limit or aggregate more narrowly */ }
// For independent panels, use Promise.allSettled and render each success/error separately.
// Keep the shared filter state in your app. Discard stale responses when a newer selection wins.""",
            guidance=['One flat query per data concern; aggregate in SQL before plotting.',
                      'No database credentials or direct production-system connections in the browser.',
                      'Writes use named, parameterized manifest.actions through rvbbitAction, not rvbbitQuery.'])
    elif topic == 'theme':
        result.update(api={'rvbbitTheme.tokens()': 'Authored CSS or user-selected profile tokens, inheriting page typography and text color when absent.',
                           'rvbbitTheme.color(cssOrToken)': 'Resolve an authored/profile token or CSS color to sRGB.',
                           'rvbbitMaps.style(mode?)': 'Appliance map style for the resolved light/dark mode.'},
            guidance=['Only a user-selected design profile supplies profile constraints. With no pin, '
                      'follow the user\'s visual brief; author your own palette, fonts and composition. '
                      'Do not suggest or attach a house style. Runtime helpers impose no font or palette.',
                      'You may define --artifact-* tokens for your own styling when unpinned, or pass '
                      'authored colors/fonts in chart specs and opts.config. Updates preserve these choices.',
                      'Load chosen font assets and verify the rendered font; naming a family in CSS '
                      'does not load it. Keep a usable fallback while fonts load.',
                      'An explicit data-rvbbit-color-mode wins, then an unambiguous computed CSS '
                      'color-scheme, then the browser preference. Declare html {color-scheme: dark} '
                      'when the artifact intentionally requires dark mode.',
                      'Map styles may change with theme. Reattach your data sources and layers on style.load.',
                      'Capture and the desktop should show the same resolved theme even when browser preferences differ.'])
    elif topic == 'publication':
        result.update(tools=[
            {'name': 'upload_artifact', 'purpose': 'Stage an existing local file; returns artifact_id.'},
            {'name': 'create_live_app', 'purpose': 'Publish a new app using source_artifact_id; returns slug/version/url.'},
            {'name': 'update_live_app', 'purpose': 'Revise that slug; produces a new immutable version.'},
            {'name': 'capture_live_app', 'purpose': 'Capture the exact published version and inspect readiness.'},
            {'name': 'debug_live_app', 'purpose': 'Inspect parsed/runtime query dependencies and errors.'}],
            guidance=['Use the exact tool schemas from the connected catalog; these are MCP tools, not SQL functions.',
                      'Keep the returned slug, version and URL as the publication receipt.',
                      'A starter is optional for API lookup: dashboard_template/live_app_template return scaffolding. '
                      'This reference does not load a playbook or prescribe a design.'])
    elif topic == 'verification':
        result.update(checks=[
            'Capture the exact published version, inspect query failures/readiness and review the image.',
            'Open the shared desktop using open_desktop and its browser_auth.origin. '
            'First-party apps authenticate as the task owner.',
            'Use with browser() as b: page = browser_page(b, url). This preserves an open document '
            'across jobs. After publication use browser_page(b, url, expected_version=receipt_version); '
            'it reloads a stale document once and checks the version. reload=True explicitly refreshes any page.',
            'Wait for app-specific data and rendered layers, not networkidle or just a canvas element.',
            'Verify a click changes the displayed data and a second click restores it. '
            'Test the actual map selection and a representative linked chart filter.',
            'Use browser_diagnostics(page) for rendering/connection failures. Finish with finish_desktop.'],
            distinction='A capture validates stored HTML with a server-side data bridge. '
                        'Desktop testing exercises the published URL, browser authentication and actual interactions.')
    return result
