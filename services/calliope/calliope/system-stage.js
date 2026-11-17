/* Persistent live System surface. Chat/stage reconciliation never owns this DOM. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = value => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
  const dollars = value => value == null ? 'Unknown' : Number(value) === 0 ? '$0.00' : '$' + Number(value).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 6});
  const duration = value => value == null ? '—' : value >= 1000 ? number(value / 1000) + ' s' : number(value) + ' ms';
  const time = value => value ? new Date(value).toLocaleString(undefined, {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : '—';
  const refreshIcon = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 6.1A8 8 0 0 1 20 12M4 12a8 8 0 0 0 13.9 5.9"/></svg>';
  const refreshButton = (attribute,label) => `<button type="button" class="system-icon-button" ${attribute} aria-label="${label}" title="${label}" aria-busy="${busy}" ${busy?'disabled':''}>${refreshIcon}</button>`;
  const names = {embed:'Text embeddings', rerank:'Reranking', decide:'Decisions', extract:'Entity extraction', nli3:'Text entailment',
    document_parse:'Document parsing', image_embed:'Image embeddings', computer_decide:'Qwen/Cua chooser',
    timeseries_anomalies:'Time-series anomalies', timeseries_embed:'Time-series embeddings', timeseries_impute:'Time-series imputation', timeseries_similarity:'Time-series similarity',
    settings:'Settings', web_research:'Web research', web_scrape:'Web extraction', tabular_fit:'Tabular training', tabular_predict:'Tabular prediction', tabular_explain:'Tabular explanations'};
  const title = id => names[id] || String(id).replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
  const stateLabels = {configured:'Configured',ready:'Health OK',needs_setup:'Needs setup',unavailable:'Unavailable',missing_credentials:'Key missing',not_enabled:'Not enabled'};
  const badge = (state, label) => `<span class="system-badge" data-state="${escape(state)}"><i></i>${escape(label || stateLabels[state] || String(state || 'unknown').replaceAll('_',' '))}</span>`;
  const powerChip = c => c.power?`<span class="system-power" title="${escape(c.power.detail)}">${escape(c.power.label)}</span>`:'';
  const kv = (label, value) => `<div><dt>${escape(label)}</dt><dd>${escape(value ?? '—')}</dd></div>`;
  let host, context, sessionId = null, options, snapshot = null, busy = false, failure = '', generation = 0, timer;
  const views = new Map();
  let checksData=null, checksBusy=false, checksTimer, checksSequence=0;
  let codeEditors=[], codeModule;
  async function mountCheckSql() {
    const boxes=[...host.querySelectorAll('[data-check-sql]')];
    if(!boxes.length)return;
    codeModule ||= import('/meaning/logic/business-logic-code-editor.mjs');
    try {
      const {mountCodeEditor}=await codeModule;
      for(const box of boxes){
        if(!box.isConnected || box.dataset.mounted)continue;
        const value=box.textContent;box.replaceChildren();box.dataset.mounted='true';
        codeEditors.push(mountCodeEditor(box,{value,language:'sql',readOnly:true,label:'Test SQL · '+box.dataset.checkSql}));
      }
    } catch { codeModule=null; /* Keep readable SQL if the editor bundle is unavailable. */ }
  }
  function executionMarkup(def) {
    if(def.execution) return def.execution.length
      ? `<dl>${def.execution.map(e=>kv((e.kind==='llm'?'LLM':'Specialist')+' · '+e.provider,e.model)).join('')}</dl><p class="system-note">Configured step models.${def.execution.some(e=>e.kind==='llm')?' Test SQL may specify an LLM override.':''}</p>`
      : '<p class="system-note">No declared model call in this operator.</p>';
    return `<dl>${def.dependencies.map(d=>kv('Backend recorded · '+d.backend,d.route?.model_version||d.model||'Not recorded')).join('')}</dl><p class="system-note">This older run did not capture LLM routing. The generic operator default is not shown.</p>`;
  }
  const executionSignature = rows => JSON.stringify(rows?.map(e=>[e.step,e.kind,e.provider,e.model,e.source??null]));
  let settingsPanel, settingsObservation = null, settingsLoaded = false;
  let instanceInfo=null, instanceDraft='', instanceMessage='', instanceBusy=false, instanceEditorOpen=null;
  const scrollPositions=new Map(), disclosures=new Map();
  let view = {tab:'overview', window:'24h', selected:'', search:'', receipt:false, request:null,settingsSection:'providers',settingsProvider:'',settingsAgent:''};

  function ensure() {
    host = document.getElementById('system-stage');
    context = document.getElementById('system-stage-context');
  }
  function packageLabel(c) {
    if (c.package?.callable) return 'Installed';
    if (c.package?.deployment_status) return 'Installed · ' + c.package.deployment_status;
    if (c.option?.pack) return 'Optional install available';
    if (c.option?.compose) return 'Compose deployment available';
    if (c.option?.runtime?.includes('worker') || c.option?.runtime === 'optional-gpu') return 'Local worker not packaged';
    return 'No additional install required';
  }
  function focusLabel() {
    const c = snapshot?.capabilities.find(c => c.id === view.selected);
    return 'System · ' + (view.receipt ? 'Work & receipts' : title(view.tab)) + (!view.receipt && c && view.tab === 'capabilities' ? ' · ' + title(c.id) : '') + (view.receipt||['settings','updates','checks'].includes(view.tab)?'':' · ' + view.window);
  }
  function updateContext() {
    if (!context) return;
    context.hidden = !sessionId;
    const observation = view.receipt ? snapshot : view.tab==='settings' ? settingsObservation : view.tab==='checks' ? checksData : snapshot;
    const focus = view.receipt ? '' : view.tab==='settings' ? (view.settingsAgent || view.settingsProvider || (view.settingsSection==='agents'?'Models & voice':'Provider connections')) : view.tab==='checks'&&!view.receipt ? [view.checkOperator,checksData?.run_id?'run #'+checksData.run_id:''].filter(Boolean).join(' · ') : '';
    context.textContent = sessionId ? focusLabel() + (focus?' · '+focus:'') + (observation ? ' · observed ' + time(observation.observed_at) : ' · waiting for observation') : '';
  }
  function costNotes(s) {
    const unknown=(s.cost_unreported||0)+(s.cost_partial||0)+(s.cost_pending||0);
    const explanation=[s.cost_historical_unreported?number(s.cost_historical_unreported)+' imported calls without historical costs':null,
      s.cost_pending?number(s.cost_pending)+' pending':null,s.cost_partial?number(s.cost_partial)+' partial':null,
      s.cost_not_sent?number(s.cost_not_sent)+' rejected before a provider call':null].filter(Boolean).join(' · ');
    return `<small>${number(s.cost_reported)} charged · ${number(s.cost_no_charge)} no charge</small>
      <button type="button" class="system-cost-link" data-system-query="${unknown?'cost-gaps':'costs'}" title="${escape(explanation||'Reported provider charges; hardware costs are excluded.')}">${unknown?number(unknown)+' without cost':'Cost details'} ↗</button>`;
  }
  const componentStatus = s => ({reachable:'Online',available:'Online',ready:'Online',unavailable:'Needs attention',unconfigured:'Not configured',needs_setup:'Needs setup',not_enabled:'Off'}[s.state]||'Unknown');
  function componentRow(s) {
    const healthy=['reachable','available','ready'].includes(s.state);
    return `<details class="system-component" data-system-disclosure="component:${escape(s.id)}" ${!healthy&&s.state!=='not_enabled'?'open':''}><summary><strong>${escape(s.label)}</strong><span class="system-badge" data-state="${escape(s.state)}"><i></i>${componentStatus(s)}</span></summary><p>${escape(s.detail)}</p></details>`;
  }
  function stats() {
    const s = snapshot?.usage?.summary || {};
    return `<div class="system-stats">
      <div><span>Clover calls</span><strong>${number(s.calls)}</strong><small>${escape(view.window)} · received by the gateway</small></div>
      <div><span>Reported provider charges</span><strong>${dollars(s.known_cost_usd)}</strong>${costNotes(s)}</div>
      <button type="button" class="system-stat-link" data-system-query="errors"><span>Failed calls</span><strong>${number(s.errors)}</strong><small>Inspect failures in Data →</small></button>
      <div><span>Typical latency</span><strong>${duration(s.avg_ms)}</strong><small>Mean · p95 ${duration(s.p95_ms)}</small></div>
    </div>`;
  }
  const capabilityAvailability = c => c.availability || (c.enabled===false || c.state==='disabled'?'disabled':c.state==='unavailable'?'unavailable':['configured','ready'].includes(c.state)?'enabled':'needs_setup');
  const capabilityHealth = c => c.health || (c.state==='ready'?'passed':c.state==='unavailable'&&c.probe?'failed':c.health_scope==='route_only'?'route_only':c.state==='configured'&&snapshot.probed?'not_offered':'not_checked');
  const healthLabel = c => ({passed:c.health_scope==='provider'?'Provider check passed':'Worker check passed',failed:'Health check failed',route_only:'Route found · worker unverified',not_offered:'Readiness check missing',not_checked:c.enabled===false?'Health check skipped':'Health not checked'}[capabilityHealth(c)]);
  function healthSummary() {
    const caps=snapshot.capabilities.filter(c=>capabilityAvailability(c)!=='disabled');
    const count=state=>caps.filter(c=>capabilityHealth(c)===state).length;
    return `<p class="system-note system-health-summary"><b>Health checks</b> · ${count('passed')} passed · <span class="${count('failed')?'system-health-failed':''}">${count('failed')} failed</span> · ${count('route_only')} with route-only checks${count('not_offered')?' · '+count('not_offered')+' missing checks':''}${count('not_checked')?' · '+count('not_checked')+' not checked':''}</p>`;
  }
  function capabilityCounts() {
    const caps=snapshot.capabilities;
    const counts={enabled:0,unavailable:0,disabled:0,needs_setup:0};
    caps.forEach(c=>counts[capabilityAvailability(c)]++);
    return Object.entries({enabled:'Enabled',unavailable:'Unavailable',disabled:'Disabled',needs_setup:'Needs setup'})
      .map(([id,label])=>`<span><b>${counts[id]}</b> ${label}</span>`).join('');
  }
  function overview() {
    const caps = snapshot.capabilities;
    return `${stats()}${activity()}<div class="system-overview-grid"><div><section class="system-section"><header><div><span class="system-kicker">Your instance · ${snapshot.services.length} components</span><h3>System components</h3></div><button type="button" data-system-ask="overview">Ask Callie</button></header>
      <div class="system-services">${snapshot.services.map(componentRow).join('')}</div>
      </section>${connectionSummary()}</div><section class="system-section"><header><div><span class="system-kicker">Inference & specialist operations</span><h3>${caps.length} capabilities</h3></div>${refreshButton('data-system-probe','Recheck health')}</header>
      <div class="system-counts">${capabilityCounts()}</div>${healthSummary()}
      <p class="system-note">Checks verify worker readiness or hosted credentials and model availability. Legacy route-only checks cannot confirm worker health; use Checks for real SQL execution.</p>
      <button type="button" data-system-tab="capabilities">Explore all ${caps.length} capabilities →</button>
      <div class="system-pack-list"><h4>${snapshot.packages.length} installed add-ons</h4>${snapshot.packages.map(p=>`<div><span>${escape(p.title || p.pack)}<small>${caps.filter(c=>c.package?.pack===p.pack).length} ${caps.filter(c=>c.package?.pack===p.pack).length===1?'capability':'capabilities'}</small></span>${badge(p.callable?'callable':p.deployment_status || 'not_installed',p.callable?'Installed':'Needs attention')}</div>`).join('') || '<p>No local add-ons installed.</p>'}</div>
      </section></div>${ledger()}`;
  }
  function detail(c) {
    if (!c) return '<div class="system-detail-empty">Select a capability to inspect its model, health, and recent usage.</div>';
    const u = c.usage || {}, installed=Boolean(c.package?.callable);
    const evidence=c.health_detail || c.reason || (capabilityHealth(c)==='not_offered'?'No health check is available. Its SQL tests can verify execution.':'');
    return `<div class="system-kicker">Capability</div><h3>${escape(title(c.id))}</h3>
      <div class="system-detail-state">${badge(capabilityAvailability(c))}${powerChip(c)}</div>
      <p class="system-cap-health ${capabilityHealth(c)==='failed'?'system-health-failed':''}"><strong>${escape(healthLabel(c))}</strong>${evidence?`<span>${escape(evidence)}</span>`:''}</p>
      <div class="system-detail-actions"><button type="button" data-system-ask="capability">Ask Callie about this</button><button type="button" data-system-query="calls">All calls in Data ↗</button></div>
      <dl class="system-cap-usage">${kv('Calls · '+view.window,number(u.calls ?? 0))}${kv('Average latency',duration(u.avg_ms))}${kv('Reported charges',dollars(u.known_cost_usd))}${kv('Last call',time(u.last_call))}</dl>
      <dl>${kv('Model',c.model || c.model_version)}${c.model && c.model_version!==c.model?kv('Version',c.model_version):''}${c.dimensions?kv('Vector dimensions',c.dimensions):''}
      ${kv('Last successful call',time(u.last_success))}${kv('Last failed call',time(u.last_error))}</dl>
      ${!installed&&c.option?.reason?`<p class="system-note">${escape(c.option.reason)}</p>`:''}
      <details class="system-deployment-detail" data-system-disclosure="deployment:${escape(c.id)}"><summary>Deployment details</summary><dl>${kv('Capability ID',c.id)}${kv('Service',routeLabel(c.route))}${kv('Installation',packageLabel(c))}${c.option?.pack?kv('Package ID',c.option.pack):''}${c.health_code?kv('Check result',c.health_code):''}</dl></details>`;
  }
  function capabilities() {
    return `<div class="system-capabilities"><section class="system-cap-list"><div class="system-list-head"><span>${snapshot.capabilities.length} capabilities</span><input type="search" aria-label="Find a capability" data-system-search placeholder="Find a capability or model…" value="${escape(view.search)}"></div>
      <div class="system-counts system-cap-counts">${capabilityCounts()}</div>${healthSummary()}
      <div class="system-cap-scroll"><div class="system-cap-columns"><span>Capability / model</span><span>Status / health</span><span>Calls</span></div>
      ${snapshot.capabilities.map(c=>`<button type="button" class="system-cap-row ${c.id===view.selected?'selected':''}" data-capability="${escape(c.id)}" aria-pressed="${c.id===view.selected}" data-search="${escape((c.id+' '+title(c.id)+' '+c.model+' '+c.model_version).toLowerCase())}">
        <span><strong>${escape(title(c.id))} ${powerChip(c)}</strong><small>${escape(c.model || c.model_version)}</small>${c.route?'<small>'+escape(routeLabel(c.route))+'</small>':''}</span><span class="system-cap-evidence">${badge(capabilityAvailability(c))}<span class="${capabilityHealth(c)==='failed'?'system-health-failed':''}" title="${escape(c.health_detail || c.reason || 'Health checks do not run inference.')}">${escape(healthLabel(c))}</span></span><span class="system-number">${number(c.usage?.calls ?? 0)}</span></button>`).join('') || '<p class="system-empty">No inventory available. Check Clover status in Overview.</p>'}
      <p class="system-empty" data-system-no-matches hidden>No matching capabilities.</p></div></section><aside class="system-detail" aria-label="Selected capability">${detail(snapshot.capabilities.find(c=>c.id===view.selected))}</aside></div>`;
  }
  const routeLabel = route => ({'legacy-clover':'Remote service','hosted-provider':'Model API',worker:'Local service',unconfigured:'Needs setup'}[route?.source] || 'Service');
  function checkRoute(op) {
    if(!op.test_count)return 'No tests';
    if(!op.callable)return 'Needs setup';
    return '';
  }
  function checkCell(data,op,run) {
    const result=data.matrix.find(m=>m.operator===op.operator&&m.run_id===run.run_id);
    const obs=data.observations.find(m=>m.operator===op.operator&&m.run_id===run.run_id);
    const state=result?(result.passed===result.total?'pass':'fail'):obs?.status==='skipped'?(obs.display_state||'excluded'):!op.test_count?'no-tests':run.active_operator===op.operator&&run.status==='running'?'running':'unrun';
    const labels={pass:'Passed',fail:'Failed',needs_setup:'Setup',changed:'Changed',excluded:'Excluded','no-tests':'No tests',running:'Running',unrun:'Not run'};
    const text=result?result.passed+'/'+result.total:labels[state];
    const explanation=obs?.detail||({unrun:'This function was not tested in this run.','no-tests':'No tests are defined for this function.'}[state])||labels[state];
    return `<td><button type="button" data-check-cell="${run.run_id}" data-check-name="${escape(op.operator)}" data-check-state="${state}" aria-label="${escape(op.operator+' · run '+run.run_id+' · '+explanation)}" title="${escape(explanation)}">${escape(text)}</button></td>`;
  }
  function checkFailure(result,test) {
    if(!result||result.passed)return '';
    const expect=test?.expect;
    const expected=expect?({exact:'equal to '+expect.value,contains:'containing '+expect.value,regex:'matching '+expect.pattern,min:'at least '+expect.value,max:'at most '+expect.value,not_empty:'a nonempty value'}[expect.type]||JSON.stringify(expect)):result.expected||'the test expectation';
    return `<p class="system-check-error">${escape(result.error||'Expected '+expected+'; received '+(result.actual==null?'NULL':String(result.actual))+'.')}</p>`;
  }
  function checks() {
    const data=checksData;
    if(!data)return '<p class="system-note">Reading embedded SQL tests and recorded results…</p>';
    if(!data.available)return `<p class="system-note">${escape(data.message)}</p>`;
    const active=data.runs.find(r=>['queued','running'].includes(r.status));
    const run=data.selected_run||data.runs.find(r=>r.run_id===data.run_id);
    const ops=data.operators, runs=data.runs.slice(0,5).reverse();
    const visibleOps=ops.filter(o=>view.checkFilter==='all'?true:view.checkFilter==='no-tests'?!o.test_count:view.checkFilter==='unavailable'?!o.callable:view.checkFilter==='legacy'?o.dependencies.some(d=>d.route?.source==='legacy-clover'):o.test_count>0);
    const detail=data.detail, def=detail?.definition;
    const waiting={queued:'Queued',running:'Running',interrupted:'Interrupted',skipped:'Not run',not_in_run:'Not included in this run',never_run:'No recorded run',error:'Harness error'}[detail?.state] || 'Not run';
    const unmatched=(detail?.results||[]).filter(r=>!(def?.tests||[]).some(t=>(t.name||'<unnamed>')===r.test_name));
    const chosen=ops.find(o=>o.operator===view.checkOperator);
    const observation=data.observations.find(o=>o.run_id===data.run_id && o.operator===view.checkOperator);
    return `<section class="system-check-head"><div><span class="system-kicker">SQL function tests</span><h3>Checks</h3><p class="system-note">${ops.filter(o=>o.test_count).length} functions · ${number(ops.reduce((n,o)=>n+o.test_count,0))} tests · ${ops.filter(o=>!o.test_count).length} functions without tests.</p></div>
      <button type="button" data-check-run ${active||checksBusy?'disabled':''}>${active?'Running…':'Run available tests'}</button></section>
      <p class="system-note">Runs real SQL calls for configured services, including remote services. Model calls may incur charges.</p>
      <div class="system-check-status" role="status">${active?`Run #${active.run_id} · ${escape(active.active_operator||'Queued')} · you can leave this tab while it runs.`:run?`Run #${run.run_id} · ${escape(time(run.created_at))} · ${run.passed}/${run.total} passed · ${run.skipped} not run · ${escape(run.status)}${run.message?' · '+escape(run.message):''}`:'No recorded runs yet. Opening this view does not execute tests.'}</div>
      <div class="system-check-filter"><label>Show <select aria-label="Show operators" data-check-filter>${[['tests','With tests'],['all','All functions'],['no-tests','Without tests'],['unavailable','Needs setup'],['legacy','Remote services']].map(([id,label])=>`<option value="${id}" ${(view.checkFilter||'tests')===id?'selected':''}>${label}</option>`).join('')}</select></label><button type="button" data-check-latest>Latest results</button><input type="search" data-check-search aria-label="Find a SQL function" placeholder="Find a function…" value="${escape(view.checkSearch||'')}"><small><span data-check-count>${visibleOps.length}</span> shown · Latest ${runs.length} runs</small></div>
      <div class="system-check-workspace"><section class="system-check-matrix" aria-label="Operator check history">
        <table style="--check-runs:${runs.length}"><thead><tr><th>Function</th>${runs.map(r=>`<th><button type="button" data-check-history="${r.run_id}" aria-pressed="${r.run_id===data.run_id}" title="${escape(time(r.created_at))}">#${r.run_id}</button></th>`).join('')}</tr></thead><tbody>${visibleOps.map(o=>`<tr data-check-search-row="${escape(o.operator)}" class="${o.operator===view.checkOperator?'selected':''}"><th><button type="button" data-check-operator="${escape(o.operator)}" aria-pressed="${o.operator===view.checkOperator}"><strong>${escape(o.operator)}</strong><small>${o.test_count?o.test_count+(o.test_count===1?' test':' tests'):''}${!o.callable?' · Needs setup':!o.test_count?'No tests':''}</small></button></th>${runs.map(r=>checkCell(data,o,r)).join('')}</tr>`).join('')}<tr data-check-empty hidden><td colspan="${runs.length+1}" class="system-empty">No matching functions.</td></tr></tbody></table></section>
      <aside class="system-check-detail" aria-label="Selected SQL check"><button type="button" class="system-check-back" data-check-back>↑ Functions</button>${def?`<span class="system-kicker">${detail.definition_at_run?'Definition recorded for this run':'Current installed definition'}</span><h3>${escape(def.operator)}</h3><p class="system-note">${data.run_id?`Run #${data.run_id} · ${escape(detail.state==='complete'?'Completed':waiting)}`:'No recorded run'}</p>${checkRoute(def)?`<p class="system-note">${escape(checkRoute(def))}</p>`:''}
        <div class="system-detail-actions"><button type="button" data-check-run-one ${active||checksBusy||!chosen?.callable||!chosen?.test_count?'disabled':''}>Run this function</button><button type="button" data-system-query="checks" ${data.run_id?'':'disabled'}>Results in Data →</button><button type="button" data-system-ask="checks">Ask Callie</button></div>
        ${executionMarkup(def)}${detail.definition_at_run&&Array.isArray(detail.current_execution)&&executionSignature(def.execution)!==executionSignature(detail.current_execution)?`<details class="system-check-current"><summary>Current routing</summary>${executionMarkup({execution:detail.current_execution})}</details>`:''}<dl>${kv('Duration',duration(observation?.duration_ms))}${kv('SQL result cache','Bypassed by the harness; downstream cache state not established')}</dl>
        ${observation?.detail?`<p class="system-check-diagnostic">${escape(observation.detail)}</p>`:''}
        ${(def.tests||[]).map((t,index)=>{const result=detail.results.find(r=>r.test_name===(t.name||'<unnamed>'));return `<article class="system-check-case"><header><strong>${escape(t.name||'Unnamed test')}</strong><span data-check-state="${result?(result.passed?'pass':'fail'):'unrun'}">${result?(result.passed?'Passed':result.error?'Error':'Failed'):escape(waiting)}</span></header>${checkFailure(result,t)}${t.description?`<p>${escape(t.description)}</p>`:''}<div class="system-check-sql" data-check-sql="${escape(t.name||'Unnamed test')}"><pre>${escape(t.sql)}</pre></div><button type="button" class="system-test-open" data-check-query="${index}" aria-label="Open ${escape(t.name||'Unnamed test')} in Data">Open in Data ↗</button><dl>${kv('Expected',JSON.stringify(t.expect))}${result?kv('Actual',result.actual ?? 'NULL'):''}${result?.error?kv('Error',result.error):''}</dl></article>`;}).join('')||'<p class="system-note">No tests are defined for this function. This is untested, not a failed check.</p>'}${unmatched.map(r=>`<article class="system-check-case"><header><strong>${escape(r.test_name)}</strong><span data-check-state="${r.passed?'pass':'fail'}">${r.passed?'Passed':r.error?'Error':'Failed'}</span></header>${checkFailure(r)}<p class="system-note">Recorded result. This test is absent from the displayed definition.</p><dl>${kv('Expected',r.expected)}${kv('Actual',r.actual??'NULL')}${r.error?kv('Error',r.error):''}</dl></article>`).join('')}`:'<p>Select a function or a historical result to inspect its SQL and expected output.</p>'}</aside></div>`;
  }
  async function loadChecks() {
    if(!sessionId)return;
    const id=sessionId, seq=++checksSequence;
    try {
      const q=new URLSearchParams({session:id});
      if(view.checkRun)q.set('run_id',view.checkRun);
      if(view.checkOperator)q.set('operator',view.checkOperator);
      const data=await options.api('/api/calliope/system/checks?'+q);
      if(id!==sessionId||seq!==checksSequence)return;
      failure='';
      checksData=data;
      view.checkOperator=data.operator || data.detail?.definition?.operator || view.checkOperator;
      if(view.tab==='checks')render();
      clearTimeout(checksTimer);
      if(data.runs?.some(r=>['queued','running'].includes(r.status)))checksTimer=setTimeout(loadChecks,3000);
    }catch(error){if(id===sessionId&&seq===checksSequence){failure=error.message||'Checks could not be loaded.';render();}}
  }
  async function runChecks(single=false) {
    if(checksBusy)return;
    const id=sessionId;checksBusy=true;failure='';render();
    try {
      const data=await options.api('/api/calliope/system/checks',{method:'POST',headers:{'Content-Type':'application/json','X-Calliope-Admin':'1'},body:JSON.stringify({session_id:id,...(single?{operator:view.checkOperator}:{})})});
      if(id!==sessionId)return;
      view.checkRun=data.run_id;await loadChecks();
    }catch(error){if(id===sessionId)failure=error.message||'Checks could not be started.';}
    finally{if(id===sessionId){checksBusy=false;render();}}
  }
  function ledger() {
    return `<section class="system-ledger-status"><strong>Call logging</strong>${snapshot.usage.writers?.map(w=>`<span>${escape(w.instance_id)} ${badge(w.fresh && w.connected==='true'?'connected':'stale')} · queue ${number(w.queue_depth)} · dropped ${number(w.dropped_events)} · failed batches ${number(w.failed_batches)}</span>`).join('') || '<span>No writer heartbeat available.</span>'}<p>${escape(snapshot.usage.scope || snapshot.usage.detail || '')}</p></section>`;
  }
  let activityChart=null, activityResize=null, activityTheme=null, activityGeneration=0;
  function disposeActivity() {
    activityGeneration++;
    activityResize?.disconnect();activityResize=null;
    activityTheme?.disconnect();activityTheme=null;
    activityChart?.finalize();activityChart=null;
  }
  async function mountActivity() {
    const box=host?.querySelector('[data-system-activity]');
    if(!box || !snapshot?.usage?.daily?.length)return;
    const token=activityGeneration, rows=snapshot.usage.daily, period=view.window;
    const readout=host.querySelector('[data-activity-readout]');
    const values=rows.map(row=>({...row, bucket:Date.parse(row.bucket),
      interval:time(row.start_at||row.bucket)+(row.end_at?' – '+time(row.end_at):'')+(row.partial?' · partial interval':'')}));
    try {
      const embed=await options.loadChart();
      if(token!==activityGeneration || !box.isConnected)return;
      const css=getComputedStyle(box), accent=css.getPropertyValue('--workspace-accent').trim()||'#4c78a8';
      // Resolve CSS color expressions before passing them to Vega's SVG renderer.
      const probe=document.createElement('span');box.append(probe);
      const color=(value)=>{probe.style.color=value;return getComputedStyle(probe).color;};
      const ink=color(accent), fade=color(`color-mix(in srgb, ${ink} 0%, transparent)`);probe.remove();
      const spec={width:Math.max(160,Math.floor(box.clientWidth)),height:184,
        autosize:{type:'fit',contains:'padding',resize:true},padding:{left:0,right:8,top:8,bottom:0},
        data:{values},encoding:{x:{field:'bucket',type:'temporal',axis:{title:null,grid:false,labelOverlap:true,labelPadding:9,
          tickCount:box.clientWidth<450?4:7,format:period==='1h'?'%H:%M':period==='24h'?'%H:%M':'%b %d'}},
          y:{field:'calls',type:'quantitative',scale:{zero:true},axis:{title:null,tickCount:4,tickMinStep:1,format:'~s',domain:false,ticks:false,labelPadding:8,gridDash:[2,4]}}},
        layer:[
          {mark:{type:'area',interpolate:'monotone',color:{gradient:'linear',x1:0,y1:0,x2:0,y2:1,stops:[{offset:0,color:ink},{offset:1,color:fade}]},opacity:0.3}},
          {mark:{type:'line',interpolate:'monotone',color:ink,strokeWidth:2,strokeJoin:'round'}},
          {params:[{name:'activityHover',select:{type:'point',fields:['bucket'],nearest:true,on:'pointermove',clear:'pointerout'}}],
            mark:{type:'point',filled:true,color:ink,size:48},encoding:{opacity:{condition:{param:'activityHover',empty:false,value:1},value:0},
              tooltip:[{field:'interval',title:'Period'},{field:'calls',title:'Requests'}]}},
          {mark:{type:'rule',color:ink,strokeWidth:1,strokeDash:[3,3]},encoding:{y:{value:0},y2:{value:155},
            opacity:{condition:{param:'activityHover',empty:false,value:0.4},value:0}}}
        ]};
      const config=options.chartTheme();config.area={opacity:0.18};
      const settings={renderer:'svg',actions:false,config,
        tooltip:(_handler,_event,_item,value)=>{if(readout?.isConnected)readout.textContent=value?`${value.Period} · ${number(value.Requests)} requests`:'';}};
      if(window.vega?.expressionInterpreter){settings.ast=true;settings.expr=window.vega.expressionInterpreter;}
      const result=await embed(box,spec,settings);
      if(token!==activityGeneration || !box.isConnected){result.finalize();return;}
      activityChart=result;box.dataset.ready='true';
      activityResize=new ResizeObserver(()=>{
        if(!box.isConnected || token!==activityGeneration || !box.clientWidth)return;
        result.view.width(Math.max(160,Math.floor(box.clientWidth))).runAsync().catch(()=>{});
      });activityResize.observe(box);
      activityTheme=new MutationObserver(()=>{disposeActivity();mountActivity();});
      activityTheme.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','style']});
    } catch(error) {
      if(token===activityGeneration && box.isConnected){box.textContent='Chart unavailable. Inspect the calls in Data above.';box.dataset.ready='error';}
    }
  }
  function activity() {
    const usage=snapshot.usage, rows=usage.daily||[], available=usage.state==='available';
    return `<section class="system-section system-usage-activity"><header><div><h3>Requests over time</h3><small>${view.window==='1h'?'Per minute':view.window==='24h'?'Hourly':'Daily'} · ${escape(view.window)}</small></div><div class="system-activity-actions" aria-label="Inspect call records in Data"><button type="button" data-system-query="calls">All calls ↗</button><button type="button" data-system-query="errors">Failed calls ↗</button><button type="button" data-system-query="costs">Cost coverage ↗</button></div></header>
      <div class="system-activity-chart" data-system-activity role="group" aria-label="Requests over time. ${escape(rows.map(d=>time(d.bucket)+': '+d.calls+' requests').join('; '))}">${rows.length?'Loading chart…':available?'No calls in this period.':'Call history is unavailable.'}</div>
      ${rows.length?'<div class="system-activity-key"><span><i></i>Requests</span><small>Partial intervals at the edges</small></div><div class="system-activity-readout" data-activity-readout aria-live="polite"></div>':''}</section>`;
  }
  function updates() {
    const u=snapshot.updates||{}, components=u.components||[];
    const release=components.filter(c=>c.kind==='release'), extra=components.filter(c=>c.kind!=='release');
    const list=rows=>`<div class="system-image-list">${rows.map(c=>`<article class="system-image-row"><div><strong>${escape(c.name)}</strong><small>${c.target?'Release target':'Outside release manifest'}</small>${c.target?`<code>${escape(c.target)}</code>`:''}${c.reused_from?`<small>Build reused from ${escape(c.reused_from)}</small>`:''}</div><div>${c.installed?.length?c.installed.map(i=>`<div class="system-installed-image"><code>${escape(i.image)}</code><small>${escape(i.container)} · ${escape(i.host||'Instance host')} · ${escape(i.state||'Unknown state')}${i.health?' · '+escape(i.health):''}</small><small>${i.version?'Version label '+escape(i.version)+' · ':''}Image ${escape((i.image_id||'Not recorded').replace('sha256:','').slice(0,12))}</small><details><summary>Build details</summary><dl>${kv('Revision',i.revision||'Not labeled')}${kv('Image ID',i.image_id)}${kv('Registry digests',i.digests?.join('\n')||'Local image; no registry digest recorded')}</dl></details></div>`).join(''):`<span class="system-note">${u.captured_at?'Not observed on the inventoried host':'Inventory not connected'}</span>`}</div></article>`).join('')}</div>`;
    return `<section class="system-section system-updates"><header><div><span class="system-kicker">Deployment</span><h3>Images & updates</h3></div><button type="button" data-system-ask="updates">Ask Callie</button></header>
      <div class="system-update-summary"><span>Release <strong>${escape(u.release_version||'Unknown')}</strong></span><span>SQL engine <strong>${escape(u.engine_version||'Unknown')}</strong></span><button type="button" disabled title="Upgrade orchestration is not implemented yet">Upgrade all · coming later</button></div>
      <p class="system-note">Installed images beside the bundled release targets. New registry releases have not been checked. Upgrade controls are not enabled yet.</p>
      <p class="system-inventory-stamp">${u.captured_at?'Inventory captured '+escape(time(u.captured_at))+(u.stale?' · over 24 hours old':''):'No deployment inventory'}${u.warning?' · '+escape(u.warning):''}</p>
      <h4>Calliope release images <small>${release.length}</small></h4>${list(release)}
      ${extra.length?`<h4>Supporting services & local builds <small>${extra.length}</small></h4><p class="system-note">Components without a release target still need a place in the shipping manifest.</p>${list(extra)}`:''}</section>`;
  }
  function connectionSummary() {
    const c=snapshot?.connections;
    return `<section class="system-section system-connections-summary"><header><div><h3>MCP servers</h3><small>${number(c?.mcp_tools ?? 0)} discovered tools</small></div><button type="button" data-system-query="mcp">Inspect in Data ↗</button></header>
      ${c?.unavailable?'<p class="system-note">Connection inventory is unavailable.</p>':c?.servers?.length?`<div class="system-mcp-list">${c.servers.map(s=>`<article><div><strong>${escape(s.name)}</strong><span>${number(s.tools)} tools</span></div><p>${escape(s.transport)} · ${s.last_call?'Last call '+escape(time(s.last_call)):'No recorded calls'}${s.failed_calls_24h?` · <span class="system-health-failed">${number(s.failed_calls_24h)} failed in 24h</span>`:''}</p></article>`).join('')}</div>`:'<p class="system-note">No MCP servers registered.</p>'}</section>`;
  }
  function instanceConnection() {
    const ai=instanceInfo;
    return `<section class="system-instance-connection"><header><div><span class="system-kicker">Instance AI</span><h3>OpenRouter</h3></div>${ai?badge(ai.configured?'connected':'missing_credentials'):'<span>Reading connection…</span>'}</header>
      <p>The shared connection for Hermes chat and Clover: SQL AI, embeddings, decisions, images, voice, and research. Local model workers do not use this key.</p>
      ${ai?.editable?`<details class="system-instance-editor" ${(instanceEditorOpen??!ai.configured)?'open':''}><summary>${ai.configured?'Change key':'Connect OpenRouter'}</summary><label for="system-instance-key">${ai.configured?'Replace the instance key':'OpenRouter API key'}</label><div class="system-instance-form"><input id="system-instance-key" type="password" autocomplete="new-password" data-system-instance-key placeholder="${ai.configured?'Paste a replacement key':'Paste your OpenRouter key'}"><button type="button" data-system-instance-save ${instanceBusy||!instanceDraft?'disabled':''}>${instanceBusy?'Working…':'Check & save'}</button><button type="button" data-system-instance-check ${instanceBusy||!ai.configured?'disabled':''}>Check saved key</button></div><small>Saving validates the key, connects it to Hermes, and restarts Hermes. Clover uses it on new requests.</small></details>`:ai?'<p class="system-note">This deployment has not enabled shared key management yet.</p>':''}
      <p role="status" class="system-instance-status">${escape(instanceMessage)}</p></section>`;
  }
  function renderInstance() {
    const el=host?.querySelector('[data-system-instance]');if(!el)return;
    const editor=el.querySelector('.system-instance-editor');if(editor)instanceEditorOpen=editor.open;
    el.innerHTML=instanceConnection();
    const input=el.querySelector('[data-system-instance-key]');if(input)input.value=instanceDraft;
  }
  async function changeInstance(action) {
    if(instanceBusy || !sessionId)return;
    const id=sessionId;instanceBusy=true;instanceMessage='Validating OpenRouter and synchronizing Hermes…';renderInstance();
    try {
      const data=await options.api('/api/calliope/system/instance-ai',{method:'POST',headers:{'Content-Type':'application/json','X-Calliope-Admin':'1'},body:JSON.stringify({session_id:id,action,...(action==='save'?{api_key:instanceDraft}:{})})});
      if(id!==sessionId)return;
      instanceInfo=data;instanceMessage=data.message;if(action==='save')instanceDraft='';
      await options.loadSettings?.(false);
    } catch(error){if(id===sessionId)instanceMessage=error.message||'The connection could not be updated.';}
    finally {if(id===sessionId){instanceBusy=false;renderInstance();}}
  }
  async function openDetailQuery(kind) {
    try {
      const query=await options.api(`/api/calliope/system/detail-query?session=${encodeURIComponent(sessionId)}&kind=${encodeURIComponent(kind)}&window=${encodeURIComponent(view.window)}${kind==='checks'?'&run_id='+encodeURIComponent(checksData?.run_id||'')+'&operator='+encodeURIComponent(view.checkOperator||''):''}`);
      options.openQuery(query.sql,query.title);
    }catch(error){failure=error.message||'The detail query could not be opened.';render();}
  }

  function settings() {
    return `<section class="system-settings-intro"><div><span class="system-kicker">Instance settings</span><h3>Models & providers</h3><p>Manage the instance AI connection, optional chat accounts, and the models Callie and her specialists use.</p></div><button type="button" data-system-ask="settings">Ask Callie about these settings</button></section>
      <nav class="system-settings-tabs" aria-label="Settings sections"><button type="button" data-settings-section="providers" aria-current="${view.settingsSection==='providers'?'page':'false'}">Connections</button><button type="button" data-settings-section="agents" aria-current="${view.settingsSection==='agents'?'page':'false'}">Models & voice</button></nav>
      <p class="system-note">Callie sees saved settings. Unapplied edits, API keys, and sign-in codes stay in the form.</p>
      ${view.settingsSection==='providers'?'<div data-system-instance></div>':''}<div class="system-settings-slot" data-section="${view.settingsSection||'providers'}"></div>`;
  }
  function mountSettings() {
    const slot=host.querySelector('.system-settings-slot');
    if(!slot || !settingsPanel)return;
    slot.append(settingsPanel);
    const refresh=slot.querySelector('#provider-settings-refresh');
    if(refresh){refresh.innerHTML=refreshIcon;refresh.classList.add('system-icon-button');refresh.setAttribute('aria-label','Refresh models');refresh.title='Refresh models';}
    const heading=slot.querySelector('.provider-settings-section:not(.provider-agents-section) h3');if(heading)heading.textContent='Chat connections';
    renderInstance();
    if(!settingsLoaded){settingsLoaded=true;options.loadSettings?.(false).catch(()=>{settingsLoaded=false;});}
  }
  function openSettings() {
    if(!sessionId)return;
    view.tab='settings';view.receipt=false;render();
  }
  function observeSettings(value, id, ai) {
    if(id!==sessionId)return;
    settingsObservation=value;instanceInfo=ai||null;renderInstance();updateContext();
    const stamp=host.querySelector('.system-head time');
    if(view.tab==='settings' && stamp){stamp.textContent=time(value.observed_at);stamp.dateTime=value.observed_at;}
  }
  function settingsActivity(busy) {
    if(view.tab!=='settings' || !sessionId)return;
    const button=host.querySelector('[data-system-refresh]');
    if(button){button.disabled=busy;button.setAttribute('aria-busy',String(busy));}
  }
  function filterRows() {
    const query = view.search.trim().toLowerCase(); let shown = 0;
    host.querySelectorAll('[data-capability]').forEach(row=>{row.hidden=!row.dataset.search.includes(query);if(!row.hidden)shown++;});
    const empty=host.querySelector('[data-system-no-matches]');if(empty)empty.hidden=shown>0;
  }
  function filterChecks() {
    let shown=0;
    for(const row of host.querySelectorAll('[data-check-search-row]')){
      row.hidden=!row.dataset.checkSearchRow.toLowerCase().includes((view.checkSearch||'').toLowerCase());if(!row.hidden)shown++;
    }
    const count=host.querySelector('[data-check-count]');if(count)count.textContent=shown;
    const empty=host.querySelector('[data-check-empty]');if(empty)empty.hidden=shown>0;
  }
  function revealCheck() {
    if(view.tab!=='checks')return;
    const matrix=host.querySelector('.system-check-matrix'), detail=host.querySelector('.system-check-detail');
    if(matrix&&detail&&detail.getBoundingClientRect().top>=matrix.getBoundingClientRect().bottom)detail.scrollIntoView({block:'start'});
  }
  function render() {
    if (!host || !sessionId) return;
    settingsPanel ||= document.querySelector('.provider-settings-shell');
    const observed=view.receipt?snapshot:view.tab==='settings'?settingsObservation:view.tab==='checks'?checksData:snapshot;
    const active=settingsPanel?.contains(document.activeElement)?document.activeElement:null;
    if(settingsPanel?.parentElement!==document.getElementById('provider-settings-dialog'))document.getElementById('provider-settings-dialog')?.append(settingsPanel);
    for(const el of host.querySelectorAll('[data-scroll-memory]'))scrollPositions.set(el.dataset.scrollMemory,[el.scrollTop,el.scrollLeft]);
    for(const el of host.querySelectorAll('[data-system-disclosure]'))disclosures.set(el.dataset.systemDisclosure,el.open);
    document.body.classList.toggle('system-receipts',view.receipt);
    codeEditors.forEach(e=>e.destroy());codeEditors=[];disposeActivity();
    host.innerHTML = `<header class="system-head"><time datetime="${escape(observed?.observed_at||'')}" title="Last updated">${observed?escape(time(observed.observed_at)):'Reading your instance…'}</time><div class="system-head-actions">
      <label ${['settings','updates','checks'].includes(view.tab)?'hidden':''}><span class="sr-only">Usage period</span><select aria-label="Usage period" data-system-window ${busy?'disabled':''}>${Object.keys({ '1h':0,'24h':0,'7d':0,'30d':0 }).map(w=>`<option ${view.window===w?'selected':''}>${w}</option>`).join('')}</select></label>
      ${refreshButton('data-system-refresh','Refresh system')}</div></header>
      <div class="system-glass"><nav class="system-tabs" aria-label="System sections">${['overview','capabilities','checks','settings','updates'].map(tab=>`<button type="button" data-system-tab="${tab}" aria-current="${!view.receipt&&tab===view.tab?'page':'false'}">${title(tab)}</button>`).join('')}<button type="button" class="system-receipts-button" data-system-receipts aria-current="${view.receipt?'page':'false'}">Work & receipts <span>${number(options.receipts || 0)}</span></button></nav>
      <div class="system-feedback" role="status">${failure?escape(failure):snapshot?.warnings?.map(escape).join(' · ')||''}</div>
      <div class="system-body ${view.tab==='settings'?'system-settings-body':''}" data-system-scroll ${view.receipt?'hidden':''}>${view.tab==='settings'?settings():snapshot?(view.tab==='capabilities'?capabilities():view.tab==='checks'?checks():view.tab==='updates'?updates():overview()):`<div class="system-loading"><span></span><h3>${failure?'System is unavailable':'Reading capabilities and usage'}</h3><p>${failure?'You can retry without leaving this conversation.':'The conversation and its receipts stay here.'}</p></div>`}</div></div>`;
    mountSettings();mountCheckSql();mountActivity();
    for(const el of host.querySelectorAll('[data-system-scroll],.system-cap-scroll,.system-detail,.system-check-matrix,.system-check-detail')){
      const focus=el.matches('.system-detail')?view.selected:el.matches('.system-check-detail')?view.checkOperator+'/'+checksData?.run_id:el.matches('.system-settings-body')?view.settingsSection:'';
      const key=view.tab+'/'+el.className+'/'+focus;el.dataset.scrollMemory=key;
      const position=scrollPositions.get(key);if(position){el.scrollTop=position[0];el.scrollLeft=position[1];}
    }
    for(const el of host.querySelectorAll('[data-system-disclosure]'))if(disclosures.has(el.dataset.systemDisclosure))el.open=disclosures.get(el.dataset.systemDisclosure);
    settingsActivity(Boolean(options.settingsBusy?.()));
    if(active?.isConnected && view.tab==='settings')active.focus({preventScroll:true});
    filterRows();filterChecks();updateContext();
  }
  async function refresh(probe=true) {
    if (!sessionId || busy) return;
    const token = generation, id=sessionId;
    busy=true;failure='';render();
    try {
      const data=await options.api(`/api/calliope/system/snapshot?session=${encodeURIComponent(id)}&window=${view.window}&probe=${probe}`);
      if(token!==generation)return;
      snapshot=data;
      if(!data.capabilities.some(c=>c.id===view.selected))view.selected=data.capabilities[0]?.id||'';
    } catch(error) {
      if(token!==generation)return;
      if(error.status===403 || error.status===404) {snapshot=null;failure='System access is no longer available. Administrator membership is required.';}
      else failure=error.message||'The last observation could not be refreshed.';
    } finally {if(token===generation){busy=false;render();}}
  }
  function events() {
    host.addEventListener('toggle',e=>{if(e.target.isConnected&&e.target.matches('.system-instance-editor'))instanceEditorOpen=e.target.open;},true);
    host.addEventListener('focusin',e=>{
      if(view.tab!=='settings')return;
      const provider=e.target.closest('[data-provider-card]'), agent=e.target.closest('[data-provider-agent]');
      if(provider || agent){view.settingsProvider=provider?.dataset.providerCard||'';view.settingsAgent=agent?.dataset.providerAgent||'';updateContext();}
    });
    host.addEventListener('input',e=>{if(e.target.matches('[data-check-search]')){view.checkSearch=e.target.value;filterChecks();return;}if(e.target.matches('[data-system-instance-key]')){instanceDraft=e.target.value;host.querySelector('[data-system-instance-save]').disabled=instanceBusy||!instanceDraft;}if(e.target.matches('[data-system-search]')){view.search=e.target.value;filterRows();}});
    host.addEventListener('change',e=>{if(e.target.matches('[data-check-filter]')){view.checkFilter=e.target.value;render();return;}if(e.target.matches('[data-system-window]')){view.window=e.target.value;refresh();}});
    host.addEventListener('click',e=>{
      const button=e.target.closest('button');if(!button)return;
      if(button.hasAttribute('data-check-query')){const test=checksData?.detail?.definition?.tests?.[Number(button.dataset.checkQuery)];if(test?.sql)options.openQuery(test.sql,view.checkOperator+' · '+(test.name||'Test'));return;}
      if(button.hasAttribute('data-check-run')){runChecks();return;}
      if(button.hasAttribute('data-check-run-one')){runChecks(true);return;}
      if(button.hasAttribute('data-check-latest')){view.checkRun=null;view.checkOperator='';loadChecks();return;}
      if(button.hasAttribute('data-check-back')){host.querySelector('.system-check-matrix')?.scrollIntoView({block:'start'});return;}
      if(button.dataset.checkOperator){view.checkOperator=button.dataset.checkOperator;view.checkRun=null;loadChecks().then(revealCheck);return;}
      if(button.dataset.checkHistory){view.checkRun=button.dataset.checkHistory;view.checkOperator='';loadChecks();return;}
      if(button.dataset.checkCell){view.checkRun=button.dataset.checkCell;view.checkOperator=button.dataset.checkName;loadChecks().then(revealCheck);return;}
      if(button.dataset.systemQuery){openDetailQuery(button.dataset.systemQuery);return;}
      if(button.hasAttribute('data-system-instance-save')){changeInstance('save');return;}
      if(button.hasAttribute('data-system-instance-check')){changeInstance('check');return;}
      if(button.hasAttribute('data-system-refresh')){if(view.tab==='checks'){loadChecks();return;}if(view.tab==='settings'){if(!options.settingsBusy?.())options.loadSettings?.(true).catch(()=>{});}else refresh();return;}
      if(button.hasAttribute('data-system-probe')){refresh(true);return;}
      if(button.dataset.settingsSection){view.settingsSection=button.dataset.settingsSection;view.settingsAgent='';view.settingsProvider='';render();return;}
      if(button.dataset.systemTab){view.tab=button.dataset.systemTab;view.receipt=false;render();if(view.tab==='checks')loadChecks();else if(['overview','capabilities'].includes(view.tab))refresh(true);return;}
      if(button.hasAttribute('data-system-receipts')){view.receipt=true;render();return;}
      if(button.dataset.capability){view.selected=button.dataset.capability;view.request=null;render();return;}
      if(button.dataset.systemAsk){options.ask(button.dataset.systemAsk==='overview'?'Summarize this instance using the System observation: what is online, what needs attention, and what the usage numbers mean.':button.dataset.systemAsk==='checks'?`Explain the results for ${view.checkOperator}${checksData?.run_id?' in SQL test run #'+checksData.run_id:''}, including the recorded error or warning and what it means.`:button.dataset.systemAsk==='updates'?'Review the release and installed image inventory. Which components need release packaging or version tracking? Do not perform upgrades.':button.dataset.systemAsk==='settings'?'Help me understand these saved model and provider settings.':button.dataset.systemAsk==='request'?'Help me understand this Clover call and its cost.':`Help me understand ${title(view.selected)}: its current configuration, readiness, and usage.`);}
    });
  }
  function update(next) {
    ensure();if(!host)return;
    options=next;
    const active=next.admin && next.session?.workspace_kind==='system';
    const id=active?next.session.id:null;
    if(id===sessionId){if(active){const count=host.querySelector('.system-receipts-button span');if(count)count.textContent=number(next.receipts||0);}return;}
    if(sessionId)views.set(sessionId,{...view});
    disposeActivity();generation++;clearInterval(timer);clearTimeout(checksTimer);checksSequence++;checksData=null;checksBusy=false;sessionId=id;snapshot=null;settingsObservation=null;settingsLoaded=false;instanceInfo=null;instanceDraft='';instanceMessage='';instanceBusy=false;instanceEditorOpen=null;scrollPositions.clear();disclosures.clear();busy=false;failure='';
    settingsPanel ||= document.querySelector('.provider-settings-shell');
    if(settingsPanel)document.getElementById('provider-settings-dialog')?.append(settingsPanel);
    document.body.classList.toggle('system-workspace',!!id);document.body.classList.remove('system-receipts');
    host.hidden=!id;context.hidden=!id;
    const nav=document.getElementById('system-workspace-open');if(nav){nav.hidden=!next.admin;nav.setAttribute('aria-current',id?'page':'false');}
    if(!id){host.replaceChildren();updateContext();return;}
    view=views.get(id)||{tab:'overview',window:'24h',selected:'',search:'',receipt:false,request:null,settingsSection:'providers',settingsProvider:'',settingsAgent:''};
    if(!host.dataset.events){events();host.dataset.events='true';}
    refresh();if(view.tab==='checks')loadChecks();
    timer=setInterval(()=>{if(!document.hidden&&!busy&&view.tab!=='settings'&&!host.contains(document.activeElement))refresh();},60000);
  }
  function origin() {
    if(!sessionId || (!snapshot && !settingsObservation))return null;
    if(view.receipt)return snapshot?{kind:'system_workspace',snapshot_id:snapshot.id,tab:'receipts'}:null;
    if(view.tab==='settings')return {kind:'system_workspace',snapshot_id:snapshot?.id,tab:'settings',settings_snapshot_id:settingsObservation?.id,settings_section:view.settingsSection||'providers',settings_provider:view.settingsProvider||'',settings_agent:view.settingsAgent||''};
    if(!snapshot)return null;
    return {kind:'system_workspace',snapshot_id:snapshot.id,tab:view.tab,
      capability:view.tab==='capabilities'?view.selected:'',...(view.tab==='checks'?{check_run_id:checksData?.run_id,check_operator:view.checkOperator,check_filter:view.checkFilter||'tests',check_search:view.checkSearch||''}:{}),...(view.tab==='usage'&&view.request?view.request:{})};
  }
  function showReceipts() {
    if(sessionId && !view.receipt){view.receipt=true;render();}
  }
  window.CalliopeSystemStage={update,origin,refresh,showReceipts,openSettings,observeSettings,settingsActivity};
})();
