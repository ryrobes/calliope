#!/usr/bin/env python3
"""Publish the regression apps through the existing Calliope artifact tools.

Run inside the server environment, with these JSON files adjacent. Example:
  python publish.py --actor user@example.com --install-fixtures
Fixture setup is explicit, keeps existing records, and never runs on app startup.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, '/app')
import server
import auth
import workspace_sql
from pglast.parser import split

PAGES = {'dossier':'Transaction dossier', 'pipeline':'Transaction pipeline',
         'briefing':'Morning briefing', 'hvac':'Service operations', 'counter':'Parts desk',
         'receiving':'Receiving', 'dispatch':'Dispatch', 'field':'Field notebook',
         'adaptive':'Adaptive workspace', 'case':'Shared case', 'charts':'Chart gallery'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actor', required=True)
    parser.add_argument('--install-fixtures', action='store_true')
    args = parser.parse_args()
    token = server._SESSION_SUB.set(args.actor)
    root = Path(__file__).parent
    results = []
    try:
        if args.install_fixtures:
            statements = list(split((root/'setup.sql').read_text()))
            for start in range(0, len(statements), 20):
                plan = workspace_sql.plan(';\n'.join(statements[start:start+20]))
                result = workspace_sql.execute(server._conn, plan, seed=auth.JWT_SECRET,
                                               schema_allowed=server._schema_allowed)
                if result.get('error'): raise RuntimeError(result['error'])
        manifest = json.loads((root/'manifest.json').read_text())
        for file, title in PAGES.items():
            name = 'Demo '+title
            slug = server._slugify(name)
            doc = json.loads((root/'workspace.json').read_text())
            doc['title'] = title
            doc['data']['state']['page'] = file
            if file == 'case': doc['data']['state']['selection'].update(job='HV-CASE-01', po='PO-CASE-01')
            sources = {'app.json': json.dumps(doc, indent=2, ensure_ascii=False)+'\n'}
            with server._conn() as conn:
                prior = conn.execute('SELECT latest_version FROM rvbbit.dashboards WHERE slug=%s', (slug,)).fetchone()
            if prior:
                result = server.tool_update_live_app(slug, source_files=sources, manifest=manifest,
                    expected_version=prior['latest_version'], notes='Postgres demo suite and expanded Vega-Lite gallery')
            else:
                result = server.tool_create_live_app(name, runtime_kind='a2ui', app_kind='workspace',
                    description='Fictional regression workspace on shared Calliope SQL data.',
                    source_files=sources, manifest=manifest)
            if result.get('error'): raise RuntimeError(result['error'])
            record = {k: result.get(k) for k in ('slug','version','url','hub_url')}
            results.append(record)
            print(json.dumps(record, default=str), flush=True)
        (root/'published.json').write_text(json.dumps(results, indent=2))
    finally:
        server._SESSION_SUB.reset(token)


if __name__ == '__main__': main()
