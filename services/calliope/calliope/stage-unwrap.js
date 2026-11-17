/* Artifact children remain a version-scoped projection, not Stage surfaces. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sourceLabel=q=>q.source==='captured'?'Observed query':q.source==='declared'?'Saved query':'Calculation';
  function metric(scalar){
    const d=scalar.display||{},v=scalar.value,number=typeof v==='number'?v:typeof v==='string'&&v.trim()!==''?Number(v):NaN;
    let value=v==null?'—':String(v);
    if(Number.isFinite(number)){
      const options={maximumFractionDigits:Math.min(8,Math.max(0,Number(d.decimals??d.precision??2)))};
      if(d.format==='percent'||d.format==='percentage')options.style='percent';
      if(d.currency){options.style='currency';options.currency=d.currency;}
      try{value=new Intl.NumberFormat(undefined,options).format(number);}catch{value=number.toLocaleString();}
    }
    return `<div class="unwrap-metric"><strong>${esc(d.prefix||'')}${esc(value)}${esc(d.suffix||'')}</strong>${d.unit?`<span>${esc(d.unit)}</span>`:''}</div>`;
  }
  function render(data,selected,showSql,renderSql,renderEngine,{previews=new Map(),visible=new Set(),renderResult,opened}={}){
    const items=data?.items||[],positions=new Map(),groups=[];
    const columns=Math.min(3,Math.max(1,Math.ceil(Math.sqrt(items.length))));
    let y=105;
    for(const kind of ['query','calculation']){
      const rows=items.filter(q=>q.kind===kind);if(!rows.length)continue;
      groups.push({label:kind==='query'?'Queries':'Calculations',y,count:rows.length});y+=38;
      rows.forEach((q,i)=>positions.set(q.id,{x:24+i%columns*372,y:y+Math.floor(i/columns)*302}));
      y+=Math.ceil(rows.length/columns)*302+26;
    }
    const width=Math.max(400,columns*372+20),height=Math.max(360,y);
    const current=items.find(q=>q.id===selected),openItem=items.find(q=>q.id===opened);
    const status=q=>{const p=previews.get(q.id);return !q.can_explore?q.unavailable_reason:p?.status==='error'?p.error:p?.status==='ready'?`${(p.value.result.rows||[]).length.toLocaleString()} rows${p.value.result.truncated?' · preview limit':''}`:p?.status==='loading'?'Reading data…':'Preview when visible';};
    const stamp=q=>{const p=previews.get(q.id)?.value;return `v${data.version} definition`+(p?` · ${p.context?.as_of?'Data as of '+p.context.as_of:'Results refreshed '+new Date(p.observed_at).toLocaleTimeString()}`:'');};
    const result=(q,expanded=false)=>{
      const p=previews.get(q.id);
      if(!q.can_explore)return `<p class="unwrap-result-status">${esc(q.unavailable_reason)}</p>`;
      if(p?.status==='ready')return p.value.scalar?metric(p.value.scalar):renderResult?.(q,p.value,expanded)||'';
      return `<div class="unwrap-result-status" role="status">${p?.status==='loading'?'<span class="unwrap-loading" aria-hidden="true"></span>':''}${esc(status(q))}</div>`;
    };
    const world=`<section class="canvas-unwrap-outline" style="width:${width}px;height:${height}px" aria-label="Inside ${esc(data?.title||'artifact')}">
      <header class="canvas-unwrap-title"><small>DASHBOARD · v${esc(data?.version)} · INSIDE</small><strong>${esc(data?.title)}</strong><span>${esc((data?.tables||[]).join(' · ')||'Saved query definitions')}</span></header>
      ${groups.map(g=>`<div class="canvas-unwrap-label" style="top:${g.y}px">${g.label} <span>${g.count}</span></div>`).join('')}
      ${items.map(q=>{const p=positions.get(q.id);return `<div class="canvas-card unwrap-card ${q.id===selected?'is-selected':''} ${q.can_explore?'':'is-unavailable'}" data-stage-key="unwrap:${esc(q.id)}" style="left:${p.x}px;top:${p.y}px">
        <span class="canvas-card-meta">${esc(sourceLabel(q))}${!q.can_explore?' · unavailable':''}</span><strong>${esc(q.title)}</strong>
        <div class="unwrap-card-result" inert aria-hidden="true">${visible.has(q.id)?result(q):''}</div>
        <span class="canvas-card-foot">${esc(status(q))}</span>
        <button type="button" class="unwrap-card-hit" data-unwrap-item="${esc(q.id)}" aria-pressed="${q.id===selected}" aria-label="${esc(q.title)}"></button>
      </div>`;}).join('')}
      ${!items.length?'<p class="canvas-empty">No query definitions retained yet.</p>':''}
    </section>`;
    const connected=current?.source_query_ids?.map(id=>items.find(q=>q.id===id)).filter(Boolean)||[];
    const actions=q=>`<button type="button" data-unwrap-explore="${esc(q.id)}" ${q.can_explore?'':'disabled'}>Explore in Data</button>
      <button type="button" data-unwrap-refresh="${esc(q.id)}" ${q.can_explore&&previews.get(q.id)?.status!=='loading'?'':'disabled'}>${previews.get(q.id)?.status==='error'?'Retry':'Refresh'}</button>
      <button type="button" data-unwrap-sql aria-expanded="${showSql}">${showSql?'Hide':'View'} SQL</button>`;
    const scope=q=>Object.keys(q.parameter_values||{}).length?`<p class="unwrap-parameters">Scope · ${Object.entries(q.parameter_values).map(([k,v])=>`${esc(k)}: ${esc(JSON.stringify(v))}`).join(' · ')}</p>`:'';
    const sql=q=>showSql?`<pre class="sql-view sql-code"><code>${renderSql(q.sql)}</code></pre>`:'';
    const details=current&&!openItem?`<aside class="canvas-selection unwrap-details" aria-label="Selected internal query">
      <div class="canvas-selection-head"><strong>${esc(current.title)}</strong>
        <button type="button" data-unwrap-open="${esc(current.id)}">Open</button>${actions(current)}
        <button type="button" data-unwrap-clear aria-label="Clear query selection">×</button></div>
      <p>${esc(current.description||current.formula||sourceLabel(current))}</p>
      <div class="unwrap-query-meta"><span>${esc(stamp(current))}</span>${renderEngine?.(previews.get(current.id)?.value?.result||current)||''}</div>
      ${scope(current)}
      ${connected.length?`<div class="canvas-connections"><span>Calculated from</span>${connected.map(q=>`<button type="button" data-unwrap-item="${esc(q.id)}">${esc(q.title)}</button>`).join('')}</div>`:''}
      ${sql(current)}
      <small>Explore in Data makes a private working copy. Preview results are limited to 200 rows.</small>
    </aside>`:'';
    const inspector=openItem?`<section class="canvas-inspector unwrap-inspector" role="region" aria-label="Open internal query" data-stage-key="unwrap-inspector:${esc(openItem.id)}">
      <header><strong>${esc(openItem.title)}</strong><button type="button" data-unwrap-close>Back to canvas ×</button></header>
      <div class="unwrap-inspector-meta"><span>${esc(stamp(openItem))}</span>${renderEngine?.(previews.get(openItem.id)?.value?.result||openItem)||''}${actions(openItem)}</div>
      <div class="unwrap-inspector-result">${result(openItem,true)}</div>
      <div class="unwrap-inspector-context"><p>${esc(openItem.description||openItem.formula||'')}</p>${scope(openItem)}${sql(openItem)}</div>
    </section>`:'';
    const captureNote=['loading','busy'].includes(data.capture_status)?`Discovering queries in saved v${data.version}…`:
      data.capture_status==='error'?'Query discovery could not finish. Saved definitions are still available.':data.note;
    return {world,width,height,positions,details,inspector,count:items.length,note:captureNote};
  }
  globalThis.CalliopeStageUnwrap={render};
})();
