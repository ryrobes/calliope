"""Calliope accounts over the existing people/Teams model.

Passwords are salted scrypt hashes, encrypted with a key outside Postgres.
Google login configuration is separate from grants to Workspace services.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from psycopg.types.json import Jsonb

import application_teams as teams
from app_actions import ActionError


def email(value):
    return teams.normalize_email(value)


def password_valid(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 1024:
        raise ActionError('PASSWORD_LENGTH', 'Use a password of 12 to 1,024 characters.', 400)
    return value


class Accounts:
    def __init__(self, factory, signing_secret):
        self.factory = factory
        self.key = hmac.digest(signing_secret.encode(), b'calliope-accounts-v1', 'sha256')
        self.cipher = Fernet(base64.urlsafe_b64encode(self.key))

    def seal(self, value):
        return self.cipher.encrypt(value.encode()).decode()

    def unseal(self, value):
        return self.cipher.decrypt(value.encode()).decode() if value else ''

    def hash_password(self, value):
        salt = secrets.token_bytes(16)
        material = hmac.digest(self.key, value.encode(), 'sha256')
        digest = Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(material)
        return self.seal('scrypt-v1:' + base64.b64encode(salt + digest).decode())

    def check_password(self, value, stored):
        try:
            version, data = self.unseal(stored).split(':', 1)
            if version != 'scrypt-v1' or not isinstance(value, str) or len(value) > 1024:
                return False
            raw = base64.b64decode(data, validate=True)
            if len(raw) != 48:
                return False
            actual = Scrypt(salt=raw[:16], length=32, n=2**17, r=8, p=1).derive(hmac.digest(self.key, value.encode(), 'sha256'))
            return hmac.compare_digest(actual, raw[16:])
        except (ValueError, TypeError, InvalidToken):
            return False

    def initialize(self, auth):
        """One-time migration; changing an account never reimports an env password."""
        with self.factory() as c, c.transaction():
            c.execute(Path(__file__).with_suffix('.sql').read_text())
            c.execute("SELECT pg_advisory_xact_lock(hashtext('calliope.accounts.initialize'))")
            if c.execute('SELECT 1 FROM rvbbit.application_login_settings').fetchone():
                return
            if not auth.LOGIN_PASSWORD and not (auth.GOOGLE_CLIENT_ID and auth.GOOGLE_CLIENT_SECRET):
                raise ValueError('First-time account setup needs WAREHOUSE_LOGIN_PASSWORD or Google credentials. After initialization, accounts are managed in Calliope.')
            c.execute('''INSERT INTO rvbbit.application_login_settings
                (allowed_emails,google_client_id,google_client_secret,google_domain,google_only)
                VALUES(%s,%s,%s,%s,%s)''', (sorted(auth.ALLOWED_EMAILS), auth.GOOGLE_CLIENT_ID,
                self.seal(auth.GOOGLE_CLIENT_SECRET) if auth.GOOGLE_CLIENT_SECRET else None,
                auth.GOOGLE_HD, auth.GOOGLE_ONLY))
            c.execute('ALTER TABLE rvbbit.application_principals ADD COLUMN IF NOT EXISTS login_allowed boolean NOT NULL DEFAULT false')
            people = {r['email'] for r in c.execute('''SELECT p.email FROM rvbbit.application_principals p
                WHERE p.login_allowed OR EXISTS(SELECT 1 FROM rvbbit.team_members m
                    JOIN rvbbit.teams t ON t.id=m.team_id WHERE t.system_key='admins' AND m.principal_email=p.email)''')}
            people.update(e for e in auth.ALLOWED_EMAILS if not e.startswith('@'))
            for person in sorted(people):
                person = email(person)
                c.execute('INSERT INTO rvbbit.application_principals(email,login_allowed) VALUES(%s,true) ON CONFLICT(email) DO UPDATE SET login_allowed=true', (person,))
                c.execute('INSERT INTO rvbbit.application_accounts(email,password_secret) VALUES(%s,%s) ON CONFLICT DO NOTHING',
                    (person, self.hash_password(auth.LOGIN_PASSWORD) if auth.LOGIN_PASSWORD else None))
            self.event(c, 'server', None, 'accounts_initialized', {'accounts': len(people)})

    def event(self, c, actor, target, action, detail=None):
        c.execute('INSERT INTO rvbbit.application_account_events(actor,email,action,detail) VALUES(%s,%s,%s,%s)',
                  (actor, target, action, Jsonb(detail or {})))

    def config(self):
        with self.factory() as c:
            row = c.execute('SELECT * FROM rvbbit.application_login_settings').fetchone()
        return {**row, 'google_client_secret': self.unseal(row['google_client_secret'])}

    def account(self, person):
        with self.factory() as c:
            return c.execute('SELECT * FROM rvbbit.application_accounts WHERE email=%s', (person,)).fetchone()

    def version(self, person):
        account = self.account(person)
        return account['credential_version'] if account and account['enabled'] else None

    def session_valid(self, person, version):
        try:
            current = self.version(person)
            return current is not None and current == version
        except Exception:
            return False

    def authenticate(self, person, password):
        config = self.config()
        if config['google_only']:
            return False
        row = self.account(person)
        return bool(row and row['enabled'] and row['password_secret'] and self.check_password(password, row['password_secret']))

    def allowed(self, person):
        row = self.account(person)
        if row:
            return row['enabled']  # A disabled account always wins over a domain/env allowlist.
        allowed = self.config()['allowed_emails']
        if any(person == item or (item.startswith('@') and person.endswith(item)) for item in allowed):
            return True
        with self.factory() as c:
            row = c.execute('SELECT login_allowed FROM rvbbit.application_principals WHERE email=%s', (person,)).fetchone()
            return bool(row and row['login_allowed'])

    def google_login(self, claims):
        person = email(claims['email'])
        if not self.allowed(person):
            raise ActionError('NOT_ALLOWED', 'That account is not allowed to sign in here.', 403)
        with self.factory() as c, c.transaction():
            c.execute('INSERT INTO rvbbit.application_principals(email,display_name,login_allowed) VALUES(%s,%s,true) ON CONFLICT(email) DO NOTHING',
                      (person, str(claims.get('name') or '')[:160] or None))
            c.execute('INSERT INTO rvbbit.application_accounts(email) VALUES(%s) ON CONFLICT DO NOTHING', (person,))
            row = c.execute('SELECT * FROM rvbbit.application_accounts WHERE email=%s FOR UPDATE', (person,)).fetchone()
            if not row['enabled'] or (row['google_subject'] and row['google_subject'] != claims['sub']):
                raise ActionError('NOT_ALLOWED', 'This Google identity is not allowed to use that account.', 403)
            c.execute('UPDATE rvbbit.application_accounts SET google_subject=%s WHERE email=%s', (claims['sub'], person))
        return person

    def admin(self, c, actor):
        teams._require_admin(c, actor)
        row = c.execute('SELECT enabled FROM rvbbit.application_accounts WHERE email=%s', (actor,)).fetchone()
        if not row or not row['enabled']:
            raise ActionError('FORBIDDEN', 'An active administrator account is required.', 403)

    def profile(self, actor):
        with self.factory() as c:
            row = c.execute('''SELECT a.email,a.enabled,a.credential_version,a.password_secret IS NOT NULL AS has_password,
                a.google_subject IS NOT NULL AS google_connected,a.password_changed_at,p.display_name
                FROM rvbbit.application_accounts a JOIN rvbbit.application_principals p USING(email) WHERE a.email=%s''', (actor,)).fetchone()
            if not row:
                raise ActionError('NOT_FOUND', 'Account not found.', 404)
            memberships = c.execute('''SELECT t.id,t.name,t.system_key FROM rvbbit.teams t
                WHERE NOT t.archived AND (t.system_key='everyone' OR EXISTS(SELECT 1 FROM rvbbit.team_members m
                WHERE m.team_id=t.id AND m.principal_email=%s)) ORDER BY t.name''', (actor,)).fetchall()
            return {**row, 'can_administer': teams._is_admin(c, actor), 'groups': memberships}

    def people(self, actor):
        with self.factory() as c:
            self.admin(c, actor)
            return {'people': c.execute('''SELECT p.email,p.display_name,p.last_seen_at,
                a.enabled,a.password_secret IS NOT NULL AS has_password,a.google_subject IS NOT NULL AS google_connected,
                a.created_at,coalesce(array_agg(t.name ORDER BY t.name) FILTER(WHERE t.id IS NOT NULL),'{}') AS groups
                FROM rvbbit.application_principals p LEFT JOIN rvbbit.application_accounts a USING(email)
                LEFT JOIN rvbbit.team_members m ON m.principal_email=p.email
                LEFT JOIN rvbbit.teams t ON t.id=m.team_id AND NOT t.archived
                GROUP BY p.email,a.email ORDER BY p.email''').fetchall()}

    def create(self, actor, body):
        person = email(body.get('email'))
        name = str(body.get('display_name') or '').strip()[:160]
        with self.factory() as c, c.transaction():
            self.admin(c, actor)
            c.execute('INSERT INTO rvbbit.application_principals(email,display_name,login_allowed) VALUES(%s,%s,true) ON CONFLICT(email) DO UPDATE SET display_name=coalesce(excluded.display_name,rvbbit.application_principals.display_name),login_allowed=true', (person, name or None))
            if not c.execute('INSERT INTO rvbbit.application_accounts(email) VALUES(%s) ON CONFLICT DO NOTHING RETURNING email', (person,)).fetchone():
                raise ActionError('EXISTS', 'That account already exists.', 409)
            self.event(c, actor, person, 'account_created')
            only = c.execute('SELECT google_only FROM rvbbit.application_login_settings').fetchone()['google_only']
            token = None if only else self._link(c, actor, person)
        return {'email': person, 'setup_token': token}

    def _link(self, c, actor, person):
        token = secrets.token_urlsafe(32)
        c.execute('UPDATE rvbbit.application_account_links SET used_at=now() WHERE email=%s AND used_at IS NULL', (person,))
        c.execute("INSERT INTO rvbbit.application_account_links(token_hash,email,expires_at,created_by) VALUES(%s,%s,now()+interval '24 hours',%s)",
                  (hashlib.sha256(token.encode()).hexdigest(), person, actor))
        return token

    def setup_link(self, actor, person):
        if self.config()['google_only']:
            raise ActionError('GOOGLE_ONLY', 'This instance uses Google sign-in; no password setup is needed.', 400)
        person = email(person)
        with self.factory() as c, c.transaction():
            self.admin(c, actor)
            row = c.execute('SELECT enabled FROM rvbbit.application_accounts WHERE email=%s FOR UPDATE', (person,)).fetchone()
            if not row or not row['enabled']:
                raise ActionError('NOT_FOUND', 'Choose an active account.', 404)
            token = self._link(c, actor, person)
            self.event(c, actor, person, 'password_link_created')
            return {'email': person, 'setup_token': token}

    def redeem(self, token, password):
        if self.config()['google_only']:
            raise ActionError('GOOGLE_ONLY', 'This instance uses Google sign-in. Sign in with your allowed email at /login.', 403)
        password_valid(password)
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise ActionError('INVALID_LINK', 'This setup link is invalid or expired.', 400)
        hashed = self.hash_password(password)
        with self.factory() as c, c.transaction():
            row = c.execute('''SELECT l.* FROM rvbbit.application_account_links l JOIN rvbbit.application_accounts a USING(email)
                WHERE l.token_hash=%s AND l.used_at IS NULL AND l.expires_at>now() AND a.enabled FOR UPDATE OF l,a''',
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if not row:
                raise ActionError('INVALID_LINK', 'This setup link is invalid or expired.', 400)
            c.execute('UPDATE rvbbit.application_account_links SET used_at=now() WHERE token_hash=%s', (row['token_hash'],))
            c.execute('''UPDATE rvbbit.application_accounts SET password_secret=%s,password_changed_at=now(),updated_at=now(),
                credential_version=credential_version+1 WHERE email=%s''', (hashed, row['email']))
            self.event(c, row['email'], row['email'], 'password_set_from_link')
            return row['email']

    def change_password(self, actor, body, session):
        password_valid(body.get('password'))
        with self.factory() as c, c.transaction():
            row = c.execute('SELECT * FROM rvbbit.application_accounts WHERE email=%s FOR UPDATE', (actor,)).fetchone()
            if not row or not row['enabled']:
                raise ActionError('FORBIDDEN', 'Sign in again.', 403)
            if row['password_secret']:
                if not self.check_password(body.get('current_password', ''), row['password_secret']):
                    raise ActionError('INVALID_PASSWORD', 'Current password is incorrect.', 400)
            elif session.get('via') != 'google' or time.time() - session.get('auth_time', 0) > 300:
                raise ActionError('REAUTHENTICATE', 'Sign in with Google again before setting a password.', 403)
            c.execute('''UPDATE rvbbit.application_accounts SET password_secret=%s,password_changed_at=now(),updated_at=now(),
                credential_version=credential_version+1 WHERE email=%s''', (self.hash_password(body['password']), actor))
            self.event(c, actor, actor, 'password_changed')
        return {'ok': True}

    def update(self, actor, person, body):
        person = email(person)
        with self.factory() as c, c.transaction():
            # Same lock as group membership changes: cannot disable the last usable administrator.
            c.execute("SELECT id FROM rvbbit.teams WHERE system_key='admins' FOR UPDATE")
            self.admin(c, actor)
            row = c.execute('SELECT * FROM rvbbit.application_accounts WHERE email=%s FOR UPDATE', (person,)).fetchone()
            if not row:
                raise ActionError('NOT_FOUND', 'Account not found.', 404)
            if 'enabled' in body:
                enabled = body['enabled']
                if not isinstance(enabled, bool):
                    raise ValueError('Enabled must be true or false.')
                if not enabled and teams._is_admin(c, person):
                    others = c.execute('''SELECT count(*) AS n FROM rvbbit.team_members m JOIN rvbbit.teams t ON t.id=m.team_id
                        JOIN rvbbit.application_accounts a ON a.email=m.principal_email
                        WHERE t.system_key='admins' AND a.enabled AND a.email<>%s''', (person,)).fetchone()['n']
                    if not others:
                        raise ActionError('LAST_ADMIN', 'Keep at least one active administrator.', 409)
                c.execute('UPDATE rvbbit.application_accounts SET enabled=%s,credential_version=credential_version+1,updated_at=now() WHERE email=%s', (enabled, person))
                c.execute('UPDATE rvbbit.application_principals SET login_allowed=%s WHERE email=%s', (enabled, person))
                c.execute('UPDATE rvbbit.application_account_links SET used_at=now() WHERE email=%s AND used_at IS NULL', (person,))
                self.event(c, actor, person, 'account_enabled' if enabled else 'account_disabled')
            if 'display_name' in body:
                c.execute('UPDATE rvbbit.application_principals SET display_name=%s WHERE email=%s', (str(body['display_name'] or '').strip()[:160] or None, person))
                self.event(c, actor, person, 'profile_updated')
        return {'ok': True}

    def save_profile(self, actor, body):
        with self.factory() as c:
            c.execute('UPDATE rvbbit.application_principals SET display_name=%s WHERE email=%s', (str(body.get('display_name') or '').strip()[:160] or None, actor))
        return {'ok': True}

    def settings(self, actor):
        with self.factory() as c:
            self.admin(c, actor)
        config = self.config()
        return {k:v for k,v in config.items() if k not in ('google_client_secret','singleton')} | {'has_google_secret': bool(config['google_client_secret'])}

    def save_settings(self, actor, body):
        allowed = body.get('allowed_emails', [])
        if not isinstance(allowed, list) or len(allowed)>1000:
            raise ValueError('Provide up to 1,000 allowed email addresses or @domains.')
        allowed = sorted({email('member'+v)[6:] if isinstance(v,str) and v.startswith('@') else email(v) for v in allowed})
        client = str(body.get('google_client_id') or '').strip()
        if client and (not client.endswith('.apps.googleusercontent.com') or len(client)>300):
            raise ValueError('Use a Google OAuth web client ID.')
        domain = str(body.get('google_domain') or '').strip().lower()
        if domain:
            email('member@'+domain)
        with self.factory() as c, c.transaction():
            self.admin(c, actor)
            current = c.execute('SELECT * FROM rvbbit.application_login_settings FOR UPDATE').fetchone()
            if body.get('revision') != current['revision']:
                raise ActionError('CONFLICT', 'Sign-in settings changed. Reload before saving.', 409)
            secret = str(body.get('google_client_secret') or '').strip()
            if len(secret)>2048:
                raise ValueError('Google client secret is too long.')
            stored = self.seal(secret) if secret else current['google_client_secret']
            if client != current['google_client_id'] and client and not secret:
                raise ValueError('Provide the matching secret when changing the Google client.')
            if client and not stored:
                raise ValueError('Provide the Google client secret.')
            only = body.get('google_only', False)
            if not isinstance(only, bool) or (only and not (client and stored)):
                raise ValueError('Configure Google sign-in before turning off passwords.')
            # Retiring passwords requires a successfully linked admin Google identity.
            if only and not current['google_only']:
                me = c.execute('SELECT google_subject FROM rvbbit.application_accounts WHERE email=%s', (actor,)).fetchone()
                if not me or not me['google_subject']:
                    raise ValueError('Sign in with Google successfully before turning off password sign-in.')
            c.execute('''UPDATE rvbbit.application_login_settings SET allowed_emails=%s,google_client_id=%s,
                google_client_secret=%s,google_domain=%s,google_only=%s,revision=revision+1,updated_at=now()''',
                (allowed,client,stored if client else None,domain,only))
            self.event(c, actor, None, 'sign_in_settings_changed', {'allowed_entries':len(allowed),'google_configured':bool(client),'google_only':only})
        return self.settings(actor)

    def events(self, actor):
        with self.factory() as c:
            self.admin(c, actor)
            return {'events':c.execute('SELECT actor,email,action,detail,created_at FROM rvbbit.application_account_events ORDER BY id DESC LIMIT 100').fetchall()}


def configure(auth, factory):
    if auth.AUTH_MODE != 'shared':
        return
    store = Accounts(factory, auth.JWT_SECRET)
    store.initialize(auth)
    auth.ACCOUNTS = store
