"""Shipped playbooks must not claim or publish client-owned capabilities."""
from contextlib import nullcontext

import server


def test_factory_seed_leaves_same_named_client_capability_untouched(monkeypatch):
    statements = []

    class Conn:
        def transaction(self):
            return nullcontext()

        def execute(self, sql, params):
            statements.append(sql)
            assert sql.startswith("SELECT owner_email")
            return self

        def fetchone(self):
            return {"owner_email": "client@example.com"}

    monkeypatch.setattr(server, "_conn", lambda: nullcontext(Conn()))
    assert server._ensure_factory_playbooks() == 0
    assert statements
    assert all(sql.startswith("SELECT") for sql in statements)


def test_shipped_catalog_has_four_valid_methods():
    import json
    from pathlib import Path
    import playbook_access

    seeds = [json.loads(p.read_text()) for p in (Path(server.__file__).parent / 'factory_playbooks').glob('*.json')]
    assert {s['title'] for s in seeds} == {'Answers', 'Decision', 'What Changed', 'Dashboards'}
    assert len({s['name'] for s in seeds}) == 4
    for seed in seeds:
        assert seed['readiness'] == 'ready'
        assert playbook_access.normalize_contract(seed['contract']) == seed['contract']
        assert seed['name'] not in server.RETIRED_FACTORY_PLAYBOOKS


def test_retirement_uses_normal_audited_archive_path(monkeypatch):
    calls = []

    class Conn:
        def execute(self, sql, params):
            assert 'AND NOT p.archived' in sql
            assert 'p.owner_email=%s AND a.owner_email=%s' in sql
            assert params == (list(server.RETIRED_FACTORY_PLAYBOOKS), server.FACTORY_PLAYBOOK_OWNER, server.FACTORY_PLAYBOOK_OWNER)
            return self

        def fetchall(self):
            return [{'id': 'old-factory-id', 'access_revision': 7}]

    def archive(conn_factory, authorization, ref, revision, archived):
        calls.append((authorization, ref, revision, archived))
        return {'changed': True}

    monkeypatch.setattr(server, '_conn', lambda: nullcontext(Conn()))
    monkeypatch.setattr(server.playbook_access, 'set_archived', archive)
    assert server._retire_factory_playbooks() == 1
    assert calls == [({'subject': server.FACTORY_PLAYBOOK_OWNER, 'actor': server.FACTORY_PLAYBOOK_APPROVER,
                      'mode': 'factory', 'platform': 'factory-seed'}, 'old-factory-id', 7, True)]


# Exercise the real boot hook against PostgreSQL, including repeated deployments.
from test_workspace_sql import db  # noqa: E402,F401


def test_factory_boot_seeds_once_and_preserves_client_forks(db, monkeypatch):
    with db() as conn:
        conn.execute('''
          CREATE TABLE rvbbit.capability_access_policies (
            capability_kind text, capability_name text, owner_email text,
            visibility text, created_by text, updated_by text,
            PRIMARY KEY(capability_kind,capability_name));
          CREATE TABLE rvbbit.calliope_playbooks (
            id uuid PRIMARY KEY, capability_name text UNIQUE, owner_email text,
            latest_version int, approved_version int, approved_at timestamptz,
            approved_by text, updated_at timestamptz DEFAULT now(),
            archived boolean DEFAULT false, access_revision int DEFAULT 1);
          CREATE TABLE rvbbit.calliope_playbook_versions (
            id uuid PRIMARY KEY, playbook_id uuid, version int, title text,
            synopsis text, readiness text, semantic_contract jsonb,
            contract_hash text, created_by text, UNIQUE(playbook_id,version));
          CREATE TABLE rvbbit.application_principals (
            email text PRIMARY KEY,display_name text,login_allowed boolean,observation_count int);
        ''')
    monkeypatch.setattr(server, '_conn', db)
    assert server._ensure_factory_playbooks() == 4
    assert server._ensure_factory_playbooks() == 0
    with db() as conn:
        rows = conn.execute('SELECT p.capability_name,p.owner_email,p.latest_version,p.approved_version,a.visibility '
                            'FROM rvbbit.calliope_playbooks p JOIN rvbbit.capability_access_policies a '
                            'USING(capability_name)').fetchall()
        assert len(rows) == 4
        assert all(r['owner_email'] == server.FACTORY_PLAYBOOK_OWNER and r['visibility'] == 'everyone'
                   and r['latest_version'] == r['approved_version'] == 1 for r in rows)
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_playbook_versions').fetchone()['n'] == 4
        # A client-owned copy with the retired name must never be archived.
        conn.execute("INSERT INTO rvbbit.calliope_playbooks(id,capability_name,owner_email) VALUES(gen_random_uuid(),%s,'client@example.com')",
                     (server.RETIRED_FACTORY_PLAYBOOKS[0],))
        conn.execute("INSERT INTO rvbbit.capability_access_policies VALUES('cap_playbook',%s,'client@example.com','private','client','client')",
                     (server.RETIRED_FACTORY_PLAYBOOKS[0],))
    assert server._ensure_factory_playbooks() == 0
    with db() as conn:
        assert conn.execute('SELECT archived FROM rvbbit.calliope_playbooks WHERE capability_name=%s',
                            (server.RETIRED_FACTORY_PLAYBOOKS[0],)).fetchone()['archived'] is False
