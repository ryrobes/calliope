import {escapeHtml as esc} from './business-logic-model.mjs';

const usd=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:6,maximumFractionDigits:9});
export function money(value){return usd.format(value||0);}
export function costLabel(row){return !row?.captured?'Unavailable':row.pending?(row.known_usd?money(row.known_usd)+' + pending':'Pending'):money(row.known_usd);}
export function cacheLabel(row){return row?.captured?(row.decisions?`${row.cached}/${row.decisions} cached`:'No calls'):'Not recorded';}

// Accounting only: retries never evaluate a rule. A generation prevents late
// responses from reappearing after the user changes documents or reruns SQL.
export function usageTracker({fetchUsage,updated,schedule=setTimeout,cancel=clearTimeout,visible=()=>true}){
 const snapshots=new Map();let timer=null,generation=0,delay=2500;
 function plan(){if(timer!==null||![...snapshots.values()].some(v=>v.pending))return;timer=schedule(poll,delay);}
 async function poll(){
  timer=null;const epoch=generation;
  if(visible())await Promise.all([...snapshots.values()].filter(v=>v.pending).map(async current=>{
   try{const next=await fetchUsage(current.id);if(epoch!==generation||snapshots.get(current.id)!==current)return;snapshots.set(next.id,next);updated(next);}
   catch{/* Preserve pending costs and retry; never turn a missing price into $0. */}
  }));
  if(epoch===generation){delay=Math.min(30000,delay*1.5);plan();}
 }
 return {get:id=>snapshots.get(id),track(value){if(!value)return;snapshots.set(value.id,value);updated(value);delay=2500;plan();},
  clear(){generation++;if(timer!==null)cancel(timer);timer=null;snapshots.clear();delay=2500;}};
}

export function usageDetail(row){
 if(!row)return '<p>Usage is not available for this evaluation.</p>';
 return `<div class="usage-total"><strong>${costLabel(row)}</strong><span>${row.decisions} decision${row.decisions===1?'':'s'} · ${cacheLabel(row)}</span></div>
 <p class="quiet">USD charged for this evaluation. An RVBBIT cache hit makes no new provider call. Pending amounts update automatically as receipts and provider costs arrive.</p>
 <ol class="usage-steps">${row.steps.map(step=>`<li><div><code>${esc(step.node)}</code><strong>${esc(step.question)}</strong></div><div><span>${step.cache==='l1'?'RVBBIT memory cache':step.cache==='l2'?'RVBBIT receipt cache':step.cache==='miss'?'Provider call':'Cache status unknown'}</span><strong>${step.pending?(step.known_usd?money(step.known_usd)+' + pending':'Pending'):money(step.known_usd)}</strong></div>${step.error?'<p class="quiet">The decision call reported an error; any reported charge is retained.</p>':''}${step.receipt_id?`<small>Receipt <code>${esc(step.receipt_id)}</code></small>`:''}</li>`).join('')||'<li>No decision calls were needed.</li>'}</ol>`;
}
