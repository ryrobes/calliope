"""Per-row decision costs, resolved against the existing append-only cost ledger."""
from collections import defaultdict
from decimal import Decimal
import uuid

from psycopg.types.json import Jsonb
from app_actions import ActionError


def summarize(rows, ledger):
    result = []
    for row in rows:
        steps = []
        captured = row['steps'] is not None
        for call in row['steps'] or []:
            step = dict(call)
            cached = call.get('cache') in ('l1', 'l2')
            costs = ledger.get(call.get('receipt_id'), [])
            known = sum((Decimal(str(c['cost_usd'])) for c in costs if c['cost_usd'] is not None), Decimal(0))
            pending = not cached and call.get('calls', 1) != 0 and (not costs or len(costs) < call.get('calls', 1) or
                                     any(c['cost_usd'] is None or c['status'] not in ('settled', 'free') for c in costs))
            # Inline usage is provisional until the ledger has all sub-calls.
            if not costs and call.get('cost_usd') is not None and not cached:
                known = Decimal(str(call['cost_usd']))
            step.update(known_usd=0 if cached else float(known), pending=pending,
                        status='cached' if cached else 'pending' if pending else 'settled',
                        tokens_in=sum(c.get('tokens_in') or 0 for c in costs),
                        tokens_out=sum(c.get('tokens_out') or 0 for c in costs))
            steps.append(step)
        pending = not captured or any(s['pending'] for s in steps)
        result.append({'row':row['row'], 'steps':steps, 'decisions':len(steps),
                       'cached':sum(s['status']=='cached' for s in steps),
                       'fresh':sum(s.get('cache')=='miss' for s in steps),
                       'known_usd':sum(s['known_usd'] for s in steps),
                       'pending':pending, 'captured':captured})
    return {'rows':result, 'pending':any(r['pending'] for r in result),
            'known_usd':sum(r['known_usd'] for r in result),
            'decisions':sum(r['decisions'] for r in result),
            'cached':sum(r['cached'] for r in result)}


def record(factory, actor, decisions):
    run_id = str(uuid.uuid4())
    rows = [{'row':i + 1, 'steps':d.get('usage') if isinstance(d,dict) else None}
            for i,d in enumerate(decisions)]
    with factory() as conn:
        conn.execute('INSERT INTO rvbbit.calliope_business_logic_usage(id,owner_email,rows) VALUES(%s,%s,%s)',
                     (run_id, actor, Jsonb(rows)))
    return read(factory, actor, run_id)


def read(factory, actor, run_id):
    import business_logic as logic
    logic.identity(actor)
    with factory() as conn:
        row = conn.execute('SELECT rows FROM rvbbit.calliope_business_logic_usage WHERE id=%s AND owner_email=%s',
                           (logic.uid(run_id), actor)).fetchone()
        if not row:
            raise ActionError('NOT_FOUND','Decision usage not found',404)
        ids = sorted({s['receipt_id'] for r in row['rows'] for s in (r['steps'] or []) if s.get('receipt_id')})
        ledger = defaultdict(list)
        if ids:
            for cost in conn.execute('''SELECT receipt_id::text,sub_call_index,status,cost_usd,tokens_in,tokens_out
                FROM rvbbit.cost_latest WHERE receipt_id=ANY(%s::uuid[])''', (ids,)).fetchall():
                ledger[cost['receipt_id']].append(cost)
    return {'id':str(run_id), **summarize(row['rows'], ledger)}
