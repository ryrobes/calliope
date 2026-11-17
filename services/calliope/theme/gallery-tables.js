/* Table observations share Gallery's cards and lens. No authoring UI or scans. */
(() => {
'use strict';
const $=s=>document.querySelector(s),esc=window.CalliopeMetricCards.esc;
window.galleryDataView=new URL(location.href).searchParams.get('inventory')==='tables'?'tables':'jobs';
const nav=document.createElement('div');nav.className='gallery-data-nav metric-relation-filter';nav.hidden=true;
nav.setAttribute('role','group');nav.setAttribute('aria-label','Data inventory');
nav.innerHTML='<button data-data-view="jobs">Jobs</button><button data-data-view="tables">Tables</button>';
$('#gallery-jobs').before(nav);
const shell=document.createElement('section');shell.id='gallery-tables';shell.className='metrics-browser';shell.hidden=true;
shell.innerHTML=`<div class="metrics-browser-head"><div><span>Table inventory</span><strong>The data behind the answers</strong><small class="table-feedback" role="status">Stored observations · no new analysis</small></div><div class="metric-browser-filters"><label class="table-schema-label">Schema <select aria-label="Table schema"><option value="">All schemas</option></select></label><div class="metric-relation-filter"><button data-table-refresh>Refresh</button></div></div></div><div class="table-grid metric-gallery-sections"></div><div class="metric-relation-filter table-more" hidden><button data-table-more>Load more tables</button></div>`;
$('#gallery-jobs').after(shell);
const modal=document.createElement('dialog');modal.className='metric-lens job-dialog table-dialog';modal.setAttribute('aria-label','Table details');document.body.append(modal);
let tables=[],active=false,loaded=false,schema='',selected=null,request=0,abort=null,total=0,nextOffset=null,listRequest=0,listAbort=null,searchTimer=null,requestedKey=null;
const fmt=v=>v==null?'—':Number(v).toLocaleString(),compact=v=>v==null?'—':Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1}).format(v);
const time=v=>v?new Date(v).toLocaleString():'Not recorded',age=v=>v?window.CalliopeMetricCards.relative(v):'No observation';
const text=v=>v==null?'—':typeof v==='object'?JSON.stringify(v):String(v);
const bytes=v=>v==null?'—':v<1024?fmt(v)+' B':v<1048576?(v/1024).toFixed(1)+' KB':v<1073741824?(v/1048576).toFixed(1)+' MB':(v/1073741824).toFixed(1)+' GB';
const cell=v=>esc(text(v));
const percent=v=>v==null?'—':(Number(v)*100).toFixed(1)+'%';
const facts=items=>`<div class="job-facts">${items.map(([k,v])=>`<div><small>${esc(k)}</small><strong>${cell(v)}</strong></div>`).join('')}</div>`;
function tabular(head,rows){return `<div class="table-scroll" tabindex="0"><table><thead><tr>${head.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(v=>`<td>${cell(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
function spark(t){
 const h=t.history.slice().reverse().filter(h=>h.rows!=null);if(h.length<2)return '<div class="job-no-chart">'+(h.length?'One saved observation':'No saved row history')+'</div>';
 const values=h.map(h=>Number(h.rows)),lo=Math.min(...values),hi=Math.max(...values),span=hi-lo||Math.max(hi,1),points=h.map((row,i)=>[12+i/(h.length-1)*576,78-(Number(row.rows)-lo)/span*60]);
 return `<svg class="job-spark" viewBox="0 0 600 92" preserveAspectRatio="none" role="img" aria-label="Row count across ${h.length} catalog observations"><path d="${points.map((p,i)=>(i?'L':'M')+p.join(' ')).join(' ')}"/>${points.map((p,i)=>`<circle cx="${p[0]}" cy="${p[1]}" r="2.5"><title>${esc(time(h[i].captured_at)+' · '+fmt(h[i].rows)+' rows')}</title></circle>`).join('')}</svg>`;
}
function cardSpark(table){
 const series=table.history.slice().reverse().filter(h=>h.rows!=null&&Number.isFinite(Number(h.rows))).map(h=>({numeric_value:Number(h.rows)}));
 const chart=window.CalliopeMetricCards.sparkline({series,series_scope:'ticks'});
 return chart?chart.replace('class="metric-card-chart"','class="metric-card-chart job-card-chart"'):
  `<div class="metric-card-chart job-card-chart"><div class="job-no-chart">${series.length?'One saved observation':'No saved row history'}</div></div>`;
}
function hero(){
 if(!active)return;
 $('#gallery-kicker').textContent='Data · Tables';$('#gallery-title').innerHTML='Know what’s in <em>your data</em>.';
 $('#gallery-tally').textContent=total+' tables and views · stored observations';
}
 const assessment=t=>(t.business_assessments||[])[0];
 const assessmentMeta=a=>[a.name,(a.role||'').replaceAll('_',' '),a.confidence==null?'':percent(a.confidence)+' recorded confidence',a.lifecycle||a.status].filter(Boolean).join(' · ');
function paint(){
 const list=tables;
 const card=t=>`<article class="metric-gallery-card job-card table-card"><div class="metric-card-grid"></div>${cardSpark(t)}<button class="metric-card-open" data-table-open="${esc(t.ref)}" aria-label="Open ${esc(t.qualified_name)}"></button><div class="metric-card-actions"><button class="ask" data-table-ask="${esc(t.ref)}">✦ Ask</button></div><div class="metric-card-content"><span class="metric-card-status">${esc(t.kind)}</span><div class="metric-card-value"><strong title="${esc(fmt(t.rows))} rows">${t.row_source==='Planner estimate'&&t.rows!=null?'≈':''}${compact(t.rows)}</strong><small class="job-duration-label">rows</small></div><h2 class="metric-card-title" title="${esc(t.qualified_name)}">${esc(t.name)}</h2><p class="metric-card-description">${esc(assessment(t)?.rationale||t.description||t.column_count+' columns · '+t.kind.toLowerCase()+(t.is_partition?' · partition':''))}</p><div class="job-tables" title="${esc(assessment(t)?assessmentMeta(assessment(t))+' · Assessed '+time(assessment(t).updated_at):t.row_source)}">${assessment(t)?esc(assessment(t).name):t.column_count+' columns · '+esc(t.row_source)}</div><footer class="metric-card-foot"><span>${esc(t.schema)}</span><span title="Catalog observation · ${esc(time(t.observed_at))}">${esc(age(t.observed_at))}</span></footer></div></article>`;
 shell.querySelector('.table-grid').innerHTML=[...new Set(list.map(t=>t.schema))].map(s=>{const group=list.filter(t=>t.schema===s);return `<section class="metric-category-section"><header class="metric-category-head"><div class="metric-category-heading"><span>Schema</span><h2>${esc(s)}</h2></div><small class="metric-category-count">${group.length} objects</small></header><div class="metric-gallery-grid ${group.length<=2?'sparse':''}" style="--metric-columns:${Math.min(group.length,2)}">${group.map(card).join('')}</div></section>`;}).join('')||`<div class="metric-gallery-empty"><strong>${loaded?'No tables match this view.':'Loading tables…'}</strong><span>${loaded?'Try another schema or clear the search.':''}</span></div>`;
 hero();
}
async function api(path='',body,signal){
 const r=await fetch('/api/calliope/gallery/tables'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal}:{signal});
 let d;try{d=await r.json();}catch{throw Error('Table inventory is temporarily unavailable');}
 if(!r.ok)throw Error(d.error?.message||'Table inventory is unavailable');return d;
}
const filterKey=()=>JSON.stringify([$('#q')?.value||'',schema]);
async function load(more=false){
 requestedKey=filterKey();
 const ticket=++listRequest;listAbort?.abort();listAbort=new AbortController();const signal=listAbort.signal;
 shell.querySelector('.table-feedback').textContent='Loading metadata and saved observations…';
 const button=shell.querySelector('[data-table-more]');button.disabled=true;
 try{const params=new URLSearchParams({q:$('#q')?.value||'',schema,offset:String(more?nextOffset||0:0)});const d=await api('?'+params,null,signal);if(ticket!==listRequest)return;
 tables=more?tables.concat(d.tables):d.tables;total=d.total;nextOffset=d.next_offset;loaded=true;
 const picker=shell.querySelector('select');picker.innerHTML='<option value="">All schemas</option>'+d.schemas.map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join('');picker.value=schema;paint();
 shell.querySelector('.table-more').hidden=nextOffset==null;
 shell.querySelector('.table-feedback').textContent=(nextOffset!=null?'Showing '+tables.length+' of '+total+' objects. ':'')+(d.unavailable.length?'Some saved observations are unavailable; live metadata is shown.':'Stored observations · no new analysis');
 }catch(e){if(e.name==='AbortError'||ticket!==listRequest)return;shell.querySelector('.table-feedback').textContent=e.message;if(!loaded)shell.querySelector('.table-grid').innerHTML='<div class="metric-gallery-empty">Tables could not be loaded. Use Refresh to try again.</div>';}finally{if(ticket===listRequest)button.disabled=false;}
}
function details(t){
 const h=t.history[0],prior=t.history[1],profiles=new Map(t.profiles.map(p=>[p.name,p]));
 const columns=t.columns.map(c=>{const p=profiles.get(c.name);return [c.name+(c.primary_key?' · PK':''),c.type,c.nullable?'Yes':'No',c.description||'',p?percent(p.null_frac):percent(c.planner_null_frac),p?text(p.distinct)+(p.method==='sampled'?' in sample':''):c.planner_distinct==null?'—':Number(c.planner_distinct)<0?percent(-c.planner_distinct)+' of rows (est.)':fmt(c.planner_distinct)+' (est.)'];});
 const drift=t.drift.comparable?`<p class="table-note">Comparing ${esc(time(prior.captured_at))} → ${esc(time(h.captured_at))}. Changes in sampled profiles can reflect sampling; they do not establish a business problem.</p>${facts([['Rows · previous',fmt(prior.rows)],['Rows · latest',fmt(h.rows)]])}${t.drift.changes.length?tabular(['Column','Observation','Previous','Latest'],t.drift.changes.map(c=>[c.column,c.field,c.before,c.after])):'<p>No changes in the compared column definitions, null fractions, distinct counts, or ranges.</p>'}${t.drift.total>100?'<p>Showing the first 100 changes. Ask Calliope for a deeper comparison.</p>':''}`:'<p>Two complete catalog observations are needed for a comparison.</p>';
 const jobs=t.jobs||[];
 const accel=t.acceleration?`${facts([['Last acceleration refresh',time(t.acceleration.last_refresh_at)],['Rows in acceleration',fmt(t.acceleration.parquet_rows)],['Acceleration drift rows',fmt(t.acceleration.drift_rows)],['Shadow heap',t.acceleration.shadow_heap_dirty?'Pending changes':'No pending changes reported']])}<p class="table-note">Storage synchronization state, separate from catalog or semantic drift.</p>`:'<p>No stored acceleration observation available.</p>';
 modal.innerHTML=`<div class="metric-lens-shell"><header class="metric-lens-head"><div class="metric-lens-heading"><span>Table Lens · ${esc(t.kind)}</span><strong>${esc(t.qualified_name)}</strong><small>${t.column_count} columns · ${esc(t.row_source)}</small></div><div class="metric-lens-actions"><button class="ask" data-table-ask="${esc(t.ref)}">✦ <span>Ask Calliope</span></button></div><button class="metric-lens-close" data-table-close aria-label="Close">×</button></header><div class="metric-lens-content"><p class="job-purpose">${esc(assessment(t)?.rationale||t.description||t.catalog_description?.doc||'No description has been recorded for this table.')}</p>${assessment(t)?`<div class="table-assessment-note"><b>Saved business assessment</b><span>${esc(assessmentMeta(assessment(t)))}</span><small>Assessed ${esc(time(assessment(t).updated_at))} · ${t.business_assessments.length>1?t.business_assessments.length+' saved perspectives · ':''}reused from Business Topology</small></div>${t.description&&t.description!==assessment(t).rationale?`<p class="table-note"><b>Table comment:</b> ${esc(t.description)}</p>`:''}`:''}${facts([['Rows · '+t.row_source,fmt(t.rows)],['Catalog observed',time(t.observed_at)],['Storage · including indexes',t.relkind==='p'?'Partitioned parent (child storage not summed)':bytes(t.size_bytes)],['Planner last analyzed',time(t.analyzed_at)]])}
 <h3>Row preview</h3><div class="table-preview" aria-live="polite"><p>Loading a bounded preview…</p></div>
 <details open><summary>Columns · ${t.columns.length}</summary><p class="table-note">Null and distinct statistics use the latest saved profile where available, otherwise planner estimates. They are not live counts.</p>${tabular(['Column','Type','Nullable','Description','Nulls','Distinct'],columns)}</details>
 <details ${t.business_context.length?'open':''}><summary>Business context · ${t.business_context.length}</summary>${t.business_context.map(c=>`<section class="table-context"><h3>${esc(c.name)}</h3><small>${esc(c.status)} · ${esc(time(c.updated_at))}</small><p>${esc(c.summary||'')}</p>${c.memberships.map(m=>`<p><b>${esc(m.role||'Source')}</b> · ${esc(m.rationale||'')} ${m.confidence!=null?'('+percent(m.confidence)+' confidence)':''}</p>`).join('')}${c.questions.length?'<h3>Open questions</h3><ul>'+c.questions.map(q=>'<li>'+esc(q)+'</li>').join('')+'</ul>':''}</section>`).join('')||'<p>No saved business context is available for this table.</p>'}<p class="table-note">Stored interpretations and their recorded status; Calliope can verify or refine them.</p></details>
 <details open><summary>Catalog history & changes</summary>${spark(t)}${drift}</details>
 <details><summary>Saved value profiles · ${t.profiles.length}</summary><p class="table-note">Observed ${esc(time(t.observed_at))}${h?.sampled?' · based on '+fmt(h.sample_rows)+' sampled rows':''}. Examples and ranges are from the saved crawl.</p>${tabular(['Column','Minimum','Maximum','Example values','Percentiles'],t.profiles.map(p=>[p.name,p.min,p.max,p.examples.join(' · '),p.quantiles]))}</details>
 <details><summary>Declared relationships · ${t.relationships.length}</summary>${t.relationships.map(r=>`<p><b>${esc(r.source)} → ${esc(r.target)}</b><br><span class="table-note">${esc(r.definition)}</span></p>`).join('')||'<p>No foreign keys are declared. Inferred relationships may still exist in the business context.</p>'}</details>
 <details ${jobs.length?'open':''}><summary>Managed jobs · ${jobs.length}</summary>${jobs.length?tabular(['Job','Type','Relationship','Schedule','Next run'],jobs.map(j=>[j.name,j.kind,j.relationship,!j.interval_seconds?'On demand':j.enabled?'Every '+(j.interval_seconds/3600)+' hours':'Paused',j.enabled?time(j.next_run_at):'—'])):'<p>No directly related managed jobs were found.</p>'}<p class="table-note">Saved source/destination references; functions may access additional tables. Ask Calliope for deeper lineage or changes.</p></details>
 <details><summary>Acceleration</summary>${accel}</details>
 ${t.unavailable.length?`<p class="table-note">Unavailable sources: ${esc(t.unavailable.join(' · '))}. No fresh analysis was requested.</p>`:''}
 <footer><label>What would you like to know or change?<textarea maxlength="2000" rows="2" placeholder="Optional question for Calliope…"></textarea></label><div class="table-dialog-feedback" role="status"></div></footer></div></div>`;
}
async function open(ref){
 selected=ref;const ticket=++request;abort?.abort();abort=new AbortController();const signal=abort.signal;
 modal.innerHTML='<div class="metric-lens-shell"><header class="metric-lens-head"><div class="metric-lens-heading"><strong>Loading table observations…</strong></div><button class="metric-lens-close" data-table-close aria-label="Close">×</button></header><div class="metric-lens-content" role="status"></div></div>';if(!modal.open)modal.showModal();
 // Start both reads together, but metadata never waits for sample success.
 const preview=api('/sample?ref='+encodeURIComponent(ref),null,signal).then(data=>({data}),error=>({error}));
 try{const t=await api('?ref='+encodeURIComponent(ref),null,signal);if(ticket!==request||!modal.open)return;details(t);
 const result=await preview;if(ticket!==request||!modal.open)return;
 const host=modal.querySelector('.table-preview');host.innerHTML=result.error?'<p>Preview unavailable. Ask Calliope to inspect this table.</p>':`<p class="table-note">${esc(result.data.note)}${result.data.observed_at?' Read '+esc(time(result.data.observed_at))+'.':''}</p>${result.data.rows.length?tabular(result.data.columns,result.data.rows.map(r=>result.data.columns.map(c=>r[c]))):result.data.columns.length?'<p>No rows were returned.</p>':''}`;
 }catch(e){if(e.name!=='AbortError'&&ticket===request)modal.querySelector('.metric-lens-content').textContent=e.message;}
}
async function ask(button){
 button.disabled=true;const inModal=modal.contains(button),feedback=inModal?modal.querySelector('.table-dialog-feedback'):shell.querySelector('.table-feedback');
 if(feedback)feedback.textContent='Preparing table context…';
 try{const d=await api('/ask',{ref:button.dataset.tableAsk,question:inModal?modal.querySelector('textarea')?.value:undefined});location.assign(d.url);}catch(e){if(feedback)feedback.textContent=e.message;button.disabled=false;}
}
shell.addEventListener('click',e=>{const b=e.target.closest('[data-table-open],[data-table-ask],[data-table-refresh],[data-table-more]');if(!b)return;if(b.hasAttribute('data-table-open'))open(b.dataset.tableOpen);else if(b.hasAttribute('data-table-ask'))ask(b);else load(b.hasAttribute('data-table-more'));});
shell.querySelector('select').addEventListener('change',e=>{schema=e.target.value;load();});
modal.addEventListener('click',e=>{if(e.target===modal||e.target.closest('[data-table-close]'))modal.close();else {const b=e.target.closest('[data-table-ask]');if(b)ask(b);}});
modal.addEventListener('close',()=>{++request;abort?.abort();selected=null;});
function activate(view){nav.hidden=view!=='data';active=view==='data'&&window.galleryDataView==='tables';shell.hidden=!active;nav.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.dataView===window.galleryDataView)));if(active){paint();if(!loaded||filterKey()!==requestedKey)load();queueMicrotask(hero);}}
window.addEventListener('gallery-view',e=>activate(e.detail));window.addEventListener('gallery-data-view',()=>activate('data'));
nav.addEventListener('click',e=>{const b=e.target.closest('[data-data-view]');if(!b)return;window.galleryDataView=b.dataset.dataView;const url=new URL(location.href);url.searchParams.set('inventory',window.galleryDataView);history.replaceState({},'',url);window.dispatchEvent(new Event('gallery-data-view'));if(window.galleryDataView==='jobs'){$('#gallery-kicker').textContent='Data · Jobs';$('#gallery-title').innerHTML='The work behind <em>your data</em>.';}});
$('#q')?.addEventListener('input',()=>{if(active){clearTimeout(searchTimer);if(filterKey()!==requestedKey)searchTimer=setTimeout(()=>{if(active)load();},220);queueMicrotask(hero);}});
})();
