#!/usr/bin/env python3
"""Small paid retrieval smoke comparison; synthetic cases, not a benchmark claim."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import time
import urllib.request

CASES = [
    ("callback", "Service visit: compressor failed again two days after a completed repair. Dispatch a callback technician.", "equipment broke again after we fixed it"),
    ("new_install", "Sales lead: customer wants a quote for a new heat-pump installation, no existing repair.", "estimate for installing a heat pump"),
    ("paid", 'invoice_id=INV-104; customer=Harbor Dental; amount=2400; status=paid; balance_due=0', "Harbor Dental invoice already settled"),
    ("overdue", 'invoice_id=INV-105; customer=Harbor Dental; amount=800; status=overdue; balance_due=800', "unpaid Harbor Dental bill"),
    ("invoice_schema", 'Table accounting.invoices: one row per bill. invoice_id primary key, customer_id joins customers.id, total_amount numeric, due_date date, paid_at timestamp.', "which table joins customers to their bills and payment dates"),
    ("revenue_sql", 'SELECT date_trunc(\'month\', paid_at) AS month, sum(total_amount) AS revenue FROM accounting.invoices WHERE paid_at IS NOT NULL GROUP BY 1;', "monthly collected revenue query"),
    ("open_sql", 'SELECT customer_id, count(*) AS open_jobs FROM service.jobs WHERE completed_at IS NULL GROUP BY customer_id;', "count unfinished service jobs per client"),
    ("stock", 'Inventory warehouse=North; sku=CAP-45-5; description=dual run capacitor 45/5 microfarad; quantity_on_hand=18; reorder_point=5.', "do we carry a 45 plus 5 MFD capacitor"),
    ("appointment", 'Customer preference: Elena can only receive technicians after 3pm on weekdays. Call before arrival.', "when can we visit Elena"),
    ("cancel", 'Message: Please cancel the Friday maintenance visit. I will contact you when I want to reschedule.', "customer wants to stop the upcoming appointment"),
    ("spanish", 'El cliente indica que el aire acondicionado no enfría y hace mucho ruido. Solicita reparación urgente.', "urgent noisy air conditioning that does not cool"),
    ("memory", 'The customer approved replacing the rooftop unit rather than repairing it. The approval came from Dana on Tuesday.', "who authorized rooftop unit replacement"),
    ("doc_config", 'Hindsight embedding configuration uses HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL and model embed to share the installation vector space.', "configure memory to use our shared embedding endpoint"),
    ("receipt", 'Receipt event: decision_tree hvac_triage, node callback, outcome repeat, model typesafe/jev-1.13, cached=true, billed_cost_usd=0.', "cached triage decision with no new inference charge"),
    ("airline", 'cubes.passenger_activity_monthly: activity_month, geo_summary, activity_type_code, price_category_code, passenger_count. Grain: month and traffic classifications.', "monthly domestic versus international airline passenger totals"),
    ("sighting", 'Witness describes a tall hairy biped crossing a logging road at dusk, then disappearing into the woods. No photograph was taken.', "unphotographed bigfoot encounter beside a forest road"),
]


def request(key, path, body):
    req=urllib.request.Request('https://openrouter.ai/api/v1/'+path,data=json.dumps(body).encode(),
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=90) as response: return json.load(response)


def evaluate(key, model):
    started=time.monotonic()
    data=request(key,'embeddings',{'model':model,'dimensions':1024,'encoding_format':'float',
        'input':[x[1] for x in CASES]+[x[2] for x in CASES]})
    vectors=[x['embedding'] for x in sorted(data['data'],key=lambda x:x['index'])]
    if len(vectors)!=len(CASES)*2 or any(len(v)!=1024 for v in vectors): raise ValueError('Incorrect dimensions or result count')
    def cosine(a,b): return sum(x*y for x,y in zip(a,b))/math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))
    ranks=[]
    for i,q in enumerate(vectors[len(CASES):]):
        order=sorted(range(len(CASES)),key=lambda j:cosine(q,vectors[j]),reverse=True)
        ranks.append({'case':CASES[i][0],'rank':order.index(i)+1,'first':CASES[order[0]][0]})
    return {'model':model,'dimensions':1024,'cases':len(CASES),'top1':sum(x['rank']==1 for x in ranks),
        'top3':sum(x['rank']<=3 for x in ranks),'mrr':sum(1/x['rank'] for x in ranks)/len(ranks),
        'seconds':round(time.monotonic()-started,3),'usage':data.get('usage'),'ranks':ranks}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--key-file',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();key=args.key_file.expanduser().read_text().strip()
    models=['voyageai/voyage-4','perplexity/pplx-embed-v1-0.6b','qwen/qwen3-embedding-8b','openai/text-embedding-3-small']
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda m:evaluate(key,m),models))
    args.output.write_text(json.dumps({'scope':'16 synthetic mixed-workload smoke cases, not an independent quality benchmark','results':results},indent=2)+'\n')
    print(json.dumps([{k:v for k,v in r.items() if k!='ranks'} for r in results],indent=2))
