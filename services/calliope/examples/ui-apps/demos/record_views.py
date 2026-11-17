"""Author a portable visit summary using ordinary SQL and catalog components."""
def compose(doc):
    doc['data']['job'].update(appointment='', next_step='')
    doc['queries']['job']['sql'] = doc['queries']['job']['sql'].replace(
        "END AS parts", "END AS parts, j.scheduled_date || ' · ' || coalesce(s.start,'08:00') || ' · ' || j.technician AS appointment, "
        "CASE WHEN coalesce(sum(l.backorder),0)>0 THEN 'Next: coordinate the outstanding parts' "
        "WHEN j.status='Complete' THEN 'Next: review the service report' ELSE 'Next: review the visit and handoff' END AS next_step")
    doc['records'] = {'visit': {
        'id': {'path': '/job/id'}, 'title': {'path': '/job/customer'},
        'when': {'/state/page': 'field'},
        'open': {'/state/page': 'field', '/state/selection/job': {'recordId': True}, '/state/controls/review': ''},
        'summary': {'queries': ['job'], 'components': [
            {'id': 'root', 'component': 'CStack', 'direction': 'column', 'gap': 'sm',
             'children': ['identity', 'status', 'appointment', 'parts', 'next']},
            {'id': 'identity', 'component': 'CText', 'text': {'path': '/job/id'}, 'size': 'eyebrow', 'tone': 'muted'},
            {'id': 'status', 'component': 'CBadge', 'text': {'path': '/job/status'}, 'tone': 'accent'},
            {'id': 'appointment', 'component': 'CText', 'text': {'path': '/job/appointment'}, 'size': 'body'},
            {'id': 'parts', 'component': 'CText', 'text': {'path': '/job/parts'}, 'size': 'body', 'tone': 'warm'},
            {'id': 'next', 'component': 'CText', 'text': {'path': '/job/next_step'}, 'size': 'caption', 'tone': 'muted'},
        ]}
    }}
