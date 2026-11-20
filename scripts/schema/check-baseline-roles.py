#!/usr/bin/env python3
"""Check demo roles and database privileges on a database installed from the baseline.

Uses psql with the usual PG* environment. scripts/schema/test-image.py runs the
same check against the real image on an empty volume.
"""
import json
import subprocess
import sys

ROLE = """(SELECT json_build_object('login',rolcanlogin,'inherit',rolinherit,'super',rolsuper,
  'createdb',rolcreatedb,'createrole',rolcreaterole,'replication',rolreplication,'bypassrls',rolbypassrls,
  'password',rolpassword IS NOT NULL,'config',(SELECT rolconfig FROM pg_roles r WHERE r.oid=a.oid))
  FROM pg_authid a WHERE rolname={name})"""

QUERY = ("SELECT json_build_object('guest'," + ROLE.format(name="'calliope_demo_guest'")
         + ",'login'," + ROLE.format(name="'calliope_demo_login'") + """,
  'login_inherits_guest',pg_has_role('calliope_demo_login','calliope_demo_guest','USAGE'),
  'login_member_of_postgres',pg_has_role('calliope_demo_login','postgres','MEMBER'),
  'public_temp',has_database_privilege('public',current_database(),'TEMP'),
  'login_temp',has_database_privilege('calliope_demo_login',current_database(),'TEMP'),
  'login_connect',has_database_privilege('calliope_demo_login',current_database(),'CONNECT'))""")

CLOSED = {'super': False, 'createdb': False, 'createrole': False, 'replication': False,
          'bypassrls': False, 'password': False}
EXPECTED = {
    'guest': {**CLOSED, 'login': False, 'inherit': False, 'config': None},
    'login': {**CLOSED, 'login': True, 'inherit': True, 'config': ['default_transaction_read_only=on']},
    'login_inherits_guest': True,
    'login_member_of_postgres': False,
    'public_temp': False,
    'login_temp': False,
    'login_connect': True,
}


def verify(doc):
    problems = [f'{key}: expected {EXPECTED[key]!r}, found {doc.get(key)!r}'
                for key in EXPECTED if doc.get(key) != EXPECTED[key]]
    if problems:
        raise ValueError('Baseline demo roles or database privileges are wrong:\n' + '\n'.join(problems))
    return doc


def main():
    out = subprocess.run(['psql', '-X', '-Atq', '-v', 'ON_ERROR_STOP=1'], input=QUERY, text=True,
                         capture_output=True)
    if out.returncode:
        raise SystemExit(out.stderr[-4000:])
    try:
        print(json.dumps(verify(json.loads(out.stdout))))
    except ValueError as error:
        raise SystemExit(str(error))


if __name__ == '__main__':
    sys.exit(main())
