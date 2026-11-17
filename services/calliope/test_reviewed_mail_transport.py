"""Exercise actual probe sequencing without contacting or sending real mail."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('reviewed_mail_bridge', Path(__file__).parents[2] / 'docker/calliope/reviewed_mail_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


@pytest.fixture
def transport(monkeypatch):
    events, failures = [], {}
    def event(name, *args):
        events.append((name, args))
        if name in failures:
            raise failures[name]
    class IMAP:
        def __init__(self, *args, **kwargs): event('imap.connect')
        def login(self, *args): event('imap.login', *args)
        def select(self, folder, *, readonly):
            assert folder == 'INBOX' and readonly
            event('imap.inbox')
            return 'OK', [b'2']
        def response(self, name):
            event('imap.cursor')
            return name, [b'8' if name == 'UIDVALIDITY' else b'3']
        def logout(self): event('imap.logout')
        def shutdown(self): event('imap.shutdown')
    class SMTP:
        def __init__(self, *args, **kwargs): event('smtp.connect')
        def ehlo(self): event('smtp.greeting')
        def starttls(self, **kwargs): event('smtp.tls')
        def login(self, *args): event('smtp.login', *args)
        def close(self): event('smtp.close')
        def sendmail(self, *args): pytest.fail('A connection test must never send mail')
    monkeypatch.setattr(bridge.imaplib, 'IMAP4_SSL', IMAP)
    monkeypatch.setattr(bridge.smtplib, 'SMTP', SMTP)
    monkeypatch.setattr(bridge.smtplib, 'SMTP_SSL', SMTP)
    return events, failures


def account(**updates):
    return {'address': 'callie@example.com', 'password': 'abcd efgh ijkl mnop',
            'imap_host': 'imap.gmail.com', 'smtp_host': 'smtp.gmail.com', **updates}


@pytest.mark.parametrize('port', [587, 465])
def test_gmail_app_password_spaces_read_only_probe_and_cleanup(transport, port):
    events, failures = transport
    # A dropped logout/close must not turn a successful login into a failure.
    failures['imap.logout'] = OSError('connection already closed')
    failures['smtp.close'] = OSError('connection already closed')
    result = bridge.probe({'account': account(smtp_port=port)})
    assert result['ok'] and result['cursor'] == {'uidvalidity': 8, 'last_uid': 2}
    assert all(result['checks'].values())
    logins = [args for name, args in events if name.endswith('.login')]
    assert logins == [('callie@example.com', 'abcdefghijklmnop')] * 2
    assert ('smtp.tls', ()) in events if port == 587 else ('smtp.tls', ()) not in events


def test_other_provider_password_spaces_preserved(transport):
    events, _ = transport
    result = bridge.probe({'account': account(imap_host='imap.example.com', smtp_host='smtp.example.com')})
    assert result['ok']
    assert [args[1] for name, args in events if name.endswith('.login')] == ['abcd efgh ijkl mnop'] * 2


@pytest.mark.parametrize('stage,error,code,imap_ok,inbox_ok', [
    ('imap.connect', bridge.socket.gaierror('private-canary'), 'dns', False, False),
    ('imap.connect', TimeoutError('private-canary'), 'timeout', False, False),
    ('imap.connect', bridge.ssl.SSLCertVerificationError('private-canary'), 'tls_certificate', False, False),
    ('imap.login', bridge.imaplib.IMAP4.error('[AUTHENTICATIONFAILED] private-canary'), 'authentication', False, False),
    ('imap.login', bridge.imaplib.IMAP4.abort('private-canary'), 'disconnected', False, False),
    ('imap.login', bridge.imaplib.IMAP4.error('Application-specific password required private-canary'), 'app_password_required', False, False),
    ('imap.login', bridge.imaplib.IMAP4.error('Please log in via your web browser private-canary'), 'web_login_required', False, False),
    ('imap.login', bridge.imaplib.IMAP4.error('IMAP access is disabled private-canary'), 'access_disabled', False, False),
    ('imap.login', bridge.imaplib.IMAP4.error('Too many simultaneous connections private-canary'), 'rate_limited', False, False),
    ('imap.login', bridge.imaplib.IMAP4.error('NO private-canary'), 'login_rejected', False, False),
    ('imap.inbox', bridge.imaplib.IMAP4.error('private-canary'), 'inbox', True, False),
    ('imap.cursor', TypeError('private-canary'), 'mailbox_metadata', True, True),
    ('smtp.connect', ConnectionRefusedError('private-canary'), 'connection', True, True),
    ('smtp.tls', bridge.ssl.SSLError('private-canary'), 'tls', True, True),
    ('smtp.login', bridge.smtplib.SMTPAuthenticationError(535, b'private-canary'), 'authentication', True, True),
])
def test_safe_stage_diagnostics_survive_cleanup_failure(transport, caplog, stage, error, code, imap_ok, inbox_ok):
    events, failures = transport
    failures.update({stage: error, 'imap.logout': RuntimeError('cleanup-private-canary'), 'smtp.close': RuntimeError('cleanup-private-canary')})
    result = bridge.probe({'account': account()})
    assert result['ok'] is False and result['stage'] == stage and result['code'] == code
    assert result['checks'] == {'imap_authenticated': imap_ok, 'inbox_opened': inbox_ok, 'smtp_authenticated': False}
    assert 'private-canary' not in json.dumps(result) + caplog.text
    assert 'abcd efgh' not in json.dumps(result) + caplog.text
    if stage.startswith('imap.'):
        assert not any(name.startswith('smtp.') for name, _ in events)
    if stage == 'smtp.login':
        assert result['smtp_code'] == 535


def test_internal_error_is_not_reported_as_bad_password(transport, caplog):
    result = bridge.probe({})
    assert not result['ok'] and result['code'] == 'internal'
    assert 'not a password rejection' in result['message']
