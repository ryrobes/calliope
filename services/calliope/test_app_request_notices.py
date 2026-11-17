"""App improvement requests reach people through the Work Inbox and Gallery, not a separate inbox."""
import json
from pathlib import Path

import app_review as review

OWNER, WORKER = 'owner@test', 'maria@test'


def request(ident, status, *, requested_by=WORKER, owner=OWNER, actor=OWNER, releases=(), key=''):
    return {'id': ident, 'user_message': 'Add a  photo field\nto the inspection form', 'slug': 'inspections',
            'title': 'Inspections', 'owner_email': owner, 'requested_by': requested_by,
            'releases': [{'version': v} for v in releases], 'status': status,
            'url': f'/apps/inspections/changes?request={ident}',
            'review': {'can_triage': actor == owner, 'is_requester': actor == requested_by, 'release_key': key}}


class Conn:
    def __init__(self):
        self.calls = []
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        rows = [{'email': WORKER, 'display_name': 'Maria Lopez'}] if 'application_principals' in sql else []
        return type('R', (), {'fetchall': lambda _: rows, 'fetchone': lambda _: None})()


def run(monkeypatch, actor, rows):
    conn = Conn()
    monkeypatch.setattr(review, 'ensure', lambda factory: None)
    monkeypatch.setattr(review, '_requests', lambda c, a: rows)
    review.sync_notices(lambda: conn, actor)
    inserts = [p for sql, p in conn.calls if sql.startswith('INSERT INTO rvbbit.calliope_work_items')]
    closes = [p for sql, p in conn.calls if sql.startswith('UPDATE rvbbit.calliope_work_items')]
    return inserts, closes


def test_owner_hears_about_new_and_reopened_requests_from_other_people(monkeypatch):
    rows = [request('a', 'new'), request('b', 'reopened', releases=[3], key='k3'),
            request('c', 'acknowledged'), request('d', 'new', requested_by=OWNER)]
    inserts, closes = run(monkeypatch, OWNER, rows)
    assert [p[2] for p in inserts] == ['a', 'b'], 'triaged and self-made requests are not news'
    assert inserts[0][4] == 'Maria Lopez asked for a change to Inspections'
    assert inserts[0][5] == '“Add a photo field to the inspection form”'
    assert inserts[1][4] == "Maria Lopez says Inspections v3 didn't fix their request"
    context = json.loads(inserts[0][6])
    assert context['open_url'] == '/apps/inspections/changes?request=a' and context['role'] == 'owner'
    assert 'draft a revised version' in inserts[0][7] and 'change_requests' in inserts[0][7]
    assert inserts[0][1] == 'suggestion' and inserts[0][0] == OWNER
    assert closes == [(OWNER, [p[3] for p in inserts])], 'notices whose request moved on close themselves'


def test_requester_hears_when_a_version_names_their_request(monkeypatch):
    rows = [request('a', 'published', actor=WORKER, releases=[2, 4], key='k4'),
            request('b', 'fixed', actor=WORKER, releases=[4], key='k4'),
            request('c', 'new', actor=WORKER)]
    inserts, _ = run(monkeypatch, WORKER, rows)
    assert [p[2] for p in inserts] == ['a']
    assert inserts[0][4] == 'Your request for Inspections shipped in v4'
    assert inserts[0][1] == 'result' and "confirm it's fixed or reopen it" in inserts[0][5]
    assert inserts[0][3] == 'a:requester:published:k4', 'a later release is a new notice; the same one is not repeated'


def test_insert_keeps_a_dismissed_notice_dismissed(monkeypatch):
    inserts, _ = run(monkeypatch, OWNER, [request('a', 'new')])
    source = Path(review.__file__).read_text()
    assert 'ON CONFLICT (owner_email,source,dedupe_key) DO NOTHING' in source
    assert len(inserts) == 1


def test_gallery_counts_open_requests_on_the_viewers_own_apps(monkeypatch):
    rows = [request('a', 'new'), request('b', 'working'), request('c', 'published', releases=[2]),
            request('d', 'deferred'), request('e', 'reopened', releases=[2]),
            request('f', 'new', owner='someone@test', requested_by=OWNER)]
    monkeypatch.setattr(review, 'ensure', lambda factory: None)
    monkeypatch.setattr(review, '_requests', lambda c, a: rows)
    assert review.open_counts(lambda: Conn(), OWNER) == {'inspections': 3}


def test_requests_have_no_separate_nav_destination_and_feed_the_inbox():
    here = Path(__file__).parent
    server = (here / 'server.py').read_text()
    assert 'app-improvements-link' not in server and "fetch('/api/app-improvements')" not in server
    assert 'class="pill card-requests" href="/apps/{e(slug)}/changes"' in server
    calliope = (here / 'calliope.py').read_text()
    snapshot = calliope[calliope.index('def _inbox_snapshot('):calliope.index('def _inbox_item(')]
    assert 'app_review.sync_notices(conn_factory, owner)' in snapshot
    assert '"App request" if item.get("origin") == "app_request"' in calliope
