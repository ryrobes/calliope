#!/usr/bin/env python3
"""Build and verify the flattened fresh-install schema using a disposable cluster.

Runs inside the Postgres image. The stock extension installer and migrations stay
available for upgrades and for comparison; no application database is inspected.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile


def command(args, *, data=None, env=None):
    proc = subprocess.run(args, input=data, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, check=False)
    if proc.returncode:
        raise RuntimeError('Command failed: ' + ' '.join(args) + '\n' + proc.stderr[-12000:])
    return proc.stdout


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


MEMBERS = """SELECT coalesce(json_agg(json_build_object('type',i.type,'identity',i.identity)
ORDER BY i.type,i.identity),'[]') FROM pg_depend d
CROSS JOIN LATERAL pg_identify_object(d.classid,d.objid,d.objsubid) i
WHERE d.refclassid='pg_extension'::regclass
AND d.refobjid=(SELECT oid FROM pg_extension WHERE extname='pg_rvbbit') AND d.deptype='e'"""

DETACH = """SET session_replication_role=replica;
DO $detach$ DECLARE r record; BEGIN
  LOOP
    SELECT i.* INTO r FROM pg_depend d
      CROSS JOIN LATERAL pg_identify_object(d.classid,d.objid,d.objsubid) i
    WHERE d.refclassid='pg_extension'::regclass
      AND d.refobjid=(SELECT oid FROM pg_extension WHERE extname='pg_rvbbit') AND d.deptype='e'
    ORDER BY CASE WHEN i.type IN ('table','view','sequence','materialized view') THEN 0 ELSE 1 END,
      i.type,i.identity LIMIT 1;
    EXIT WHEN NOT FOUND;
    EXECUTE format('ALTER EXTENSION pg_rvbbit DROP %s %s',r.type,r.identity);
  END LOOP;
END $detach$;"""


def attach_sql(members):
    # Adding a relation also adds its row/array types. Test membership rather
    # than swallowing errors: every other failure must abort the installation.
    return """DO $attach$ DECLARE r record; BEGIN
FOR r IN SELECT * FROM jsonb_to_recordset(""" + literal(json.dumps(members)) + """::jsonb)
  AS members(type text,identity text)
  ORDER BY CASE WHEN type IN ('table','view','sequence','materialized view') THEN 0 ELSE 1 END,type,identity
LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_depend d
    CROSS JOIN LATERAL pg_identify_object(d.classid,d.objid,d.objsubid) i
    WHERE d.refclassid='pg_extension'::regclass
      AND d.refobjid=(SELECT oid FROM pg_extension WHERE extname='pg_rvbbit') AND d.deptype='e'
      AND i.type=r.type AND i.identity=r.identity)
  THEN EXECUTE format('ALTER EXTENSION pg_rvbbit ADD %s %s',r.type,r.identity); END IF;
