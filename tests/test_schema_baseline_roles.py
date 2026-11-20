"""The flattened schema baseline recreates reviewed roles and database privileges.

Pure unit tests: a fake database answers the catalog queries. The live check
against PostgreSQL runs in CI (scripts/schema/check-baseline-roles.py).
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('schema_baseline', ROOT / 'scripts/schema/baseline.py')
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def role(name, **overrides):
    item = {'name': name, 'login': False, 'inherit': True, 'super': False, 'createdb': False,
            'createrole': False, 'replication': False, 'bypassrls': False, 'password': False,
            'config': None, 'comment': None}
    item.update(overrides)
    return item


DEMO_ROLES = [
    role('calliope_demo_guest', inherit=False, comment='guest'),
    role('calliope_demo_login', login=True, config=['default_transaction_read_only=on'], comment="it's a login"),
    role('rvbbit_guest'),
]
DEMO_MEMBERS = [
    {'role': 'calliope_demo_guest', 'member': 'calliope_demo_login', 'admin': False, 'inherit': True, 'set': True},
    {'role': 'calliope_demo_guest', 'member': 'postgres', 'admin': False, 'inherit': True, 'set': True},
    {'role': 'rvbbit_guest', 'member': 'postgres', 'admin': True, 'inherit': True, 'set': True},
]


class FakeDb:
    def __init__(self, roles, members=(), acl=None, owner='postgres'):
        self.roles, self.members, self.acl, self.owner = roles, list(members), acl, owner
        self.queries = []

    def json(self, db, query):
        self.queries.append(query)
        if 'FROM pg_authid' in query:
            return self.roles
        if 'FROM pg_auth_members' in query:
            return self.members
        if 'FROM pg_database' in query:
            return {'owner': self.owner, 'acl': self.acl}
        raise AssertionError(query)


def test_demo_roles_are_recreated_with_reviewed_attributes():
    sql = baseline.role_seed(FakeDb(DEMO_ROLES, DEMO_MEMBERS), 'source')
    assert 'CREATE ROLE "calliope_demo_guest" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;' in sql
    assert 'CREATE ROLE "calliope_demo_login" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;' in sql
    assert 'ALTER ROLE "calliope_demo_login" SET default_transaction_read_only TO \'on\';' in sql
    assert "COMMENT ON ROLE \"calliope_demo_login\" IS 'it''s a login';" in sql
    assert 'GRANT "calliope_demo_guest" TO "calliope_demo_login" WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;' in sql
    assert 'GRANT "calliope_demo_guest" TO "postgres" WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;' in sql
    # rvbbit roles keep their original statements.
    assert 'CREATE ROLE "rvbbit_guest" NOLOGIN; END IF;' in sql
    assert 'GRANT "rvbbit_guest" TO "postgres" WITH ADMIN OPTION;' in sql
    # Roles exist before any membership refers to them.
    assert sql.index('CREATE ROLE "calliope_demo_login"') < sql.index('GRANT "calliope_demo_guest" TO "calliope_demo_login"')
    assert 'PASSWORD' not in sql.upper()


def test_role_query_never_reads_password_hashes():
    db = FakeDb(DEMO_ROLES, DEMO_MEMBERS)
    baseline.role_seed(db, 'source')
    role_query = next(q for q in db.queries if 'FROM pg_authid' in q)
    assert re.search(r"'password',rolpassword IS NOT NULL", role_query)
    member_query = next(q for q in db.queries if 'FROM pg_auth_members' in q)
    assert "'calliope_demo_guest'" in member_query and "'calliope_demo_login'" in member_query


@pytest.mark.parametrize('bad', [
    role('someone_else'),
    role('calliope_demo_other'),
    role('rvbbit_admin', login=True),
    role('rvbbit_admin', config=['search_path=public']),
    role('calliope_demo_guest', inherit=False, super=True),
    role('calliope_demo_guest', inherit=False, login=True),
    role('calliope_demo_login', login=True, config=['default_transaction_read_only=on'], bypassrls=True),
    role('calliope_demo_login', login=True, config=None),
    role('calliope_demo_login', login=True, config=['default_transaction_read_only=on', 'role=postgres']),
])
def test_unknown_or_changed_roles_are_rejected(bad):
    with pytest.raises(ValueError):
        baseline.role_seed(FakeDb([bad]), 'source')


def test_a_seed_role_password_is_never_baked():
    login = role('calliope_demo_login', login=True, config=['default_transaction_read_only=on'], password=True)
    with pytest.raises(ValueError, match='password'):
        baseline.role_seed(FakeDb([login]), 'source')


def test_database_privileges_follow_the_target_database_and_owner():
    acl = [{'grantee': None, 'privilege': 'CONNECT', 'grantable': False},
           {'grantee': 'postgres', 'privilege': 'CONNECT', 'grantable': False},
           {'grantee': 'postgres', 'privilege': 'CREATE', 'grantable': False},
           {'grantee': 'postgres', 'privilege': 'TEMPORARY', 'grantable': False}]
    db = FakeDb([], acl=acl)
    assert baseline.database_acl(db, 'source')[1]['grantee'] == 'OWNER'
    sql = baseline.database_acl_seed(db, 'source')
    assert "REVOKE ALL ON DATABASE %I FROM PUBLIC', db);" in sql
    assert "GRANT CONNECT ON DATABASE %I TO PUBLIC', db);" in sql
    assert "GRANT TEMPORARY ON DATABASE %I TO %I', db, owner);" in sql
    assert 'GRANT TEMPORARY ON DATABASE %I TO PUBLIC' not in sql
    assert 'calliope_baseline' not in sql


def test_default_database_privileges_emit_nothing():
    assert baseline.database_acl_seed(FakeDb([], acl=None), 'source') == ''


def test_installer_passthrough_names_each_setting_once():
    text = (ROOT / 'deploy/install.sh').read_text()
    body = re.search(r'local passthrough=\(([^)]*)\)', text).group(1)
    names = body.split()
    assert len(names) == len(set(names)), names
