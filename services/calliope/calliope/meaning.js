import {neighborhood, collection, connectionsCount} from './meaning-explore.mjs';
import {exactNumber, timestamp, dataValue, sqlMarkup, historyChart, matchingFields, numericHistory, label} from './meaning-present.mjs';
import {W,H,types,clamp,initialVisible,groupedGraph,positions,cleanLayout,fit,zoomAt,curve,edgeLabels,hiddenNeighbors} from './meaning-layout.mjs';
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const array=v=>Array.isArray(v)?v:[],text=v=>v==null?'Not recorded':typeof v==='string'?v:Array.isArray(v)?v.map(text).join(' · '):typeof v==='object'?Object.entries(v).map(([k,x])=>`${k.replaceAll('_',' ')}: ${text(x)}`).join('\n'):String(v);
const date=v=>v?String(v).replace('T',' ').replace(/\.\d+(?=[+Z-]|$)/,''):'not recorded';
const number=v=>typeof v==='number'?new Intl.NumberFormat(undefined,{notation:Math.abs(v)>=10000?'compact':'standard',maximumFractionDigits:2}).format(v):text(v);
const kindName=k=>({relation:'Source',cube:'Cube',group:'Collapsed group',idea:'Metric idea',artifact:'App',area:'Business area',metric:'Metric',breakdown:'Breakdown'}[k]||k);
const button=(action,label,attrs='')=>`<button type="button" data-action="${esc(action)}" ${attrs}>${esc(label)}</button>`;
const state={view:'explore',collectionKind:'',collectionQuery:'',collectionLens:'all',trail:[],area:'',selected:'',edge:null,graph:{nodes:[],edges:[]},visible:new Set(),layout:cleanLayout(),busy:false,stale:false,catalog:false,catalogMode:'objects',kind:'',detail:null,detailBusy:false,detailError:'',expanded:false,pins:[],pinsBusy:false,discussing:false,request:0,detailRequest:0,controller:null,detailController:null,detailKey:'',detailScrollKey:'',saveTimer:0,drag:null,suppressClick:false,layouts:{},handoff:null};
const owner=document.body.dataset.meaningOwner,storageKey=`calliope.meaning.map.v3:${owner}`;
try{const saved=JSON.parse(localStorage.getItem(storageKey)||'{}');if(saved&&typeof saved==='object'&&!Array.isArray(saved))state.layouts=saved;}catch{}
function save(){if(state.area)state.layout.shown=[...state.visible];state.layouts[state.area]=state.layout;clearTimeout(state.saveTimer);state.saveTimer=setTimeout(()=>{try{localStorage.setItem(storageKey,JSON.stringify(state.layouts))}catch{notice('Map placement could not be saved on this device.')}},250);}
function notice(message=''){const el=$('map-notice');el.textContent=message;el.hidden=!message;}
let toastTimer;function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4500);}
async function api(path,body,signal){const r=await fetch(path,{credentials:'same-origin',headers:{Accept:'application/json',...(body?{'Content-Type':'application/json'}:{})},method:body?'POST':'GET',body:body?JSON.stringify(body):undefined,signal});let data={};try{data=await r.json()}catch{}if(!r.ok){const e=new Error(data.error?.message||`Calliope request failed (${r.status})`);e.status=r.status;throw e;}return data;}
const node=id=>state.graph.nodes.find(n=>n.id===(id??state.selected));
const displayGraph=()=>groupedGraph(state.graph.nodes,state.graph.edges,state.visible);
const selected=()=>state.detail?.node?.id===state.selected?state.detail.node:node();
function rememberUrl(){const q=new URLSearchParams();if(state.area)q.set('area',state.area);if(state.view!=='explore')q.set('view',state.view);if(state.selected&&state.area)q.set('node',state.selected);history.replaceState({},'',`/meaning${q.size?'?'+q:''}`);}
function prepare(){const ids=new Set(state.graph.nodes.map(n=>n.id));state.visible=new Set([...state.visible].filter(id=>ids.has(id)));if(!state.visible.size)state.visible=state.layout.shown?new Set(state.layout.shown.filter(id=>ids.has(id))):initialVisible(state.graph.nodes,!state.area);if(!ids.has(state.selected))state.selected=state.graph.nodes[0]?.id||'';if(state.area&&state.selected)state.visible.add(state.selected);const ps=positions(displayGraph().nodes,!state.area);for(const [id,p0] of Object.entries(ps)){if(state.layout.positions[id])continue;const p=[...p0];while(Object.values(state.layout.positions).some(old=>Math.abs(old[0]-p[0])<301&&Math.abs(old[1]-p[1])<H+1))p[1]+=230;state.layout.positions[id]=p;}}
async function openArea(area,focus='',url=true){if(state.graph.nodes.length)save();state.controller?.abort();state.detailController?.abort();state.area=area;state.graph={nodes:[],edges:[]};state.selected=focus||(area?`area:${area}`:'');state.edge=null;state.detail=null;state.detailKey='';state.visible=new Set();state.layout=cleanLayout(state.layouts[area]);state.catalog=false;state.kind='';state.collectionKind='';state.collectionQuery='';state.collectionLens='all';state.trail=[];$('collection-search').value='';$('collection-lens').value='all';$('area-search').value='';$('object-search').value='';render();await loadGraph({fit:!state.layouts[area]||(!!focus&&!state.layout.shown?.includes(focus))});if(url)rememberUrl();}
async function loadGraph(options={}){
 const req=++state.request;state.controller?.abort();state.controller=new AbortController();state.busy=true;notice();renderToolbar();
 try{const q=new URLSearchParams({area:state.area,limit:String(state.layout.limit)});if(state.selected)q.set('focus',state.selected);const data=await api('/api/calliope/meaning/graph?'+q,undefined,state.controller.signal);if(req!==state.request)return;state.graph=data;state.stale=false;prepare();if(!state.area)filterAreas();render();if(options.fit)requestAnimationFrame(()=>{fitMap();});save();loadDetail();}
 catch(e){if(req!==state.request||e.name==='AbortError')return;state.stale=true;if([401,403,404].includes(e.status)){state.graph={nodes:[],edges:[]};state.visible.clear();state.detail=null;state.detailController?.abort();state.detailKey='';}notice(`${e.message}${state.graph.nodes.length?' · Showing last received map.':''}`);render();}
 finally{if(req===state.request){state.busy=false;renderToolbar();renderMap();}}
}
function filterAreas(){const q=$('area-search').value.trim().toLowerCase();state.visible=new Set(state.graph.nodes.filter(n=>`${n.title} ${n.description}`.toLowerCase().includes(q)).map(n=>n.id));}
function fitMap(){state.layout=fit(state.layout,new Set(displayGraph().nodes.map(n=>n.id)),$('map').clientWidth,$('map').clientHeight);save();renderMap();}
function select(id,locate=false){if(!node(id))return;if(state.selected&&state.selected!==id){state.trail.push(state.selected);state.trail=state.trail.slice(-8);}state.selected=id;state.edge=null;state.visible.add(id);prepare();if(locate){const p=state.layout.positions[id],z=state.layout.zoom;state.layout.pan=[$('map').clientWidth/2-(p[0]+W/2)*z,$('map').clientHeight/2-(p[1]+H/2)*z];}save();rememberUrl();loadDetail();renderMap();renderCatalog();renderDetail();renderExplorer();renderTrace();}
async function loadDetail(force=false){const n=node();const key=state.area&&n?`${state.area}|${n.id}|${state.graph.as_of}`:'';if(!force&&key===state.detailKey)return;state.detailKey=key;state.detailController?.abort();const req=++state.detailRequest;state.detail=null;state.detailError='';state.detailBusy=false;if(!key||!['relation','cube','metric'].includes(n.kind)){renderDetail();return;}state.detailController=new AbortController();state.detailBusy=true;renderDetail();try{const data=await api('/api/calliope/meaning/inspect',n.reference,state.detailController.signal);if(req===state.detailRequest)state.detail=data;}catch(e){if(req===state.detailRequest&&e.name!=='AbortError')state.detailError=e.message;}finally{if(req===state.detailRequest){state.detailBusy=false;renderDetail();}}}
function spark(series){const values=array(series).filter(Number.isFinite);if(values.length<2)return '';const lo=Math.min(...values),hi=Math.max(...values),d=values.map((v,i)=>`${i?'L':'M'}${i*75/(values.length-1)},${hi===lo?12.5:25-(v-lo)*25/(hi-lo)}`).join(' ');return `<svg viewBox="0 0 75 25" aria-hidden="true"><path d="${d}"/></svg>`;}
function nodeSummary(n){if(n.kind==='relation')return `<div class="node-description">${esc(n.description)}</div>${n.preview?`<div class="node-sample">${esc(number(n.preview.value))} profiled rows · ${esc(date(n.preview.at))}</div>`:''}`;if(n.preview)return `<div class="node-reading"><span class="node-value">${esc(number(n.preview.value))}</span>${spark(n.series)}</div><div class="node-sample">${esc(n.preview.label)} · ${esc(date(n.preview.at))}</div>`;return `<div class="node-description">${esc(n.description)}</div>`;}
function renderMap(){if(state.view!=='map')return;const map=$('map'),{zoom:z,pan}=state.layout,w=map.clientWidth,h=map.clientHeight,display=displayGraph(),displayIds=new Set(display.nodes.map(n=>n.id));
 $('nodes').innerHTML=display.nodes.map(n=>{const p=state.layout.positions[n.id];if(!p)return '';const x=pan[0]+p[0]*z,y=pan[1]+p[1]*z;if(x+W*z<0||y+H*z<0||x>w||y>h)return '';return `<button class="map-node ${esc(n.kind)} ${n.id===state.selected?'selected':''} " data-node="${esc(n.id)}" aria-pressed="${n.id===state.selected}" aria-label="${esc(n.title)} · ${esc(kindName(n.kind))}" style="left:${x}px;top:${y}px;transform:scale(${z})"><span class="node-heading" data-drag-node="${esc(n.id)}"><i></i>${esc(kindName(n.kind))}<em>${esc(n.status)}</em></span><span class="node-title">${esc(n.title)}</span><span class="node-summary">${nodeSummary(n)}</span><span class="node-subtitle">${esc(n.subtitle)}</span></button>`;}).join('');
 const trans=p=>[pan[0]+p[0]*z,pan[1]+p[1]*z];const edges=display.edges.filter(e=>state.layout.positions[e.from]&&state.layout.positions[e.to]);
 $('connections').innerHTML=edges.map(e=>{const [a,b,c,d]=curve(state.layout.positions[e.from],state.layout.positions[e.to]).map(trans);return `<path data-edge="${esc(e.id)}" class="${[e.from,e.to].includes(state.selected)||state.edge===e.id?'active':''} ${['unverified','implied','contradicted'].includes(e.state)?'suggested':''}" d="M${a} C${c} ${d} ${b}"><title>${esc(e.label)} · ${esc(e.state)}</title></path>`;}).join('')+edgeLabels(edges,state.layout,displayIds,state.selected,state.edge,w,h).map(e=>{const[x,y,width,height]=e.rect;return `<g data-edge="${esc(e.id)}" role="button" tabindex="0" aria-label="${esc(e.label)} relationship"><rect x="${x}" y="${y}" width="${width}" height="${height}"/><text x="${x+width/2}" y="${y+height/2}">${esc(e.label)}</text></g>`;}).join('');
 $('map-caption').textContent=`${state.visible.size} objects on map · ${state.graph.nodes.length-state.visible.size} in collections · zoom stops at a readable scale · drag to explore`;$('zoom-level').textContent=`${Math.round(z*100)}%`;
 $('map-empty').hidden=state.visible.size>0;$('map-empty').innerHTML=`<h2>${state.busy?'Finding your business areas…':state.stale?'Meaning is unavailable':state.graph.nodes.length?'No matching areas':'Meaning is still taking shape'}</h2><p>${state.stale?'Use Refresh to try again.':state.graph.nodes.length?'Try another search.':'Published areas will appear as Calliope recognizes your company’s data.'}</p>`;
}
function renderToolbar(){$('areas-back').hidden=!state.area;$('area-search').hidden=!!state.area;$('map-title').textContent=state.area?state.graph.nodes.find(n=>n.kind==='area')?.title||'Meaning':'How your business connects';$('map-freshness').textContent=state.busy?'Refreshing…':state.stale?'Last received map':'Published Meaning';$('refresh').disabled=state.busy;$('browse').hidden=!state.area;$('reset-view').hidden=!state.area;$('pins').textContent=`Pinned${state.pins.length?' · '+state.pins.length:''}`;$('pins').setAttribute('aria-expanded',state.catalog&&state.catalogMode==='pins');$('browse').setAttribute('aria-expanded',state.catalog&&state.catalogMode==='objects');}
function catalogMatches(){const q=$('object-search').value.trim().toLowerCase();return state.graph.nodes.filter(n=>n.kind!=='area'&&(!state.kind||n.kind===state.kind)&&`${n.title} ${n.description} ${n.source||''} ${n.formula||''}`.toLowerCase().includes(q)).sort((a,b)=>(types.findIndex(t=>t[0]===a.kind)-types.findIndex(t=>t[0]===b.kind))||a.title.localeCompare(b.title));}
function renderCatalog() {
 $('catalog').hidden=!state.catalog;if(!state.catalog)return;
 const scrollKey=[state.area,state.catalogMode,state.kind,$('object-search').value].join('|'),scroll=state.catalogScrollKey===scrollKey?$('catalog-list').scrollTop:0;state.catalogScrollKey=scrollKey;
 const pinned=state.catalogMode==='pins';$('catalog-title').textContent=pinned?'Pinned to Home':'Object library';$('object-search').hidden=pinned;$('catalog-types').hidden=pinned;$('catalog-footer').innerHTML='';
 if(pinned) {
  $('catalog-count').textContent=`${state.pins.length} private reference${state.pins.length===1?'':'s'} · shared with desktop`;
  $('catalog-list').innerHTML=state.pins.length?state.pins.map(p=>`<article class="catalog-card"><div class="catalog-kicker"><span class="kind-dot ${esc(p.reference.node.split(':')[0])}"></span>${esc(kindName(p.reference.node.split(':')[0]))}</div><h3>${esc(node(p.reference.node)?.title||p.reference.node.replace(/^[^:]*:/,'').replaceAll('_',' '))}</h3><p class="pin-area">${esc(p.reference.area.replace(/^subject:/,'').replaceAll('_',' '))}</p><div class="catalog-actions">${button('open-pin','Open object →',`data-pin="${esc(p.id)}"`)}${button('unpin','Remove',`data-pin="${esc(p.id)}" aria-label="Remove pin for ${esc(p.reference.node)}"`)}</div></article>`).join(''):'<div class="panel-empty"><span class="empty-symbol">◇</span><p>Keep useful objects close.</p><small>Select an object and choose Pin to Home. Its live reference appears here and on desktop.</small></div>';
  $('catalog-list').scrollTop=scroll;return;
 }
 $('catalog-types').innerHTML=[['','All'],...types].map(([kind,title])=>button('kind',title,`data-kind="${kind}" aria-pressed="${state.kind===kind}"`)).join('');
 const matches=catalogMatches(),catalog=array(state.graph.catalog).filter(c=>!state.kind||c.kind===state.kind),loaded=catalog.reduce((s,c)=>s+c.loaded,0),available=catalog.reduce((s,c)=>s+c.available,0);
 $('catalog-count').innerHTML=`<strong>${matches.length}</strong> matching <span>${loaded} loaded / ${catalog.some(c=>c.available_is_lower_bound)?'≥ ':''}${available} available</span>`;
 $('catalog-list').innerHTML=matches.map(n=>`<article class="catalog-card ${state.visible.has(n.id)?'on-map':''} ${state.selected===n.id?'selected':''}"><div class="catalog-kicker"><span class="kind-dot ${esc(n.kind)}"></span>${esc(kindName(n.kind))}${statusPill(n.status)}</div><h3>${esc(n.title)}</h3>${n.kind==='metric'&&n.preview?`<div class="catalog-reading">${dataValue(n.preview.value)}<span>${esc(n.preview.label)}</span></div>`:''}<p class="catalog-description">${esc(n.description||n.subtitle||'')}</p>${n.source?`<code class="catalog-source">${esc(n.source)}</code>`:''}<div class="catalog-actions">${button('show',state.visible.has(n.id)?'Locate →':'Add to map +',`data-id="${esc(n.id)}"`)}${state.visible.has(n.id)?button('hide','Hide',`data-id="${esc(n.id)}" aria-label="Hide ${esc(n.title)} from the map"`):'<span>In library</span>'}</div></article>`).join('')||'<div class="panel-empty"><p>No loaded objects match.</p><small>Try a different search or load more metadata.</small></div>';
 $('catalog-footer').innerHTML=catalog.some(c=>c.can_load)?button('load-more','Refresh catalog'):`<p>Complete authorized neighborhood. Search covers every object in this area.</p>`;
 if(state.kind)$('catalog-footer').innerHTML+=button('browse-kind','Read complete collection',`data-kind="${esc(state.kind)}"`)+button('expand-kind','Arrange category on map');
 $('catalog-list').scrollTop=scroll;
}
function link(id,label){return node(id)?button('show',label,`data-id="${esc(id)}" class="detail-link"`):`<p class="detail-hint">${esc(label)} · not in the loaded catalog</p>`;}
function pill(value, tone='') {
 return `<span class="status-pill ${esc(tone)}">${esc(value)}</span>`;
}
function statusPill(value) {
 return pill(value||'Recorded', ['governed','verified','published'].includes(value)?'confirmed':['implied','suggested','unverified','provisional'].includes(value)?'tentative':'');
}
function section(title, content, hint='', extra='') {
 return `<section class="dossier-section ${extra}"><header><h3>${esc(title)}</h3>${hint?`<span>${esc(hint)}</span>`:''}</header>${content}</section>`;
}
function prose(value) {
 return `<p class="dossier-prose">${esc(value||'No interpretation recorded yet.')}</p>`;
}
function facts(rows) {
 return `<dl class="fact-grid">${rows.filter(([,v])=>v!==undefined&&v!==null&&v!=='').map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${dataValue(v)}</dd></div>`).join('')}</dl>`;
}
function note(title, value) {
 return `<aside class="context-note"><span class="note-mark">i</span><div><strong>${esc(title)}</strong>${prose(value)}</div></aside>`;
}
const sourceId=s=>node('cube:'+s)?'cube:'+s:'relation:'+s;
function sourceLinks(sources) {
 return `<div class="source-links">${array(sources).map(s=>link(sourceId(s),node(sourceId(s))?.title||s)).join('')||'<p class="not-recorded">No sources recorded.</p>'}</div>`;
}
function codePanel(title, source, copy=false) {
 return `<section class="code-panel"><header><span>${esc(title)}</span>${copy?button('copy-sql','Copy SQL'):''}</header><pre><code>${sqlMarkup(source)}</code></pre></section>`;
}
function questions(title, values, caution=false) {
 return section(title, array(values).length?`<ol class="question-list ${caution?'cautions':''}">${values.map((v,i)=>`<li><span>${caution?'?':String(i+1).padStart(2,'0')}</span><div>${dataValue(v)}</div></li>`).join('')}</ol>`:'<p class="not-recorded">None recorded.</p>',`${array(values).length} recorded`);
}
function objectDetails(n,d) {
 const tab=state.detailTab;
 if(tab==='definition'&&n.kind==='cube')return `${codePanel('Registered cube SQL',d.sql||'',true)}${section('Declared row grain',prose(n.grain))}${section('Source lineage',sourceLinks(n.sources))}`;
 if(tab==='definition')return `${section('Calculation scope',facts([['Grain',d.grain||n.subtitle],['Window',d.window],['Definition version',n.version==null?null:'v'+n.version],['Default arguments',Object.keys(d.defaults||{}).length?d.defaults:'{} — no overrides required']]))}
   ${n.formula?codePanel('Registered formula',n.formula):''}${d.sql?codePanel('Registered SQL',d.sql,true):''}
   ${section('Declared sources',sourceLinks(n.sources))}${d.grain_columns?.length?section('Grouping fields',dataValue(d.grain_columns)):''}
   ${section('Definition notes',prose(n.description))}${note('Read-only definition','This is the stored definition. Opening it does not execute SQL. Daily history contains only this definition version at its canonical defaults. Explicit default arguments and an empty call mean the same thing; other scopes remain separate.')}`;
 if(tab==='observations')return observationTable(n,d);
 if(tab==='fields')return fieldDictionary(n,d);
 if(n.kind==='metric')return `${array(n.review).length?note('Definition needs review',n.review.join(' ')):''}<div class="metric-overview"><section class="metric-reading"><div class="eyebrow">${esc(n.preview?.label||'Saved reading')}</div>
   <div class="reading-value">${n.preview?dataValue(n.preview.value):'<span class="not-recorded">No saved value</span>'}</div>${n.preview?timestamp(n.preview.at):''}
   <div class="reading-meta">${pill('Definition v'+n.version)}${statusPill(n.status)}</div><p class="panel-footnote">Recorded evidence, not a new calculation.</p></section>
   <section class="metric-history">${state.detailBusy&&!state.detail?'<div class="panel-loading">Loading saved history…</div>':d.observations_restricted?note('Restricted history','Saved aggregate observations are unavailable for this database role.'):historyChart(d.observations)}</section></div>
   <div class="dossier-columns">${section('What it measures',prose(d.why||n.description))}${section('Scope & sources',facts([['Grain',d.grain||n.subtitle],['Window',d.contract?.window||d.window],['Population',d.contract?.population],['Time basis',d.contract?.time_basis],['Unit',d.contract?.unit],['Canonical defaults',Object.keys(d.defaults||n.defaults||{}).length?(d.defaults||n.defaults):'{} — no overrides required']])+sourceLinks(n.sources))}</div>
   ${n.formula?codePanel('Formula',n.formula):''}`;
 if(n.kind==='cube')return `${section('Reusable data model',prose(n.description))}${section('Declared grain',prose(n.grain))}${section('Source lineage',sourceLinks(n.sources))}<div class="dossier-columns">${section('Dimensions',dataValue(n.dimensions))}${section('Numeric fields',dataValue(n.measures))}</div>${note('Aggregation rules','Numeric fields are catalog hints, not proof of additivity. Use declared measures; ratios, distinct counts and snapshots require appropriate aggregation.')}${section('Refresh',dataValue(n.refresh))}${n.association==='shared source'?note('Related through shared sources','This cube shares a source with this area. Its fact population can belong to another business subject.'):''}`;
 if(n.kind==='relation') {
   if(state.detailBusy&&!state.detail)return '<div class="panel-loading">Loading source evidence and field dictionary…</div>';
   const fields=array(d.fields), roles=[...new Set(fields.map(f=>f.role||'unclassified'))];
   return `<div class="source-identity"><span class="object-glyph">▤</span><div><span class="eyebrow">Source relation</span><code>${esc(n.source)}</code></div></div>${section('What this source represents',prose(n.description))}
    <div class="summary-strip"><div><strong>${fields.length}${d.fields_more?'+':''}</strong><span>Recorded fields</span></div><div><strong>${roles.length}</strong><span>Field roles</span></div>${n.preview?`<div><strong>${esc(exactNumber(n.preview.value))}</strong><span>Profiled rows</span></div>`:''}</div>
    ${section('Field dictionary',`<div class="role-filters">${roles.map(r=>button('field-role',`${label(r)} · ${fields.filter(f=>(f.role||'unclassified')===r).length}`,`data-role="${esc(r)}"`)).join('')}</div><p class="panel-footnote">Explore field types, roles, and recorded interpretations.</p>${button('detail-tab','Browse fields →','data-tab="fields" class="inline-action"')}`)}
    ${section('Provenance',facts([['Recorded basis',n.basis]])+`<div class="provenance-time"><span>Metadata updated</span>${timestamp(n.updated_at)}</div>`)}
    ${note('Metadata only','Row counts are profile metadata, not a business metric. Source rows are not loaded in this view.')}`;
 }
 if(n.kind==='area') {
   const catalog=array(state.graph.catalog);
   return `${section('Business context',prose(n.description))}<div class="summary-strip area-summary">${catalog.filter(c=>['relation','cube','metric','artifact'].includes(c.kind)).map(c=>`<div><strong>${c.available_is_lower_bound?'≥ ':''}${esc(c.available)}</strong><span>${esc(types.find(t=>t[0]===c.kind)?.[1]||c.kind)}</span></div>`).join('')}</div>
    <div class="dossier-columns">${questions('Questions to explore',n.questions)}${questions('Gaps to resolve',n.gaps,true)}</div>`;
 }
 if(n.kind==='artifact')return `${section('Purpose',prose(n.description))}${section('Published experience',facts([['Revision',n.subtitle],['Renderer',n.runtime||'Not recorded']]))}
   ${section('Dependencies in this area',`<div class="dependency-list">${array(n.dependencies).map(d=>`<article><span class="dependency-kind">${esc(d.kind==='table'?'Source':'Metric')}</span>${link((d.kind==='table'?sourceId(d.object_ref):'metric:'+d.object_ref),node((d.kind==='table'?sourceId(d.object_ref):'metric:'+d.object_ref))?.title||d.object_ref)}<small>${esc(d.source)}</small></article>`).join('')||'<p class="not-recorded">No matching dependencies.</p>'}</div>`)}
   ${note('Declared dependencies','These are the recorded dependencies matching this area, not a complete app manifest.')}`;
 if(n.kind==='breakdown')return `${section('Analysis to explore',prose(n.description))}<div class="dossier-columns">${section('Group by',dataValue(n.dimensions))}${section('Measure',dataValue(n.measures))}</div>${n.caveat?note('Before interpreting this',n.caveat):''}${note('Suggested analysis','Ask Callie to refine this analysis. No calculation has run.')}`;
 return `${section('Why it may help',prose(n.description))}${section('Proposed scope',facts([['Grain',n.subtitle],['Population',n.proposal?.population],['Unit',n.proposal?.unit],['Time basis',n.proposal?.time_basis],['Window',n.proposal?.window],['Defaults',n.proposal?.defaults]]))}${n.formula?codePanel('Proposed formula',n.formula):''}${note('A definition to develop','This is a suggested metric, not a registered definition. Promote opens a discussion with its evidence pinned.')}`;
}
function observationTable(n,d) {
 if(d.observations_restricted)return note('Restricted history','Saved aggregate observations are unavailable for this database role.');
 const rows=array(d.observations);
 if(state.detailBusy&&!state.detail)return '<div class="panel-loading">Loading observation receipts…</div>';
 return `${section('Observation ledger',`<p class="panel-footnote">Definition v${esc(n.version)} · canonical defaults ${esc(JSON.stringify(d.observation_scope||{}))} · last saved snapshot per UTC day · latest 24 days with receipts. Dates are shown in your local time zone; hover for the original timestamp.</p>`,`${rows.length} saved`)}
  ${rows.length?`<div class="data-table-wrap"><table class="data-table observations-table"><thead><tr><th scope="col">Recorded value</th><th scope="col">Data as of</th><th scope="col">Saved</th></tr></thead><tbody>${rows.map(o=>`<tr><td class="observation-value">${dataValue(o.value)}</td><td>${timestamp(o.data_as_of)}</td><td>${timestamp(o.observed_at)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="panel-empty"><p>No observation receipts for this definition and parameter scope.</p></div>'}`;
}
function fieldRows(fields) {
 const matches=matchingFields(fields,state.fieldQuery,state.fieldRole);
 return `<div class="table-summary" role="status">${matches.length} of ${fields.length} recorded fields${state.fieldRole?' · '+esc(label(state.fieldRole)):''}</div>${matches.length?`<div class="data-table-wrap"><table class="data-table fields-table"><thead><tr><th scope="col">Field / type</th><th scope="col">Role</th><th scope="col">Recorded meaning</th></tr></thead><tbody>${matches.map(f=>`<tr><td><code>${esc(f.col_name)}</code><span class="type-badge">${esc(f.data_type||'Unknown type')}</span></td><td>${pill(label(f.role||'unclassified'),'field-role')}<small class="field-basis">${esc(f.basis||'No basis recorded')}</small></td><td>${f.what?dataValue(f.what):'<span class="not-recorded">No interpretation recorded</span>'}</td></tr>`).join('')}</tbody></table></div>`:'<div class="panel-empty"><p>No fields match these filters.</p><small>Try another field name, role, type, or meaning.</small></div>'}`;
}
function fieldDictionary(n,d) {
 const fields=array(d.fields),roles=[...new Set(fields.map(f=>f.role||'unclassified'))].sort();
 return `<div class="dictionary-tools"><label class="field-search"><span>Search the field dictionary</span><input id="field-search" type="search" placeholder="Field, type, or meaning…" value="${esc(state.fieldQuery||'')}" autocomplete="off"></label><div class="role-filters" aria-label="Filter fields by role">${[['','All roles'],...roles.map(r=>[r,label(r)])].map(([r,t])=>button('field-role',t,`data-role="${esc(r)}" aria-pressed="${(state.fieldRole||'')===r}"`)).join('')}</div></div>
  <div id="field-results">${state.detailBusy&&!state.detail?'<div class="panel-loading">Loading recorded fields…</div>':fieldRows(fields)}</div><p class="panel-footnote">${d.fields_more?'Showing the first 80 recorded fields. ':''}Roles and meanings are recorded interpretations. No source rows are loaded.</p>`;
}
function relationshipDetails(e) {
 return `${section('Recorded relationship',`<div class="relationship-chain"><div><span>From</span>${link(e.from,node(e.from)?.title||e.from)}</div><span class="chain-arrow">→</span><div><span>To</span>${link(e.to,node(e.to)?.title||e.to)}</div></div><div class="relationship-caption">${pill(e.label)}${statusPill(e.state)}</div>`)}
  ${section('Evidence',dataValue(e.evidence||{}))}${note('What this establishes','This is the recorded basis for the connection. A declared dependency does not verify a business interpretation.')}`;
}
function renderConnections(n) {
 const connections=state.graph.edges.filter(e=>e.from===n.id||e.to===n.id),hidden=hiddenNeighbors(state.graph.edges,n.id,state.visible),groups=new Map();
 for(const e of connections){const other=e.from===n.id?e.to:e.from,k=node(other)?.kind||'other';if(!groups.has(k))groups.set(k,[]);groups.get(k).push({e,other});}
 const names={area:'Business areas',relation:'Sources',cube:'Cubes',metric:'Metrics',artifact:'Apps',idea:'Metric ideas',breakdown:'Breakdowns',other:'Other objects'};
 return `<header class="connections-header"><div><span class="eyebrow">Neighborhood</span><h3>Connections <span>${connections.length}</span></h3></div>${button('trace',`Trace relationships`,'class="inline-action"')}</header>
  ${[...groups].map(([kind,items])=>`<section class="connection-group"><h4><span class="kind-dot ${esc(kind)}"></span>${esc(names[kind])}<span>${items.length}</span></h4>${items.map(({e,other})=>`<article class="connection-row ${state.edge===e.id?'selected':''}"><div class="connection-main">${link(other,node(other)?.title||other)}<p><span class="connection-direction" title="${e.from===n.id?'Outgoing relationship':'Incoming relationship'}">${e.from===n.id?'↗':'↙'}</span>${esc(e.label)} <span>· ${esc(e.state)}</span></p></div>${button('evidence','↗',`data-edge="${esc(e.id)}" class="connection-evidence" aria-label="Evidence for ${esc(node(other)?.title||other)}: ${esc(e.label)}" title="Inspect relationship evidence"`)}</article>`).join('')}</section>`).join('')||'<p class="panel-footnote">No connections in the loaded neighborhood.</p>'}
  <p class="panel-footnote connection-note">Complete recorded connections within this authorized area. Shared-source associations are distinct from direct lineage. Inspect a relationship for its evidence.</p>`;
}
function renderDetail() {
 const n=selected(),panel=$('details');panel.hidden=!state.area||!n;if(panel.hidden)return;
 if(state.detailViewKey!==n.id){state.detailViewKey=n.id;state.detailTab='overview';state.fieldQuery='';state.fieldRole='';}
 const tabs=[['overview','Overview']];
 if(n.kind==='metric')tabs.push(['observations','Observations'],['definition','Definition']);
 if(['relation','cube'].includes(n.kind))tabs.push(['fields','Fields']);if(n.kind==='cube')tabs.push(['definition','Definition']);
 if(state.edge)tabs.push(['relationship','Relationship']);
 tabs.push(['connections','Connections']);
 if(!tabs.some(([key])=>key===state.detailTab))state.detailTab='overview';
 panel.classList.toggle('expanded',state.expanded);$('meaning-workspace').classList.toggle('reading',state.expanded);panel.dataset.tab=state.detailTab;
 const scrollKey=[state.selected,state.edge||'',state.detailTab].join('|'),same=scrollKey===state.detailScrollKey,scroll=same?$('detail-content').scrollTop:0,connectionScroll=same?$('detail-connections').scrollTop:0;
 const searchFocus=document.activeElement?.id==='field-search',selection=searchFocus?[document.activeElement.selectionStart,document.activeElement.selectionEnd]:null;
 state.detailScrollKey=scrollKey;
 const pinned=state.pins.some(p=>p.reference.area===n.reference.area&&p.reference.node===n.id);
 $('detail-header').innerHTML=`<div class="detail-heading"><div class="object-kicker"><span class="kind-dot ${esc(n.kind)}"></span>${esc(kindName(n.kind))}${statusPill(n.status)}</div><h2 title="${esc(n.title)}">${esc(n.title)}</h2></div>
  <div class="detail-actions">${button('discuss','Ask Callie',`data-intent="ask" class="primary-action" ${state.discussing?'disabled':''}`)}${button('pin',pinned?'✓ Pinned':'Pin to Home',pinned||state.pinsBusy?'disabled':'')}${button('discuss','Promote…',`data-intent="promote" title="Discuss promoting this definition" ${state.discussing?'disabled':''}`)}${button('discuss','Disagree…',`data-intent="disagree" title="Discuss a correction" ${state.discussing?'disabled':''}`)}${n.kind==='artifact'?`<a class="open-app" href="/apps/${encodeURIComponent(n.slug)}" target="_blank" rel="noopener">Open app ↗</a>`:''}${button('expand-detail',state.expanded?'Close reading view':'Reading view',`aria-expanded="${state.expanded}" title="${state.expanded?'Restore the map':'Give details more space'}"`)}</div>`;
 $('detail-tabs').innerHTML=tabs.map(([key,title])=>`<button type="button" role="tab" id="detail-tab-${key}" data-action="detail-tab" data-tab="${key}" aria-selected="${state.detailTab===key}" aria-controls="${key==='connections'?'detail-connections':'detail-content'}" tabindex="${state.detailTab===key?0:-1}">${esc(title)}${key==='fields'&&state.detail?`<span>${array(state.detail.detail?.fields).length}</span>`:''}${key==='observations'&&state.detail?`<span>${array(state.detail.detail?.observations).length}</span>`:''}</button>`).join('')+`<span class="detail-provenance">${esc(n.basis)}</span>`;
 $('detail-content').setAttribute('aria-labelledby','detail-tab-'+(state.detailTab==='connections'?'overview':state.detailTab));
 const edge=state.graph.edges.find(e=>e.id===state.edge);
 $('detail-content').innerHTML=(state.detailError?`<div class="detail-error">${esc(state.detailError)} ${button('retry-detail','Retry details')}</div>`:'')+(state.detailTab==='relationship'&&edge?relationshipDetails(edge):objectDetails(n,state.detail?.detail||{}));
 $('detail-connections').innerHTML=renderConnections(n);
 $('detail-connections').setAttribute('role',state.detailTab==='connections'?'tabpanel':'complementary');
 if(state.detailTab==='connections')$('detail-connections').setAttribute('aria-labelledby','detail-tab-connections');
 else $('detail-connections').removeAttribute('aria-labelledby');
 $('detail-content').scrollTop=scroll;$('detail-connections').scrollTop=connectionScroll;
 if(searchFocus&&$('field-search')){$('field-search').focus({preventScroll:true});$('field-search').setSelectionRange(...selection);}
}
function render(){renderToolbar();renderView();renderCatalog();renderDetail();renderMap();renderExplorer();renderTrace();}
function openCollection(kind='') {state.collectionKind=kind;state.collectionQuery='';state.collectionLens='all';$('collection-search').value='';$('collection-lens').value='all';setView('explore');}
function setView(view) {
 state.view=['explore','trace','map'].includes(view)?view:'explore';
 if(!state.area&&state.view==='trace')state.view='explore';
 state.catalog=false;render();rememberUrl();
 if(state.view==='map')requestAnimationFrame(()=>{fitMap();if(state.area&&node()?.kind!=='area')select(state.selected,true);});
}
function renderView() {
 if(!state.area&&state.view==='trace')state.view='explore';
 $('meaning-workspace').dataset.view=state.view;
 $('meaning-workspace').classList.toggle('has-selection',!!state.area&&!!selected());
 $('map').hidden=state.view!=='map';$('explorer').hidden=state.view!=='explore';$('trace').hidden=state.view!=='trace';
 for(const b of document.querySelectorAll('[data-action=view]')){b.setAttribute('aria-pressed',b.dataset.view===state.view);b.disabled=b.dataset.view==='trace'&&!state.area;}
 $('view-description').textContent={explore:'Find useful definitions, evidence, and things to investigate.',trace:'Follow the recorded inputs and consumers of one object.',map:'A spatial overview. Open a collection to read all its objects.'}[state.view];
 $('fit').hidden=state.view!=='map';document.querySelector('.map-footer').hidden=state.view!=='map';
}
function objectRow(n) {
 const g=neighborhood(state.graph,n.id),inputs=connectionsCount(g.inputs),uses=connectionsCount(g.consumers);
 const scope=n.kind==='breakdown'?`Measures: ${array(n.measures).join(' · ')}. Group by: ${array(n.dimensions).join(' · ')}`:n.kind==='relation'?(array(n.facets).length?'Recorded dimensions: '+n.facets.join(' · '):n.source):n.kind==='idea'?(n.formula||n.subtitle):n.contract?.window||n.grain||n.subtitle||n.description;
 return `<article class="explore-object ${n.id===state.selected?'selected':''}" data-object="${esc(n.id)}">
  <div class="object-description"><div class="object-kicker"><span class="kind-dot ${esc(n.kind)}"></span>${esc(kindName(n.kind))}${statusPill(n.status)}${array(n.review).length?'<span class="review-mark">Scope needs review</span>':''}</div>
  ${button('inspect-object',n.title,`data-id="${esc(n.id)}" class="object-name" aria-label="Inspect ${esc(n.title)}"`)}
  <p>${esc(n.description||scope)}</p>${n.source?`<code>${esc(n.source)}</code>`:''}</div>
  <div class="object-facts">${n.kind==='metric'&&n.preview?`<div class="collection-value">${esc(exactNumber(n.preview.value))}${spark(n.series)}</div><span class="eyebrow">${esc(n.preview.label)}</span>${timestamp(n.preview.at,true)}`:`<span class="eyebrow">${n.kind==='relation'?'Explore by':n.kind==='artifact'?'Published version':['idea','breakdown'].includes(n.kind)?'Proposed calculation':'Recorded grain / scope'}</span><p>${esc(scope||'Not recorded')}</p>`}</div>
  <div class="object-links"><span>${inputs} input${inputs===1?'':'s'} · ${uses} consumer${uses===1?'':'s'}</span>${g.context.length?`<small>${connectionsCount(g.context)} contextual connection${connectionsCount(g.context)===1?'':'s'}</small>`:''}${button('trace-object','Trace →',`data-id="${esc(n.id)}" aria-label="Trace ${esc(n.title)}"`)}</div></article>`;
}
function renderExplorer() {
 if(state.view!=='explore')return;
 const root=state.graph.nodes.find(n=>n.kind==='area');
 document.querySelector('.collection-tools').hidden=!state.area;
 if(!state.area){$('explorer-intro').innerHTML='<div class="explore-lead"><span class="eyebrow">Business knowledge</span><h2>Start with an area of your business.</h2><p>See what is measured, which data supports it, and where the interpretation still needs work.</p></div>';
 $('collection-results').innerHTML=`<div class="area-library">${state.graph.nodes.filter(n=>state.visible.has(n.id)).map(n=>`<article><span class="eyebrow">Business area</span>${button('open-area',n.title,`data-area="${esc(n.area)}" class="object-name"`)}<p>${esc(n.description)}</p><small>${esc(n.subtitle)}</small></article>`).join('')}</div>`;return;}
 const review=collection(state.graph,{lens:'review'}).length;
 $('explorer-intro').innerHTML=root?`<div class="explore-lead"><span class="eyebrow">Recorded understanding</span><p>${esc(root.description)}</p><div class="lead-actions">${button('inspect-object','Read context & open questions',`data-id="${esc(root.id)}"`)}${review?button('review-collection',`${review} definitions or analyses to develop`):''}</div></div>`:'';
 $('collection-types').innerHTML=[['','All objects'],...types].map(([kind,title])=>{const count=state.graph.nodes.filter(n=>n.kind!=='area'&&(!kind||n.kind===kind)).length;return `<button data-action="collection-kind" data-kind="${kind}" aria-pressed="${state.collectionKind===kind}">${esc(title)} <span>${count}</span></button>`;}).join('');
 renderExplorerResults();
}
function renderExplorerResults() {
 const nodes=collection(state.graph,{kind:state.collectionKind,query:state.collectionQuery,lens:state.collectionLens,selected:state.selected});
 const total=state.graph.nodes.filter(n=>n.kind!=='area').length;
 $('collection-summary').textContent=`${nodes.length} of ${total} objects${state.collectionLens==='connected'?' · connected to '+(node()?.title||'selection'):state.collectionLens==='review'?' · proposals and definitions with recorded review needs':''} · select to inspect, trace to follow the evidence`;
 $('collection-results').innerHTML=types.map(([kind,title])=>{const items=nodes.filter(n=>n.kind===kind);return items.length?`<section class="object-collection"><header><h2>${esc(title)}</h2><span>${items.length}</span><p>${esc({relation:'Source data and its recorded meaning',cube:'Reusable facts with an explicit row grain',metric:'Saved measures at their defined default scope',artifact:'Published experiences that use these objects',idea:'Proposed measures, awaiting a usable definition',breakdown:'Questions and comparisons to explore'}[kind])}</p></header>${items.map(objectRow).join('')}</section>`:'';}).join('')||'<div class="panel-empty">No objects match this search and filter.</div>';
}
function traceEntry({node:n,edge,incoming}) {
 return `<article class="trace-entry"><span class="eyebrow">${esc(kindName(n.kind))}</span>${button('inspect-object',n.title,`data-id="${esc(n.id)}" class="object-name"`)}<p>${esc(n.subtitle||n.description)}</p><div class="trace-edge">${button('evidence',`→ ${edge.label}`,`data-edge="${esc(edge.id)}" title="Inspect the evidence for this relationship"`)}${statusPill(edge.state)}</div></article>`;
}
function renderTrace() {
 if(state.view!=='trace')return;const n=selected();if(!n)return;
 const g=neighborhood(state.graph,n.id);
 if(n.kind==='area'){$('trace').innerHTML=`<header class="trace-heading"><div><span class="eyebrow">Business context</span><h2>How objects relate to ${esc(n.title)}</h2></div></header><p class="dossier-prose">Select an object below to follow its recorded inputs and consumers. Topic membership and shared sources are shown with their recorded evidence.</p><section class="trace-context"><div>${g.context.map(traceEntry).join('')}</div></section>`;return;}
 const column=(key,title,hint)=>`<section class="trace-column"><header><span class="eyebrow">${esc(hint)}</span><h2>${title}<span>${connectionsCount(g[key])}</span></h2></header><div class="trace-items">${g[key].map(traceEntry).join('')||'<p class="trace-empty">No direct dependencies recorded in this area.</p>'}</div></section>`;
 $('trace').innerHTML=`<header class="trace-heading"><div><span class="eyebrow">Follow the evidence</span><h2>Where it comes from. What it supports.</h2></div>${button('view','Back to collection','data-view="explore"')}</header>
  ${state.trail.length?`<nav class="trace-trail" aria-label="Recently followed objects"><span>Trail</span>${state.trail.map(id=>node(id)?button('trail',node(id).title,`data-id="${esc(id)}"`):'').join('<span>›</span>')}</nav>`:''}
  <div class="trace-flow">${column('inputs','Inputs','Recorded lineage')}<section class="trace-focus"><div class="object-kicker"><span class="kind-dot ${esc(n.kind)}"></span>${esc(kindName(n.kind))}${statusPill(n.status)}</div><h2>${esc(n.title)}</h2>${n.kind==='metric'&&n.preview?`<div class="collection-value">${esc(exactNumber(n.preview.value))}</div><small>${esc(n.preview.label)}</small>`:''}<p>${esc(n.description)}</p><dl><dt>Scope / grain</dt><dd>${esc(n.contract?.window||n.grain||n.subtitle||'Not recorded')}</dd></dl>${array(n.review).length?`<div class="trace-review">${esc(n.review.join(' '))}</div>`:''}${button('expand-detail','Read full evidence','class="inline-action"')}</section>${column('consumers','Consumers','Recorded lineage')}</div>
  <section class="trace-context"><header><h2>Context & associations <span>${connectionsCount(g.context)}</span></h2><p>Topic membership, shared sources, and suggested relationships. These do not establish data lineage.</p></header><div>${g.context.map(traceEntry).join('')||'<p class="trace-empty">No contextual relationships recorded.</p>'}</div></section>`;
}
async function loadPins(){try{state.pins=array((await api('/api/calliope/meaning/pins')).pins);renderToolbar();renderCatalog();renderDetail();}catch(e){if([401,403].includes(e.status))state.pins=[];toast('Pins could not refresh: '+e.message);}}
async function pin(remove){if(state.pinsBusy)return;state.pinsBusy=true;renderDetail();try{await api('/api/calliope/meaning/pins',remove?{operation:'remove',id:remove}:{operation:'pin',reference:selected().reference});await loadPins();toast(remove?'Pin removed.':'Pinned to your Home.');}catch(e){toast(e.message)}finally{state.pinsBusy=false;renderDetail();}}
async function discuss(intent){const n=selected();if(!n||state.discussing)return;state.discussing=true;renderDetail();const identity=JSON.stringify([n.reference,intent,state.edge]);if(state.handoff?.identity!==identity)state.handoff={identity,key:crypto.randomUUID()};try{const result=await api('/api/calliope/meaning/discuss',{reference:n.reference,intent,edge:state.edge,request_key:state.handoff.key});const destination=new URL(result.url,location.origin);if(destination.origin!==location.origin||destination.pathname!=='/calliope')throw new Error('Invalid chat destination');location.assign(destination.href);}catch(e){toast('The chat could not open: '+e.message);state.discussing=false;renderDetail();}}
function showEvidence(id){const e=state.graph.edges.find(e=>e.id===id);if(!e){if(id.startsWith('group-edge:')){state.catalog=true;state.catalogMode='objects';renderCatalog();}return;}if(![e.from,e.to].includes(state.selected))select(e.from);state.edge=id;state.detailTab='relationship';renderDetail();renderMap();}
function action(target){const a=target.dataset.action,id=target.dataset.id;if(a==='kind'){state.kind=target.dataset.kind;renderCatalog();}
 else if(a==='view')setView(target.dataset.view);
 else if(a==='open-area')openArea(target.dataset.area);
 else if(a==='review-collection'){openCollection();state.collectionLens='review';$('collection-lens').value='review';renderExplorerResults();}
 else if(a==='trace'){setView('trace');}
 else if(a==='collection-kind'){state.collectionKind=target.dataset.kind;renderExplorer();$('collection-results').scrollIntoView({block:'nearest'});}
 else if(a==='inspect-object')select(id);
 else if(a==='trace-object'){select(id);setView('trace');}
 else if(a==='browse-kind')openCollection(target.dataset.kind);
 else if(a==='trail'){const i=state.trail.lastIndexOf(id);select(id);state.trail=state.trail.slice(0,i);renderTrace();}
 else if(a==='show')select(id,true);
 else if(a==='hide'){if(node(id)?.kind==='area')return;state.visible.delete(id);if(state.selected===id){state.selected=state.graph.nodes.find(n=>n.kind==='area')?.id||'';state.edge=null;loadDetail();}save();render();}
 else if(a==='expand-kind'||a==='collapse-kind'){for(const n of state.graph.nodes.filter(n=>n.kind===state.kind)){if(a==='expand-kind')state.visible.add(n.id);else state.visible.delete(n.id);}if(!state.visible.has(state.selected))state.selected='area:'+state.area;state.layout.positions=positions(displayGraph().nodes);prepare();save();loadDetail();render();fitMap();}
 else if(a==='load-more'){state.layout.limit=Math.min(state.layout.limit+12,40);save();loadGraph().then(renderCatalog);}
 else if(a==='expand-neighbors')setView('trace');
 else if(a==='detail-tab'){state.detailTab=target.dataset.tab;if(state.detailTab!=='relationship')state.edge=null;renderDetail();renderMap();$('detail-tab-'+state.detailTab)?.focus({preventScroll:true});}
 else if(a==='field-role'){state.detailTab='fields';state.fieldRole=target.dataset.role;renderDetail();}
 else if(a==='copy-sql'){const sql=state.detail?.detail?.sql;if(sql)navigator.clipboard.writeText(sql).then(()=>toast('SQL copied.'),()=>toast('Could not copy. You can select and copy the SQL directly.'));}
 else if(a==='expand-detail'){state.expanded=!state.expanded;renderDetail();}
 else if(a==='back-detail'){state.edge=null;renderDetail();renderMap();}
 else if(a==='evidence')showEvidence(target.dataset.edge);
 else if(a==='retry-detail')loadDetail(true);
 else if(a==='pin')pin();else if(a==='unpin')pin(target.dataset.pin);
 else if(a==='open-pin'){const p=state.pins.find(p=>p.id===target.dataset.pin);if(p)openArea(p.reference.area,p.reference.node);}
 else if(a==='discuss')discuss(target.dataset.intent);
}
document.addEventListener('click',e=>{const t=e.target.closest('[data-action]');if(t)action(t)});
$('map').addEventListener('click',e=>{if(state.suppressClick)return;const edge=e.target.closest('[data-edge]');if(edge){showEvidence(edge.dataset.edge);return;}const card=e.target.closest('[data-node]');if(card){if(card.dataset.node.startsWith('group:')){const group=displayGraph().nodes.find(n=>n.id===card.dataset.node);openCollection(group?.group_kind||'');return;}const n=node(card.dataset.node);if(!state.area)openArea(n.area);else select(n.id);}});
$('connections').addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)&&e.target.dataset.edge){e.preventDefault();showEvidence(e.target.dataset.edge)}});
$('map').addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('.zoom-controls'))return;const heading=e.target.closest('[data-drag-node]'),card=e.target.closest('[data-node]');if(card&&!heading||e.target.closest('[data-edge]'))return;const id=heading?.dataset.dragNode;if(id?.startsWith('group:'))return;state.drag={id,x:e.clientX,y:e.clientY,pan:[...state.layout.pan],p:id?[...state.layout.positions[id]]:null,moved:false};state.suppressClick=false;$('map').setPointerCapture(e.pointerId);$('map').classList.add('dragging');if(id&&state.area){state.selected=id;state.edge=null;loadDetail();renderDetail();}e.preventDefault();});
$('map').addEventListener('pointermove',e=>{const d=state.drag;if(!d)return;const dx=e.clientX-d.x,dy=e.clientY-d.y;d.moved||=Math.abs(dx)+Math.abs(dy)>4;if(!d.moved)return;if(d.id)state.layout.positions[d.id]=[d.p[0]+dx/state.layout.zoom,d.p[1]+dy/state.layout.zoom];else state.layout.pan=[d.pan[0]+dx,d.pan[1]+dy];renderMap();});
function endDrag(e){const d=state.drag;if(!d)return;state.suppressClick=d.moved;state.drag=null;$('map').classList.remove('dragging');if($('map').hasPointerCapture(e.pointerId))$('map').releasePointerCapture(e.pointerId);if(d.moved)save();else if(d.id){const n=node(d.id);if(!state.area)openArea(n.area);else select(n.id);}setTimeout(()=>state.suppressClick=false,0);}
$('map').addEventListener('pointerup',endDrag);$('map').addEventListener('pointercancel',endDrag);
$('map').addEventListener('wheel',e=>{e.preventDefault();const r=$('map').getBoundingClientRect(),delta=e.deltaY*(e.deltaMode===1?24:e.deltaMode===2?r.height:1);state.layout=zoomAt(state.layout,state.layout.zoom*Math.exp(clamp(-delta*.004,-.5,.5)),e.clientX-r.left,e.clientY-r.top);save();renderMap();},{passive:false});
for(const [id,factor] of [['zoom-in',1.2],['zoom-out',1/1.2]])$(id).onclick=()=>{state.layout=zoomAt(state.layout,state.layout.zoom*factor,$('map').clientWidth/2,$('map').clientHeight/2);save();renderMap();};
$('map').addEventListener('keydown',e=>{if(e.target!==$('map'))return;const offsets={ArrowLeft:[40,0],ArrowRight:[-40,0],ArrowUp:[0,40],ArrowDown:[0,-40]};if(offsets[e.key]){e.preventDefault();state.layout.pan=state.layout.pan.map((p,i)=>p+offsets[e.key][i]);save();renderMap();}else if(e.key==='Home'){e.preventDefault();fitMap();}});
$('areas-back').onclick=()=>openArea('');$('fit').onclick=fitMap;$('refresh').onclick=()=>{loadGraph();loadPins();};$('area-search').oninput=()=>{filterAreas();renderExplorer();if(state.view==='map')fitMap();};$('object-search').oninput=renderCatalog;
$('browse').onclick=()=>{state.catalog=!(state.catalog&&state.catalogMode==='objects');state.catalogMode='objects';render();};$('pins').onclick=()=>{state.catalog=!(state.catalog&&state.catalogMode==='pins');state.catalogMode='pins';render();};$('catalog-close').onclick=()=>{state.catalog=false;render();};
$('reset-view').onclick=()=>{state.visible=initialVisible(state.graph.nodes);state.selected=state.graph.nodes.find(n=>n.kind==='area')?.id||'';state.edge=null;state.layout.positions=positions(displayGraph().nodes);save();loadDetail();render();fitMap();};
new ResizeObserver(()=>renderMap()).observe($('map'));
window.addEventListener('popstate',()=>{const q=new URLSearchParams(location.search);state.view=['trace','map'].includes(q.get('view'))?q.get('view'):'explore';openArea(q.get('area')||'',q.get('node')||'',false)});
window.addEventListener('pagehide',()=>{clearTimeout(state.saveTimer);try{localStorage.setItem(storageKey,JSON.stringify(state.layouts))}catch{}});
setInterval(()=>{if(!document.hidden&&!state.busy&&!state.discussing&&!state.drag){loadGraph();loadPins();}},60000);
const q=new URLSearchParams(location.search);state.view=['trace','map'].includes(q.get('view'))?q.get('view'):'explore';openArea(q.get('area')||'',q.get('node')||'');loadPins();

$('detail-content').addEventListener('input',e=>{if(e.target.id!=='field-search')return;state.fieldQuery=e.target.value;$('field-results').innerHTML=fieldRows(array(state.detail?.detail?.fields));});
$('detail-tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;const tabs=[...$('detail-tabs').querySelectorAll('[role="tab"]')],i=tabs.indexOf(e.target);if(i<0)return;e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;action(tabs[next]);});
function inspectObservation(e){const target=e.target.closest('[data-observation]');if(!target)return;const o=numericHistory(state.detail?.detail?.observations)[Number(target.dataset.observation)],readout=$('detail-content').querySelector('.chart-readout');if(o&&readout)readout.innerHTML=`<span>Saved observation</span><strong>${esc(exactNumber(o.value))}</strong>${timestamp(o.observed_at,true)}`;}
$('detail-content').addEventListener('pointerover',inspectObservation);$('detail-content').addEventListener('focusin',inspectObservation);

$('collection-search').oninput=e=>{state.collectionQuery=e.target.value;renderExplorerResults();};
$('collection-lens').onchange=e=>{state.collectionLens=e.target.value;renderExplorerResults();};

document.addEventListener('keydown',e=>{if(e.key==='Escape'&&state.expanded){state.expanded=false;renderDetail();}});