END LOOP;
END $attach$;
"""


class Database:
    def __init__(self, env):
        self.env = {**env, 'PGOPTIONS': '-c rvbbit.force_heap_scan=on'}

    def sql(self, db, statement):
        return command(['psql', '-X', '-Atq', '-v', 'ON_ERROR_STOP=1', '-d', db], data=statement, env=self.env).strip()

    def json(self, db, query):
        return json.loads(self.sql(db, query))

    def schema(self, db):
        text = command(['pg_dump', '-d', db, '--schema-only'], env=self.env)
        # pg_dump generates a new psql restriction nonce on every invocation.
        return '\n'.join(line for line in text.splitlines()
                         if not line.startswith(('\\restrict ', '\\unrestrict ', '-- Dumped ')))

    def data(self, db):
        tables = self.json(db, """SELECT coalesce(json_agg(format('%I.%I',n.nspname,c.relname)
        ORDER BY n.nspname,c.relname),'[]') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema')
        AND n.nspname NOT LIKE 'pg_toast%'""")
        result = {}
        for table in tables:
            rows = self.json(db, 'SELECT coalesce(json_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\') FROM ' + table + ' t')
            if rows:
                result[table] = rows
        return result

    def oid_fixups(self, db):
        fields = self.json(db, """SELECT coalesce(json_agg(json_build_object('table',format('%I.%I',n.nspname,c.relname),
        'column',a.attname,'type',a.atttypid::regtype::text) ORDER BY n.nspname,c.relname,a.attnum),'[]')
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind='r' AND n.nspname='rvbbit' AND a.attnum>0 AND NOT a.attisdropped
        AND a.atttypid IN ('oid'::regtype,'oid[]'::regtype,'regclass'::regtype,'regclass[]'::regtype)""")
        statements, mappings = [], []
        for field in fields:
            table, column, kind = field['table'], field['column'], field['type']
            values = self.json(db, f"SELECT coalesce(json_agg(v),'[]') FROM (SELECT DISTINCT {ident(column)} AS v FROM {table} WHERE {ident(column)} IS NOT NULL) t")
            if not values:
                continue
            if kind != 'oid':
                # regclass COPY output is symbolic already. Arrays of raw OIDs
                # need an explicit, reviewed mapping before a new baseline ships.
                if kind.startswith('regclass'):
                    continue
                raise ValueError(f'Unmapped populated OID array: {table}.{column}')
            for value in values:
                if value == 0:
                    continue
                if column == 'created_by':
                    regtype = 'regrole'
                elif column in ('table_oid', 'reloid', 'catcher_oid', 'target_oid', 'relation_oid', 'destination_oid'):
                    regtype = 'regclass'
                else:
                    raise ValueError(f'Unmapped populated OID field: {table}.{column}')
                name = self.sql(db, f'SELECT {int(value)}::oid::{regtype}::text')
                if name == str(value):
                    raise ValueError(f'Dangling catalog identity: {table}.{column}')
                statements.append(f'UPDATE {table} SET {ident(column)}={literal(name)}::{regtype}::oid WHERE {ident(column)}={int(value)};')
                mappings.append({**field, 'old': value, 'identity': name, 'regtype': regtype})
        return '\n'.join(statements), mappings


def canonical_schema(text):
    # PostgreSQL's deparser adds inferred aliases to cast literals and flattens
    # nested associative CHECK expressions after a restore. Compare parsed SQL,
    # preserving every other definition, owner, grant, literal and expression.
    from pglast import parser
    def normalize(node):
        if isinstance(node, list):
            return [normalize(value) for value in node]
        if not isinstance(node, dict):
            return node
        node = {key: normalize(value) for key, value in node.items()
                if key not in ('location', 'stmt_location', 'stmt_len')}
        if 'BoolExpr' in node:
            expr = node['BoolExpr']
            op, args = expr.get('boolop'), []
            for arg in expr.get('args', []):
                if op in ('AND_EXPR', 'OR_EXPR') and arg.get('BoolExpr', {}).get('boolop') == op:
                    args.extend(arg['BoolExpr']['args'])
                else:
                    args.append(arg)
            expr['args'] = args
        if 'ResTarget' in node:
            target = node['ResTarget']
            cast = target.get('val', {}).get('TypeCast', {})
            names = cast.get('typeName', {}).get('names', [])
            if ('A_Const' in cast.get('arg', {}) and names
                    and target.get('name') == names[-1].get('String', {}).get('sval')):
                target.pop('name', None)
        return node
    return normalize(json.loads(parser.parse_sql_json(text)))


def normalized_data(data, mappings):
    data = json.loads(json.dumps(data))
    for item in mappings:
        for row in data.get(item['table'], []):
            # Normalize any source/target OID to a semantic object identity.
            if row.get(item['column']) == item['old']:
                row[item['column']] = item['identity']
    return {k: sorted(v, key=lambda r: json.dumps(r, sort_keys=True)) for k, v in data.items()}


def role_seed(db, source):
    roles = db.json(source, """SELECT coalesce(json_agg(json_build_object('name',rolname,'login',rolcanlogin,
    'super',rolsuper,'createdb',rolcreatedb,'createrole',rolcreaterole,'replication',rolreplication,
    'bypassrls',rolbypassrls,'password',rolpassword,'config',(SELECT rolconfig FROM pg_roles WHERE oid=pg_authid.oid)) ORDER BY rolname),'[]')
    FROM pg_authid WHERE rolname<>'postgres' AND rolname NOT LIKE 'pg_%'""")
    commands = []
    for role in roles:
        if (not role['name'].startswith('rvbbit_') or any(role[k] for k in
                ('login', 'super', 'createdb', 'createrole', 'replication', 'bypassrls', 'password', 'config'))):
            raise ValueError('Unexpected non-seed role in the disposable baseline database')
        name = role['name']
        commands.append(f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname={literal(name)}) THEN CREATE ROLE {ident(name)} NOLOGIN; END IF; END $$;")
    memberships = db.json(source, """SELECT coalesce(json_agg(json_build_object('role',r.rolname,'member',m.rolname,
      'admin',am.admin_option) ORDER BY r.rolname,m.rolname),'[]') FROM pg_auth_members am
      JOIN pg_roles r ON r.oid=am.roleid JOIN pg_roles m ON m.oid=am.member
      WHERE r.rolname LIKE 'rvbbit_%'""")
    for item in memberships:
        commands.append(f"GRANT {ident(item['role'])} TO {ident(item['member'])}" + (' WITH ADMIN OPTION;' if item['admin'] else ';'))
    return '\n'.join(commands)


def generate(db, out, lock, engine, extdir):
    out.mkdir(parents=True, exist_ok=True)
    target = list(lock['migrations'])[-1]
    shell_version = engine + '-calliope-' + target.split('_')[0]
    # This is a normal Postgres extension version edge. A baseline starts with
    # an empty extension shell, restores the flattened schema, reattaches its
    # exact original members, then advances to the ordinary engine version.
    (extdir / f'pg_rvbbit--{shell_version}.sql').write_text('-- Objects are installed atomically by the Calliope schema baseline.\n')
    (extdir / f'pg_rvbbit--{shell_version}--{engine}.sql').write_text('-- Flattened schema installed; normal migration tracking resumes.\n')
    source, restored = 'calliope_baseline_history', 'calliope_baseline_restored'
    db.sql('postgres', f'CREATE DATABASE {source}; CREATE DATABASE {restored};')
    db.sql(source, 'CREATE EXTENSION pg_rvbbit; SELECT rvbbit.migrate();')
    installed = db.json(source, "SELECT json_agg(name ORDER BY name) FROM rvbbit.schema_migrations")
    if installed != list(lock['migrations']):
        raise ValueError('Compiled migrations do not match the locked release history')
    print('baseline: verified clean migration history (' + str(len(installed)) + ')', flush=True)
    members = db.json(source, MEMBERS)
    reattach = attach_sql(members)
    roles = role_seed(db, source)
    fixes, mappings = db.oid_fixups(source)
    source_data = db.data(source)
    db.sql(source, DETACH)
    source_schema = db.schema(source)
    archive = out / 'schema.dump'
    command(['pg_dump', '-d', source, '-Fc', '-f', str(archive)], env=db.env)
    toc = command(['pg_restore', '-l', str(archive)], env=db.env)
    keep = [line for line in toc.splitlines() if line and not line.startswith(';') and not any(
        excluded in line for excluded in (' EXTENSION - pg_rvbbit', ' COMMENT - EXTENSION pg_rvbbit',
                                         ' SCHEMA - rvbbit', ' COMMENT - SCHEMA rvbbit'))]
    listing = out / 'schema.list'
    listing.write_text('\n'.join(keep) + '\n')
    body = command(['pg_restore', '-L', str(listing), '-f', '-', str(archive)], env=db.env)
    body = '\n'.join(line for line in body.splitlines() if not line.startswith(('\\restrict ', '\\unrestrict ')))
    script = ('-- Generated from a clean database, never from a running instance.\n'
              'SET LOCAL rvbbit.force_heap_scan=on;\nSET LOCAL session_replication_role=replica;\n' + roles + '\n'
              f'CREATE EXTENSION pg_rvbbit VERSION {literal(shell_version)};\n' + body + '\n' + fixes + '\n'
              + reattach + f'ALTER EXTENSION pg_rvbbit UPDATE TO {literal(engine)};\n')
    (out / 'baseline.sql').write_text(script)
    print('baseline: restoring flattened DDL and seed data', flush=True)
    db.sql(restored, 'BEGIN;\n' + script + '\nCOMMIT;')
    actual_members = db.json(restored, MEMBERS)
    if actual_members != members:
        raise ValueError('Baseline changed extension membership')
    # Compare complete schema with extension objects visible on both sides.
    db.sql(restored, DETACH)
    actual_schema = db.schema(restored)
    if canonical_schema(actual_schema) != canonical_schema(source_schema):
        (out / 'expected-schema.sql').write_text(source_schema)
        (out / 'actual-schema.sql').write_text(actual_schema)
        raise ValueError('Baseline schema/ACL definitions differ; inspect expected/actual schema files')
    print('baseline: schema, grants and membership match', flush=True)
    _, restored_mappings = db.oid_fixups(restored)
    actual_data = db.data(restored)
    if normalized_data(source_data, mappings) != normalized_data(actual_data, restored_mappings):
        (out / 'expected-data.json').write_text(json.dumps(normalized_data(source_data, mappings), indent=2, sort_keys=True))
        (out / 'actual-data.json').write_text(json.dumps(normalized_data(actual_data, restored_mappings), indent=2, sort_keys=True))
        raise ValueError('Baseline seed data differs; inspect expected/actual data files')
    print('baseline: seed data matches; testing incremental upgrades', flush=True)
    for name in (source, restored):
        db.sql(name, 'SET session_replication_role=replica;\n' + reattach)
        before = db.json(name, "SELECT json_agg(name ORDER BY name) FROM rvbbit.schema_migrations")
        result = db.sql(name, 'SELECT rvbbit.migrate();')
        if 'up to date' not in result or before != db.json(name, "SELECT json_agg(name ORDER BY name) FROM rvbbit.schema_migrations"):
            raise ValueError('Baseline replayed an already-covered migration')
        timestamps = db.json(name, "SELECT json_object_agg(name,applied_at::text) FROM rvbbit.schema_migrations")
        db.sql(name, 'DELETE FROM rvbbit.schema_migrations WHERE name=' + literal(target))
        pending = db.sql(name, 'SELECT rvbbit.migrate();')
        after = db.json(name, "SELECT json_object_agg(name,applied_at::text) FROM rvbbit.schema_migrations")
        if ('applied 1 of ' not in pending or any(after.get(k) != v for k, v in timestamps.items() if k != target)):
            raise ValueError('Incremental migration replay did not run exactly the pending migration')
        # A forward DDL/seed change must behave the same on both databases.
        db.sql(name, """BEGIN;
            CREATE TABLE rvbbit.baseline_forward_probe(id bigint PRIMARY KEY,value text NOT NULL);
            INSERT INTO rvbbit.baseline_forward_probe VALUES(1,'forward migration');
            ALTER TABLE rvbbit.baseline_forward_probe ADD COLUMN active boolean NOT NULL DEFAULT true;
            INSERT INTO rvbbit.schema_migrations(name) VALUES('9999_baseline_forward_probe');
            COMMIT;""")
        if db.sql(name, 'SELECT value FROM rvbbit.baseline_forward_probe WHERE active') != 'forward migration':
            raise ValueError('Forward migration verification failed')
        db.sql(name, DETACH)
    if canonical_schema(db.schema(source)) != canonical_schema(db.schema(restored)):
        raise ValueError('Baseline and history diverge after the forward DDL test')
    manifest = {'format': 1, 'id': shell_version, 'engine_version': engine,
                'migration_target': target, 'migrations': lock['migrations'],
                'baseline_sha256': digest(out / 'baseline.sql'),
                'extension_install_sha256': digest(extdir / f'pg_rvbbit--{engine}.sql'),
                'extension_library_sha256': digest(Path(command(['pg_config', '--pkglibdir']).strip()) / 'pg_rvbbit.so'),
                'extension_member_count': len(members), 'seed_tables': sorted(source_data),
                'verification': {'schema_and_grants': True, 'seed_data': True,
                                 'extension_membership': True, 'migration_noop': True, 'pending_migration_only': True, 'forward_ddl': True}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for path in (archive, listing):
        path.unlink()
    print(json.dumps({k: manifest[k] for k in ('id', 'migration_target', 'extension_member_count', 'verification')}), flush=True)


def build(args):
    # Temporary cluster exists only inside this image build. Disable fsync and
    # TCP listening; it cannot see customer volumes, credentials or services.
    pg = pwd.getpwnam('postgres')
    with tempfile.TemporaryDirectory(prefix='calliope-schema-build-') as tmp:
        directory = Path(tmp)
        directory.chmod(0o700)
        os.chown(directory, pg.pw_uid, pg.pw_gid)
        data, socket = directory / 'data', directory / 'socket'
        socket.mkdir(); os.chown(socket, pg.pw_uid, pg.pw_gid)
        pgbin = Path(command(['pg_config', '--bindir']).strip())
        command(['gosu', 'postgres', str(pgbin / 'initdb'), '-D', str(data), '-A', 'trust', '--no-instructions'])
        env = {**os.environ, 'PGHOST': str(socket), 'PGUSER': 'postgres', 'PGDATABASE': 'postgres'}
        options = f"-k {socket} -c listen_addresses='' -c fsync=off -c full_page_writes=off -c shared_preload_libraries=pg_rvbbit"
        command(['gosu', 'postgres', str(pgbin / 'pg_ctl'), '-D', str(data), '-l', str(directory/'postgres.log'), '-o', options, '-w', 'start'])
        try:
            generate(Database(env), args.output, json.loads(args.lock.read_text()), args.engine, args.extdir)
        finally:
            command(['gosu', 'postgres', str(pgbin / 'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lock', type=Path, required=True)
    parser.add_argument('--engine', required=True)
    parser.add_argument('--extdir', type=Path, default=Path('/usr/share/postgresql/18/extension'))
    parser.add_argument('--existing-cluster', action='store_true', help='Development only: use a disposable cluster through PGHOST/PGUSER')
    args = parser.parse_args()
    try:
        if args.existing_cluster:
            generate(Database(os.environ), args.output, json.loads(args.lock.read_text()), args.engine, args.extdir)
        else:
            build(args)
    except (ValueError, RuntimeError) as error:
        raise SystemExit(str(error))
