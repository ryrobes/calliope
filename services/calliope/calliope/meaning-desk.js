import {mountPivot} from './meaning-pivot.js';
import {escapeHtml as esc,exactNumber,timestamp,dataValue,sqlMarkup,historyChart,numericHistory} from './meaning-present.mjs';
import {kinds,initialObject,connected,dataSource,routeFromUrl,routeUrl,nextRoute,bars,valueText,ownedNavigation,shelfSpec} from './meaning-data.mjs';
import {mountDiagram,areaModel,objectModel,landscapeModel,levelsFor,LEGEND,LEVEL_OF,TAGS,legendKey,ROLES,relationRole,grainText,joinLabel,compact,areaGroups} from './meaning-diagram.mjs';
const $=id=>document.getElementById(id),content=$('desk-content'),owner=document.body.dataset.meaningOwner;
const icon=name=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">${({back:'<path d="m14 6-6 6 6 6"/>',forward:'<path d="m10 6 6 6-6 6"/>',refresh:'<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>',arrow:'<path d="M5 12h14m-6-6 6 6-6 6"/>',pin:'<path d="m8 3 8 0-1 6 4 4H5l4-4-1-6Zm4 10v8"/>',close:'<path d="M6 6l12 12M18 6 6 18"/>'})[name]||''}</svg>`;
const iconButton=(label,action,name,attrs='')=>btn(icon(name),action,`class="desk-icon-button" aria-label="${label}" title="${label}" ${attrs}`);
const btn=(label,action,attrs='')=>`<button type="button" data-do="${action}" ${attrs}>${label}</button>`;
let route=routeFromUrl(location.href),graph={nodes:[],edges:[]},detail=null,data=null,loading=false,dataLoading=false,error='',dataError='',request=0,controller,backIndex=0,forwardIndex=0;
const graphCache=new Map(),detailCache=new Map(),dataCache=new Map(),fieldsCache=new Map();
let pivotHost=null,pivotMount=null,pivotSource='',readyContext='';
let mapHost=null,mapMount=null,mapQuery='',mapCrossing=false,mapFocus='',playTimer=null;
const mapHidden=new Set((()=>{try{return JSON.parse(localStorage.getItem('meaning-map-hidden')||'[]');}catch{return [];}})().filter(k=>LEGEND.some(l=>l[0]===k)));
const viewContext=()=>JSON.stringify([route.area,route.node,route.source,route.panel,route.record,route.view]);
const array=v=>Array.isArray(v)?v:[],selected=()=>graph.nodes.find(n=>n.id===route.node),source=()=>dataSource(graph,selected(),route.source);
let collectionKind=route.node.startsWith('cube:')?'cube':route.node.startsWith('relation:')?'relation':route.node.startsWith('artifact:')?'artifact':/^(idea|breakdown):/.test(route.node)?'idea':'metric',collectionQuery='',pins=[],operation=false;
async function api(path,body,signal){const r=await fetch(path,{credentials:'same-origin',method:body?'POST':'GET',headers:{Accept:'application/json',...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal});let value={};try{value=await r.json();}catch{}if(!r.ok)throw new Error(value.error?.message||`Request failed (${r.status})`);return value;}
function cacheSet(cache,key,value){cache.set(key,value);if(cache.size>30)cache.delete(cache.keys().next().value);}
function scrollPosition(){return {page:content.scrollTop||0,body:content.querySelector('.desk-object-body')?.scrollTop||0,overview:content.querySelector('.desk-overview,.desk-pins')?.scrollTop||0,catalog:content.querySelector('.desk-object-list')?.scrollTop||0};}
function restoreScroll(saved){const s=typeof saved==='number'?{page:saved,body:saved}:saved||{};content.scrollTop=s.page||0;for(const [key,selector] of [['body','.desk-object-body'],['overview','.desk-overview,.desk-pins'],['catalog','.desk-object-list']]){const pane=content.querySelector(selector);if(pane)pane.scrollTop=s[key]||0;}}
function snapshot(){history.replaceState({meaningDesk:true,owner,index:backIndex,route,scroll:scrollPosition(),collectionKind,collectionQuery},'',routeUrl(route));}
function navigate(patch){const scroll=['dimension','aggregate','measure','filter','offset','spec','dataView'].some(k=>k in patch)?scrollPosition():('panel'in patch||'record'in patch)?{catalog:scrollPosition().catalog}:0;snapshot();route=nextRoute(route,patch);if('node'in patch||'area'in patch){collectionQuery='';const kind=route.node.split(':')[0];collectionKind=kind==='breakdown'?'idea':kind||'metric';}backIndex++;forwardIndex=backIndex;history.pushState({meaningDesk:true,owner,index:backIndex,route,scroll,collectionKind,collectionQuery},'',routeUrl(route));load(false,scroll);}
async function load(refresh=false,scroll=0){
 const inPlace=route.view!=='pins'&&!refresh&&!loading&&pivotHost?.isConnected&&readyContext===viewContext();
 controller?.abort();controller=new AbortController();const signal=controller.signal,id=++request;
 // Shelf edits, result tabs, paging and query history only update the mounted
 // worksheet. Keep its last result and the surrounding object detail in place.
 if(inPlace){dataError='';await loadData(false,id,signal);return;}
 readyContext='';dataLoading=false;loading=refresh||!graphCache.has(route.area);if(!loading)graph=graphCache.get(route.area);error='';dataError='';data=null;detail=null;render();try{const key=route.area;graph=!refresh&&graphCache.has(key)?graphCache.get(key):await api('/api/calliope/meaning/graph?'+new URLSearchParams({area:key,limit:'40'}),null,signal);if(id!==request)return;cacheSet(graphCache,key,graph);if(route.area&&route.node==='area:'+route.area){route.node='';snapshot();}if(route.area&&route.mode==='objects'&&!route.node){route.node=initialObject(graph.nodes)?.id||'';collectionKind=route.node.split(':')[0]||'metric';snapshot();}const n=selected();if(n)detail=detailCache.get(JSON.stringify(n.reference))||null;loading=false;render();if(n&&['metric','cube','relation'].includes(n.kind)){const key=JSON.stringify(n.reference);detail=!refresh&&detailCache.has(key)?detailCache.get(key):await api('/api/calliope/meaning/inspect',n.reference,signal);if(id!==request)return;cacheSet(detailCache,key,detail);render();}readyContext=viewContext();await loadData(refresh,id,signal);if(id===request)restoreScroll(scroll);}catch(e){if(e.name!=='AbortError'&&id===request){error=e.message;loading=false;render();}}}
async function loadData(refresh,id,signal){const n=route.view!=='pins'&&['relation','cube'].includes(selected()?.kind)?source():null;if(!n||route.record||route.panel==='definition'){dataLoading=false;renderData();return;}const body={reference:n.reference,spec:shelfSpec(route),filter:route.filter,offset:route.offset},key=JSON.stringify(body);dataLoading=true;renderData();try{const result=!refresh&&dataCache.has(key)?dataCache.get(key):await api('/api/calliope/meaning/data',body,signal);if(id!==request)return;data=result;cacheSet(dataCache,key,data);fieldsCache.set(n.source,{fields:data.fields,catalog:data.catalog});}catch(e){if(e.name==='AbortError')return;if(id===request)dataError=e.message;}finally{if(id===request){dataLoading=false;renderData();}}}
function nodeButton(n){return btn(`<strong>${esc(n.title)}</strong>${n.kind==='metric'?`<span class="desk-list-value">${n.preview?esc(exactNumber(n.preview.value)):'No saved value'}</span><small>${esc(n.preview?.label||'No observation')} · ${esc(n.status)}</small>`:`<small>${esc(n.source||n.subtitle||n.status)}</small>`}`,'select',`data-id="${esc(n.id)}" class="desk-object" aria-pressed="${route.node===n.id}"`);}
function shelf(){const items=collectionItems();return `<aside class="desk-shelf" aria-label="Objects in this area"><header class="desk-shelf-heading"><strong>${esc(kinds[collectionKind]||'Objects')}</strong><span>${items.length}</span></header><div class="desk-object-list">${items.map(nodeButton).join('')||'<p class="desk-small desk-empty">No matching objects.</p>'}</div></aside>`;}
function collectionItems(){return graph.nodes.filter(n=>(n.kind===collectionKind||(collectionKind==='idea'&&n.kind==='breakdown'))&&`${n.title} ${n.source||''}`.toLowerCase().includes(collectionQuery.toLowerCase()));}
function catalogBar(){const available=[...new Set(graph.nodes.filter(n=>n.kind!=='area').map(n=>n.kind==='breakdown'?'idea':n.kind))];if(!available.includes(collectionKind))collectionKind=available[0]||'metric';return `<div class="desk-collection-toolbar">${modeSwitch('objects')}<div class="desk-types" aria-label="Object categories">${available.map(k=>btn(`${esc(kinds[k])}<span>${graph.nodes.filter(n=>(n.kind==='breakdown'?'idea':n.kind)===k).length}</span>`,'kind',`data-kind="${k}" aria-pressed="${collectionKind===k}"`)).join('')}</div><input id="desk-search" type="search" placeholder="Find an object…" aria-label="Find an object in this area" value="${esc(collectionQuery)}"></div>`;}
function connections(n){const links=connected(graph,n.id);if(!links.length)return '';return `<section class="desk-connections"><h2>Connected data & work</h2><div>${links.map(({node,edge,incoming})=>`<article>${btn(esc(node.title),'select',`data-id="${esc(node.id)}" class="desk-link"`)}<small>${esc(kinds[node.kind]||node.kind)} · ${esc(edge.label)} · ${esc(edge.state)}</small><details><summary>Connection basis</summary><p>${esc(valueText(edge.evidence))}</p></details></article>`).join('')}</div></section>`;}
function definition(n){const d=detail?.detail||{};return `<section class="desk-definition"><h2>${n.kind==='metric'?'Definition & specs':'Definition & provenance'}</h2>${n.kind==='metric'?`<dl class="desk-facts"><div><dt>Definition version</dt><dd>${esc(n.version)}</dd></div><div><dt>Fixed default scope</dt><dd>${dataValue(d.defaults||n.defaults||{})}</dd></div><div><dt>Grain</dt><dd>${esc(d.grain||n.subtitle)}</dd></div><div><dt>Window</dt><dd>${dataValue(d.window??n.contract?.window)}</dd></div></dl>`:''}${d.sql?`<pre><code>${sqlMarkup(d.sql)}</code></pre>`:'<p class="desk-small">No SQL definition recorded for this object.</p>'}${n.review?.length?`<div class="desk-warning">Definition review: ${esc(n.review.join(' · '))}</div>`:''}<details><summary>Recorded description · ${esc(n.status)}</summary><p>${esc(n.description||'No description recorded.')}</p><p class="desk-small">Descriptions and classifications can be inferred. A recorded calculation does not establish a business policy.</p></details>${array(d.fields).length?`<h3>Recorded fields</h3><div class="desk-table"><table><thead><tr><th>Field</th><th>Type</th><th>Recorded role</th></tr></thead><tbody>${d.fields.map(f=>`<tr><td>${esc(f.col_name)}</td><td>${esc(f.data_type)}</td><td>${esc(f.role)}</td></tr>`).join('')}</tbody></table></div>`:''}</section>`;}
function metric(n){const d=detail?.detail,observations=numericHistory(d?.observations),latest=observations.at(-1);return `<section class="desk-metric"><span class="desk-eyebrow">${latest?'Stored daily observations':esc(n.preview?.label||'Registered measure')} · definition v${esc(n.version)}</span><div class="desk-reading">${dataValue(latest?.value??n.preview?.value)}</div><div class="desk-small">${latest?timestamp(latest.observed_at,true):n.preview?timestamp(n.preview.at,true):'No saved observation available.'}</div><div data-metric-history>${d?.observations_restricted?'<p class="desk-warning">Saved aggregate history is unavailable for your database role.</p>':d?historyChart(d.observations,content.querySelector('.desk-object-body')?.clientWidth||640):'<p class="desk-small">Loading stored history…</p>'}</div><details><summary>Observation receipts · fixed defaults ${esc(JSON.stringify(d?.observation_scope||n.defaults||{}))}</summary><div class="desk-table"><table><thead><tr><th>Value</th><th>Observed</th><th>Data as of</th></tr></thead><tbody>${array(d?.observations).map(o=>`<tr><td>${dataValue(o.value)}</td><td>${timestamp(o.observed_at,true)}</td><td>${timestamp(o.data_as_of,true)}</td></tr>`).join('')}</tbody></table></div></details></section>`;}
function explorer(n){const src=source();if(!src)return `<p class="desk-empty">No declared source is available for data exploration.</p>`;const fields=data?.fields||fieldsCache.get(src.source)?.fields||[],peers=connected(graph,n.id).filter(x=>['relation','cube'].includes(x.node.kind)&&x.edge.state==='declared').map(x=>x.node),options=items=>items.map(f=>`<option value="${esc(f.name)}">${esc(f.name)}</option>`).join('');return `<section class="desk-data"><header class="desk-section-head"><div><h2>${n.kind==='cube'?'Explore this cube':'Explore source records'}</h2><p class="desk-small" data-query-caption>${esc(src.source)} · ${data?`queried ${timestamp(data.as_of,true)}`:'live source query'}</p></div>${peers.length>1?`<label>Source<select data-control="source">${peers.map(p=>`<option value="${esc(p.id)}" ${src.id===p.id?'selected':''}>${esc(p.title)}</option>`).join('')}</select></label>`:''}</header><div id="meaning-pivot-slot">${dataError?`<p class="desk-warning">${esc(dataError)} ${btn('Retry data','data-refresh')}</p>`:'<p class="desk-small" role="status">Loading source fields…</p>'}</div></section>`;}
function recordView(){const r=route.record;return `<section class="desk-record"><span class="desk-eyebrow">Source row · fetched ${esc(r.as_of)}</span><h1>${esc(r.source)}</h1><dl class="desk-facts">${Object.entries(r.values).map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${dataValue(v)}</dd></div>`).join('')}</dl><p class="desk-small">A read-only preview captured when you opened this row. Back restores your selection.</p></section>`;}
function objectView(){const n=selected();if(!n)return '<div class="desk-empty">This object is not available in the authorized area. Choose another object.</div>';const pinned=pins.some(p=>p.reference.node===n.id&&p.reference.area===route.area);return `<div class="desk-object-page"><header class="desk-title"><div><span class="desk-eyebrow">${esc(kinds[n.kind]||n.kind)} · ${esc(n.status)}</span><h1>${esc(n.title)}</h1></div><div class="desk-actions">${btn('Ask Callie','discuss',`class="desk-primary" ${operation?'disabled':''}`)}${btn(pinned?'Pinned':'Pin to Home','pin',pinned||operation?'disabled':'')}</div></header><nav class="desk-local" aria-label="Object view">${btn(n.kind==='metric'?'History':n.kind==='artifact'?'Published work':n.kind==='idea'||n.kind==='breakdown'?'Suggestion':'Data','panel',`data-panel="data" aria-pressed="${route.panel==='data'}"`)}${btn('Definition & source','panel',`data-panel="definition" aria-pressed="${route.panel==='definition'}"`)}${btn('Map','panel',`data-panel="map" aria-pressed="${route.panel==='map'}"`)}</nav><div class="desk-object-body">${route.record?recordView():route.panel==='map'?`<div class="desk-map desk-dg-object">${mapToolbar('object')}${mapFrame('object')}</div>`:route.panel==='definition'?definition(n):n.kind==='metric'?`<div class="desk-metric-overview">${metric(n)}${definition(n)}</div>`:['relation','cube'].includes(n.kind)?explorer(n):n.kind==='artifact'?`<div class="desk-artifact"><h2>${esc(n.title)}</h2><p class="desk-small">${esc(n.subtitle)}</p><a href="/apps/${encodeURIComponent(n.slug)}" target="_blank" rel="noopener">Open published app ↗</a></div>`:`<div class="desk-empty"><h2>Suggested analysis · no result recorded</h2><p>${esc(n.title)}</p><p class="desk-small">This is an unexecuted proposal. Explore a registered measure, cube, or source from the area list.</p></div>`}${route.panel!=='map'&&(!['relation','cube'].includes(n.kind)||route.panel==='definition')?connections(n):''}</div></div>`;}
function home(){const nodes=graph.nodes.filter(n=>!collectionQuery||n.title.toLowerCase().includes(collectionQuery.toLowerCase()));return `<div class="desk-overview"><header class="desk-home-title"><span class="desk-eyebrow">Meaning · business data</span><h1>Your data,<br><em>connected.</em></h1><p>${graph.nodes.length} business areas · measures, cubes, sources & connected work</p></header>${graph.nodes.length?`<section class="desk-landscape" aria-label="How your business areas connect">${mapToolbar('landscape')}${mapFrame('landscape')}</section>`:''}<div class="desk-collection-toolbar"><strong class="desk-collection-label">Business areas <span>${nodes.length}</span></strong><input id="desk-search" type="search" placeholder="Find an area…" aria-label="Find a business area" value="${esc(collectionQuery)}"></div><div class="desk-areas">${nodes.map(n=>btn(`<span class="desk-eyebrow">Business area</span><h2>${esc(n.title)}</h2><p>${esc(n.subtitle||'Published data objects')}</p><footer><span>Explore data</span>${icon('arrow')}</footer>`,'area',`data-area="${esc(n.area)}" class="desk-area-card"`)).join('')||'<p class="desk-empty">No matching business areas.</p>'}</div></div>`;}
function pinnedView(){return `<div class="desk-pins"><header class="desk-home-title"><span class="desk-eyebrow">Your collection</span><h1>Pinned to Home</h1><p>${pins.length} saved references to your data and work.</p></header><div class="desk-areas">${pins.map(p=>btn(`<span class="desk-eyebrow">${esc(kinds[p.reference.node.split(':')[0]]||'Saved object')}</span><h2>${esc(p.title||p.reference.node)}</h2><footer><span>Open object</span>${icon('arrow')}</footer>`,'open-pin',`class="desk-area-card" data-area="${esc(p.reference.area)}" data-id="${esc(p.reference.node)}"`)).join('')||'<div class="desk-empty"><h2>Your collection starts here</h2><p>Open an object and choose Pin to Home to keep it close.</p></div>'}</div></div>`;}
function render(){const previousScroll=scrollPosition();content.classList.toggle('desk-query',route.view!=='pins'&&!loading&&['relation','cube'].includes(selected()?.kind)&&route.panel==='data'&&!route.record);const area=graph.nodes.find(n=>n.kind==='area'&&n.area===route.area);$('desk-nav').innerHTML=`<div class="desk-location">${iconButton('Back','back','back',backIndex?'':'disabled')}${iconButton('Forward','forward','forward',backIndex<forwardIndex?'':'disabled')}${btn('Meaning','home')}${route.view==='pins'?'<span>/</span><strong>Pinned to Home</strong>':route.area?`<span>/</span><strong>${esc(area?.title||route.area)}</strong>`:''}</div><div class="desk-nav-actions"><a href="/meaning/logic">Business logic ↗</a>${btn(`${icon('pin')}<span>Pinned</span>`,'pins',`aria-pressed="${route.view==='pins'}"`)}${iconButton('Refresh','refresh','refresh',loading?'disabled':'')}</div>`;$('desk-notice').hidden=!error;$('desk-notice').textContent=error;const focus=document.activeElement?.id==='desk-search',position=focus?document.activeElement.selectionStart:null,mapSearchFocus=document.activeElement?.id==='dg-search'?{at:document.activeElement.selectionStart}:null;pivotHost?.remove();content.classList.toggle('desk-mapping',mapScope()==='area');content.innerHTML=loading?'<div class="desk-empty" role="status">Loading published data objects…</div>':route.view==='pins'?pinnedView():route.area&&!route.node&&route.mode!=='objects'?areaMapView():route.area?`${catalogBar()}<div class="desk-layout">${shelf()}${objectView()}</div>`:home();for(const k of ['dimension','aggregate','measure']){const el=content.querySelector(`[data-control="${k}"]`);if(el)el.value=route[k];}syncPivot();syncMap();resizeHistory();restoreScroll(previousScroll);if(focus&&$('desk-search')){$('desk-search').focus({preventScroll:true});$('desk-search').setSelectionRange(position,position);}if(mapSearchFocus&&$('dg-search')){$('dg-search').focus({preventScroll:true});$('dg-search').setSelectionRange(mapSearchFocus.at,mapSearchFocus.at);}}
async function operationOn(action,target){const n=target||selected();if(!n||operation)return;operation=true;render();try{if(action==='pin'){await api('/api/calliope/meaning/pins',{operation:'pin',reference:n.reference});pins=(await api('/api/calliope/meaning/pins')).pins||[];}else{const result=await api('/api/calliope/meaning/discuss',{reference:n.reference,intent:'ask',request_key:crypto.randomUUID()});const url=new URL(result.url,location.origin);if(url.origin!==location.origin||url.pathname!=='/calliope')throw new Error('Invalid discussion destination');location.assign(url.href);}}catch(e){error=e.message;}finally{operation=false;render();}}
content.addEventListener('change',e=>{const key=e.target.dataset.control;if(key)navigate({[key]:e.target.value});});
document.addEventListener('click',e=>{const t=e.target.closest('[data-do]');if(!t||t.disabled)return;const a=t.dataset.do;if(playTimer&&a!=='dg-play')stopPlay();if(a.startsWith('dg-')){mapAction(a,t);return;}if(a==='back')history.back();else if(a==='forward')history.forward();else if(a==='home'){collectionQuery='';navigate({area:'',node:'',view:'data'});}else if(a==='area'){collectionQuery='';collectionKind='metric';navigate({area:t.dataset.area,node:''});}else if(a==='select'){const n=graph.nodes.find(n=>n.id===t.dataset.id);navigate({node:t.dataset.id});}else if(a==='kind'){collectionKind=t.dataset.kind;collectionQuery='';render();snapshot();}else if(a==='panel')navigate({panel:t.dataset.panel,record:null});else if(a==='refresh'){graphCache.clear();detailCache.clear();dataCache.clear();load(true);}else if(a==='data-refresh'){dataCache.clear();load();}else if(a==='group'){const group=data?.groups?.[Number(t.dataset.index)];if(group)navigate({filter:route.filter?.value===group.key?null:{field:route.dimension,value:group.key}});}else if(a==='clear')navigate({filter:null});else if(a==='next'||a==='previous')navigate({offset:route.offset+(a==='next'?40:-40)});else if(a==='record'){const row=data?.rows?.[Number(t.dataset.index)];if(row)navigate({record:{source:data.source,as_of:data.as_of,values:row}});}else if(a==='pin'||a==='discuss')operationOn(a);else if(a==='pins')navigate({view:route.view==='pins'?'data':'pins'});else if(a==='open-pin')navigate({area:t.dataset.area,node:t.dataset.id,view:'data',panel:'data',record:null});else if(a==='return')render();});
content.addEventListener('input',e=>{if(e.target.id==='desk-search'){collectionQuery=e.target.value;render();snapshot();}else if(e.target.id==='dg-search'){mapQuery=e.target.value;syncMap();}});
content.addEventListener('keydown',e=>{if(e.target.id==='dg-search'&&e.key==='Enter'){e.preventDefault();revealMatch();}});
document.addEventListener('keydown',e=>{if(e.metaKey||e.ctrlKey||e.altKey||e.target.closest?.('input,textarea,select,[contenteditable="true"]')||!['1','2','3'].includes(e.key)||!mapScope())return;e.preventDefault();stopPlay();setLevel(Number(e.key));});
function observation(e){const hit=e.target.closest('[data-observation]');if(!hit)return;const o=numericHistory(detail?.detail?.observations)[Number(hit.dataset.observation)],readout=content.querySelector('.chart-readout');if(o&&readout)readout.innerHTML=`<span>Saved observation</span><strong>${esc(exactNumber(o.value))}</strong>${timestamp(o.observed_at,true)}`;}
content.addEventListener('mouseover',observation);content.addEventListener('focusin',observation);
window.addEventListener('popstate',e=>{const saved=ownedNavigation(e.state,owner);route=saved?saved.route:routeFromUrl(location.href);backIndex=saved?.index||0;collectionKind=saved?.collectionKind||'metric';collectionQuery=saved?.collectionQuery||'';load(false,saved?.scroll||0);});
if(ownedNavigation(history.state,owner)){route=history.state.route;backIndex=history.state.index||0;forwardIndex=backIndex;collectionKind=history.state.collectionKind||'metric';collectionQuery=history.state.collectionQuery||'';}snapshot();load();api('/api/calliope/meaning/pins').then(r=>{pins=r.pins||[];render();}).catch(()=>{});

function resizeHistory(){const chart=content.querySelector('[data-metric-history]'),d=detail?.detail;if(chart&&d&&!d.observations_restricted)chart.innerHTML=historyChart(d.observations,chart.clientWidth);}
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(resizeHistory,100);});

function syncPivot(){
 const slot=$('meaning-pivot-slot'),src=source(),catalog=data?.catalog||fieldsCache.get(src?.source)?.catalog;
 if(!slot||!catalog){if(!slot&&pivotMount){pivotMount.destroy();pivotMount=null;pivotHost=null;pivotSource='';}return;}
 const props={catalog,spec:shelfSpec(route),result:data?.rollup||null,loading:dataLoading,error:dataError,records:data,view:route.dataView||'results',elapsed:data?.elapsed_ms,
  selection:route.filter?(Array.isArray(route.filter)?route.filter:[route.filter]):null,
  onChange:spec=>navigate({spec}),onSelect:filter=>navigate({filter}),
  onView:dataView=>navigate({dataView}),onPage:offset=>navigate({offset}),
  onOpenRecord:row=>navigate({record:{source:data.source,as_of:data.as_of,values:row}}),
  onRefresh:()=>{dataCache.clear();load(false,scrollPosition());},
  probe:(field,search='')=>api('/api/calliope/meaning/data',{reference:src.reference,spec:shelfSpec(route),probe:{field,search}})};
 if(pivotSource!==src.source){pivotMount?.destroy();pivotMount=null;pivotHost=document.createElement('div');pivotSource=src.source;}
 if(slot.firstChild!==pivotHost)slot.replaceChildren(pivotHost);if(pivotMount)pivotMount.update(props);else pivotMount=mountPivot(pivotHost,props);
}

function renderData(){
 if(!pivotMount||!pivotHost?.isConnected){render();return;}
 const caption=content.querySelector('[data-query-caption]'),src=source();
 if(caption&&src)caption.innerHTML=`${esc(src.source)} · ${data?`queried ${timestamp(data.as_of,true)}`:'live source query'}`;
 for(const action of ['back','forward']){
  const button=$('desk-nav').querySelector(`[data-do="${action}"]`);
  if(button)button.disabled=action==='back'?!backIndex:backIndex>=forwardIndex;
 }
 syncPivot();
}

// Map: EventCatalog-style levels over the same graph (meaning-diagram.mjs).
// L1 areas and groups, L2 sources and cubes, L3 measures and work. Each map
// type remembers its last level for this viewer; the URL carries the rest.
function remembered(scope){try{return Number(localStorage.getItem('meaning-map-level:'+scope))||0;}catch{return 0;}}
function remember(scope,n){try{localStorage.setItem('meaning-map-level:'+scope,String(n));}catch{}}
function mapScope(){if(route.view==='pins'||loading)return '';if(!route.area)return 'landscape';if(!route.node)return route.mode==='objects'?'':'area';return route.panel==='map'&&selected()?'object':'';}
function mapLevel(scope){const levels=levelsFor(scope),ok=n=>levels.some(l=>l.n===n&&l.available);for(const n of [route.level,remembered(scope),scope==='object'?2:1])if(ok(n))return n;return levels.find(l=>l.available).n;}
function mapModel(scope=mapScope(),level=mapLevel(scope)){return scope==='landscape'?landscapeModel(graph):scope==='area'?areaModel(graph,{level,hidden:mapHidden}):scope==='object'?objectModel(graph,{target:route.node,level,hidden:mapHidden}):{nodes:[],edges:[]};}
function modeSwitch(active){return `<div class="desk-types dg-mode" role="group" aria-label="Area view">${btn('Map','dg-mode',`data-mode="map" aria-pressed="${active==='map'}"`)}${btn('Objects','dg-mode',`data-mode="objects" aria-pressed="${active==='objects'}"`)}</div>`;}
function mapToolbar(scope){
 const level=mapLevel(scope);
 const levels=`<div class="dg-levels" role="group" aria-label="Diagram level">${levelsFor(scope).map(l=>btn(`<b>L${l.n}</b><span>${esc(l.label)}</span>`,'dg-level',`data-level="${l.n}" aria-pressed="${l.n===level}" aria-label="Level ${l.n}: ${esc(l.label)}" title="${esc(l.available?l.hint+' ('+l.n+')':l.reason)}" ${l.available?'':'disabled'}`)).join('')}</div>`;
 const extra=scope==='landscape'?'':`${btn(`<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">${playTimer?'<path d="M7 6h3v12H7zM14 6h3v12h-3z"/>':'<path d="M8 5v14l11-7z"/>'}</svg><span>${playTimer?'Stop':'Play levels'}</span>`,'dg-play',`class="dg-play" aria-pressed="${!!playTimer}" title="Step through the levels"`)}${btn('Cross-area links','dg-crossing',`aria-pressed="${mapCrossing}" title="Highlight every link that crosses this area’s boundary"`)}`;
 return `<div class="dg-toolbar">${scope==='area'?modeSwitch('map'):''}${levels}${extra}<input id="dg-search" type="search" placeholder="Find on the map…" aria-label="Find an object on the map" value="${esc(mapQuery)}"></div>`;
}
function mapLegend(scope){
 if(scope==='landscape')return '';
 const level=Math.max(mapLevel(scope),scope==='object'?LEVEL_OF[selected()?.kind]||2:1),counts=new Map();
 for(const n of graph.nodes){const k=legendKey(n.kind);counts.set(k,(counts.get(k)||0)+1);}
 const items=LEGEND.filter(([k,,min])=>min<=level&&counts.get(k));
 return items.length?`<div class="dg-legend" role="group" aria-label="Show or hide object types">${items.map(([k,label])=>btn(`<i data-kind="${k==='suggestion'?'idea':k}" aria-hidden="true"></i>${esc(label)}<span>${counts.get(k)}</span>`,'dg-legend',`data-key="${k}" aria-pressed="${!mapHidden.has(k)}" title="${mapHidden.has(k)?'Show':'Hide'} ${esc(label.toLowerCase())}"`)).join('')}</div>`:'';
}
function mapFrame(scope){return `<div class="desk-dg-frame" data-scope="${scope}"><div id="meaning-map-slot" class="dg-slot"></div>${mapLegend(scope)}${inspector(scope)}</div>`;}
function areaMapView(){return `<div class="desk-map">${mapToolbar('area')}${mapFrame('area')}</div>`;}
const pinnedNode=n=>pins.some(p=>p.reference.node===n.id&&p.reference.area===(n.area||route.area));
// The inspector shows what Meaning knows about the picked object, not a
// sentence about it: a group's members and their grain, a table's columns and
// a few real rows, an area's questions and gaps, a measure's history.
const inspectCache=new Map(),sampleCache=new Map(),areaGraphs=new Map();
function lazy(cache,key,load){
 if(cache.has(key))return cache.get(key);
 cache.set(key,{loading:true});
 load().then(value=>cache.set(key,{value}),e=>cache.set(key,{error:e.message||'Unavailable'})).finally(()=>{if(route.pick)render();});
 return cache.get(key);
}
const inspectOf=n=>lazy(inspectCache,JSON.stringify(n.reference),()=>api('/api/calliope/meaning/inspect',n.reference));
const sampleOf=n=>lazy(sampleCache,JSON.stringify(n.reference),()=>api('/api/calliope/meaning/data',{reference:n.reference,spec:{groupBy:[],measures:[{id:'value',column:null,agg:'count',alias:'Record count'}]},filter:null,offset:0}));
const areaGraphOf=area=>graphCache.has(area)?{value:graphCache.get(area)}:lazy(areaGraphs,area,()=>api('/api/calliope/meaning/graph?'+new URLSearchParams({area,limit:'40'})));
const pending=(state,what)=>state?.loading?`<p class="desk-small dg-loading">Reading ${what}…</p>`:state?.error?`<p class="desk-small">${esc(what[0].toUpperCase()+what.slice(1))} unavailable: ${esc(state.error)}</p>`:'';
function roleChip(n){const role=relationRole(n);return role?`<span class="dg-chip" data-role="${role}">${esc(ROLES[role])}</span>`:'';}
function memberList(members){
 return `<ul class="dg-members">${members.map(x=>`<li>${btn(`<strong>${esc(x.title)}</strong>`,'dg-reveal',`data-id="${esc(x.id)}"`)}${roleChip(x)}${x.subject_role&&x.subject_role!=='core'?'<span class="dg-chip">supporting</span>':''}<small>${esc(grainText(x)||x.description||'')}</small><small class="dg-mono">${esc([x.source,x.preview&&compact(x.preview.value)+' rows'].filter(Boolean).join(' · '))}</small></li>`).join('')}</ul>`;
}
function sparkline(series){
 const v=(series||[]).filter(Number.isFinite);if(v.length<2)return '';
 const lo=Math.min(...v),hi=Math.max(...v),w=240,h=42,x=i=>i*(w/(v.length-1)),y=n=>h-3-(hi===lo?.5:(n-lo)/(hi-lo))*(h-6);
 return `<svg class="dg-spark" viewBox="0 0 ${w} ${h}" role="img" aria-label="${v.length} saved observations"><polyline points="${v.map((n,i)=>`${x(i).toFixed(1)},${y(n).toFixed(1)}`).join(' ')}"/></svg>`;
}
function columnsTable(fields){
 const shown=(fields||[]).filter(f=>!/^_dlt_/.test(f.col_name)).slice(0,14);
 return shown.length?`<ul class="dg-columns">${shown.map(f=>`<li><code>${esc(f.col_name)}</code>${f.role?`<span class="dg-chip">${esc(f.role)}</span>`:''}<small>${esc(f.what||f.data_type||'')}</small></li>`).join('')}</ul>${fields.length>shown.length?`<p class="desk-small">${fields.length-shown.length} more in Open details.</p>`:''}`:'';
}
function sampleTable(result){
 const rows=(result?.rows||[]).slice(0,5);if(!rows.length)return '<p class="desk-small">No rows returned.</p>';
 const cols=Object.keys(rows[0]).filter(c=>!/^_dlt_/.test(c)).slice(0,5);
 return `<div class="dg-sample"><table><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${esc(valueText(r[c]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function list(title,items){return items?.length?`<h3>${esc(title)}</h3><ul class="dg-points">${items.map(x=>`<li>${esc(typeof x==='string'?x:x.question||x.text||x.name||JSON.stringify(x))}</li>`).join('')}</ul>`:'';}
function inspector(scope){
 const id=route.pick;if(!id)return '';
 const model=mapModel(scope),m=model.nodes.find(n=>n.id===id);if(!m)return '';
 const n=graph.nodes.find(x=>x.id===(m.source||id)),level=mapLevel(scope),byId=new Map(graph.nodes.map(x=>[x.id,x]));
 const links=model.edges.filter(e=>e.from===id||e.to===id).map(e=>({e,out:e.from===id,other:model.nodes.find(x=>x.id===(e.from===id?e.to:e.from))})).filter(x=>x.other);
 let body='',actions='',eyebrow=TAGS[m.kind]||m.kind;
 if(m.kind==='group'){
  let members=(m.members||[]).map(x=>byId.get(x)).filter(Boolean),note='';
  if(scope==='landscape'&&m.area){
   const loaded=areaGraphOf(m.area),g=loaded.value;
   if(g){const groups=areaGroups(g),mine=groups.find(x=>x.id===(m.group||m.id)),by=new Map(g.nodes.map(x=>[x.id,x]));members=(mine?.members||[]).map(x=>by.get(x)).filter(Boolean);}
   else note=pending(loaded,'this group');
  }
  const inside=new Set(members.map(x=>x.id)),memberBy=new Map(members.map(x=>[x.id,x]));
  const joins=(scope==='landscape'?[]:graph.edges).filter(e=>e.label==='joins to'&&inside.has(e.from)&&inside.has(e.to));
  body=`${m.description?`<p class="dg-lede">${esc(m.description)}</p>`:''}<p class="desk-small">${esc(m.tagNote||m.subtitle||'')}</p>${note}${members.length?`<h3>In this group</h3>${memberList(members)}`:''}${joins.length?`<h3>How they connect</h3><ul class="dg-list">${joins.map(e=>`<li>${(([verb,on])=>`<strong>${esc(memberBy.get(e.from)?.title||e.from)}</strong> ${esc(verb)} <strong>${esc(memberBy.get(e.to)?.title||e.to)}</strong><small>${on?`on ${esc(on)} · `:''}${esc(e.state||'')}${e.evidence?.basis?' · '+esc(e.evidence.basis):''}</small>`)(joinLabel(e,byId).split(' · '))}</li>`).join('')}</ul>`:''}`;
  actions=scope==='landscape'?btn('Open in its area map','dg-drill',`data-id="${esc(id)}" class="desk-primary"`):level===1||scope==='object'?btn('Show its sources','dg-drill',`data-id="${esc(id)}" class="desk-primary"`):'';
 }else if(m.kind==='area'){
  const own=scope==='area'&&!m.external,area=n||graph.nodes.find(x=>x.id===id)||{},groups=own?areaGroups(graph):(area.groups||[]);
  body=`${area.description?`<p class="dg-lede">${esc(area.description)}</p>`:''}<p class="desk-small">${esc(area.subtitle||m.subtitle||'')}</p>${list('Questions it can answer',area.questions)}${list('Known gaps',area.gaps)}${groups.length?`<h3>Groups</h3><ul class="dg-list">${groups.map(g=>`<li>${btn(esc(g.title),own?'dg-reveal':'dg-drill',`data-id="${esc(g.id)}"`)}<small>${esc(g.description||(g.size?`${g.size} sources`:''))}</small></li>`).join('')}</ul>`:''}${(m.ties||[]).length?`<h3>How it connects</h3><ul class="dg-list">${m.ties.map(t=>`<li><strong>${esc(t.label)}</strong><small>${esc(t.state)} · ${esc(t.basis||'')}</small></li>`).join('')}</ul>`:''}`;
  actions=own?'':btn('Open this area','dg-drill',`data-id="${esc(id)}" class="desk-primary"`);
 }else if(m.kind==='suggestions')body=`<p class="desk-small">${esc(m.subtitle)}</p>`;
 else if(n){
  const facts=[];
  if(n.kind==='relation'){
   eyebrow=ROLES[relationRole(n)]||eyebrow;
   if(n.subject_role)facts.push(['Role in this area',n.subject_role]);
   if(n.time_grain&&n.time_grain!=='none')facts.push(['Time',n.time_grain.replaceAll('_',' ')]);
   if(n.entity_keys?.length)facts.push(['Keyed by',n.entity_keys.filter(k=>!/^_dlt_/.test(k)).join(', ')]);
   if(n.preview)facts.push(['Rows',exactNumber(n.preview.value)]);
  }
  const detailState=['relation','cube','metric'].includes(n.kind)?inspectOf(n):null,d=detailState?.value?.detail;
  const sample=['relation','cube'].includes(n.kind)?sampleOf(n):null;
  body=`${grainText(n)?`<p class="dg-lede">${esc(grainText(n))}</p>`:''}${m.value?`<div class="dg-reading">${esc(m.value)}</div><p class="desk-small">${esc(n.preview?.label||'')}</p>${sparkline(n.series)}`:''}${n.description&&n.description!==grainText(n)?`<p>${esc(n.description)}</p>`:''}${facts.length?`<dl class="dg-facts">${facts.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>`:''}${n.formula?`<h3>${n.kind==='idea'?'Proposed formula':'Formula'}</h3><pre class="dg-code">${esc(n.formula)}</pre>`:''}${n.kind==='metric'&&d?.window?`<p class="desk-small">Window: ${esc(typeof d.window==='string'?d.window:JSON.stringify(d.window))}</p>`:''}${n.kind==='breakdown'&&(n.dimensions?.length||n.measures?.length)?`<p class="desk-small">By ${esc((n.dimensions||[]).join(', ')||'—')} · measuring ${esc((n.measures||[]).join(', ')||'—')}</p>`:''}${['idea','breakdown'].includes(n.kind)?'<p class="desk-small">A proposal from the published dossier, not a registered definition.</p>':''}${['relation','cube'].includes(n.kind)?`<h3>Columns</h3>${d?columnsTable(d.fields):pending(detailState,'columns')}<h3>A few rows</h3>${sample?.value?sampleTable(sample.value):pending(sample,'sample rows')}`:''}${/\s/.test(n.basis||'')?`<p class="desk-small">${esc(n.basis)}</p>`:''}`;
  actions=`${btn('Open details','dg-open',`data-id="${esc(id)}" class="desk-primary"`)}${n.kind==='artifact'?`<a href="/apps/${encodeURIComponent(n.slug)}" target="_blank" rel="noopener">Open app ↗</a>`:''}${btn('Ask Callie','dg-ask',`data-id="${esc(id)}" ${operation?'disabled':''}`)}${btn(pinnedNode(n)?'Pinned':'Pin','dg-pin',`data-id="${esc(id)}" ${pinnedNode(n)||operation?'disabled':''}`)}`;
 }
 const linked=links.length&&m.kind!=='group'?`<h3>Links at this level</h3><ul class="dg-list">${links.map(({e,out,other})=>`<li>${btn(esc(other.title),'dg-reveal',`data-id="${esc(other.id)}"`)}<small>${out?'→':'←'} ${esc(e.label)}${e.state?' · '+esc(e.state):''}</small>${e.evidence?.basis?`<small>${esc(e.evidence.basis)}</small>`:''}</li>`).join('')}</ul>`:'';
 return `<aside class="dg-inspector" aria-label="${esc(m.title)}"><header><div><span class="desk-eyebrow">${esc(eyebrow)}${n?.status&&!['area','group'].includes(m.kind)?' · '+esc(n.status):''}</span><h2>${esc(m.title)}</h2></div>${iconButton('Close details','dg-close','close')}</header><div class="dg-inspector-body">${body}${linked}</div>${actions?`<footer class="desk-actions">${actions}</footer>`:''}</aside>`;
}
function syncMap(){
 const slot=$('meaning-map-slot'),scope=mapScope();
 if(!slot||!scope){if(!slot&&mapMount){mapMount.destroy();mapMount=null;mapHost=null;}return;}
 if(!mapMount){mapHost=document.createElement('div');mapMount=mountDiagram(mapHost,{onPick:pick,onDrill:drill});}
 if(slot.firstChild!==mapHost)slot.replaceChildren(mapHost);
 const level=mapLevel(scope),area=graph.nodes.find(n=>n.kind==='area'&&n.area===route.area),frame=slot.closest('.desk-dg-frame');
 mapMount.update({model:mapModel(scope,level),scope:`${scope}:${route.area}:${scope==='object'?route.node:''}`,level,picked:route.pick,query:mapQuery,crossing:mapCrossing,focus:mapFocus,
  aspect:frame?.clientWidth&&frame?.clientHeight?frame.clientWidth/frame.clientHeight:1.8,wheel:scope==='landscape'?'page':'pan',
  label:scope==='landscape'?'Map of business areas':`${scope==='object'?selected()?.title:area?.title||'Area'} map, level ${level}`,
  empty:scope==='landscape'?'No published business areas yet.':'Nothing to show at this level.'});
 mapFocus='';
}
function pick(id){if(route.pick===id)return;route.pick=id;snapshot();render();}
function setLevel(n,push=true){const scope=mapScope();if(!scope||!levelsFor(scope).some(l=>l.n===n&&l.available)||n===mapLevel(scope)&&route.level)return;remember(scope,n);if(push)navigate({level:n});else{route.level=n;snapshot();render();}}
function stopPlay(){if(playTimer){clearInterval(playTimer);playTimer=null;}}
function togglePlay(){
 if(playTimer){stopPlay();render();return;}
 const scope=mapScope(),levels=levelsFor(scope).filter(l=>l.available).map(l=>l.n);if(levels.length<2)return;
 let i=levels.indexOf(mapLevel(scope));
 const step=()=>{if(mapScope()!==scope){stopPlay();return;}i=(i+1)%levels.length;remember(scope,levels[i]);route.level=levels[i];snapshot();render();};
 playTimer=setInterval(step,2600);step();
}
function openFromMap(id){const n=graph.nodes.find(x=>x.id===id);if(!n)return;navigate({node:id,panel:mapScope()==='object'?'map':'data'});}
function drill(id){
 const scope=mapScope(),m=mapModel(scope).nodes.find(n=>n.id===id);if(!m)return;stopPlay();
 if(scope==='landscape'){if(m.kind==='group'&&m.group)mapFocus=m.group;navigate({area:m.area,node:'',...(m.kind==='group'&&m.group?{level:2,pick:m.group}:{})});return;}
 if(m.kind==='area'){if(m.external&&m.area)navigate({area:m.area,node:''});else if(scope==='object')navigate({node:'',mode:'map',pick:id});return;}
 if(m.kind==='group'){mapFocus=id;if(scope==='object'){navigate({node:'',mode:'map',pick:id,level:2});return;}route.pick=id;const next=Math.max(2,mapLevel(scope));if(next===mapLevel(scope)){snapshot();render();mapMount?.focus(id);}else setLevel(next);return;}
 if(m.kind==='suggestions'){setLevel(3);return;}
 openFromMap(id);
}
function revealMatch(){
 const q=mapQuery.trim().toLowerCase();if(!q)return;
 // Prefer what is inside the map over the area being viewed, whose own
 // title often contains the words people search for.
 const scope=mapScope(),hit=x=>`${x.title} ${x.subtitle||''} ${x.source||''}`.toLowerCase().includes(q),shown=mapModel(scope).nodes.filter(hit),own=shown.find(x=>x.viewing&&x.kind==='area');
 const inner=shown.find(x=>x!==own);
 if(inner){pick(inner.id);mapMount?.focus(inner.id);return;}
 const n=scope==='landscape'?null:graph.nodes.find(x=>x.kind!=='area'&&hit(x));
 if(n){mapHidden.delete(legendKey(n.kind));mapFocus=n.id;route.pick=n.id;const next=Math.max(mapLevel(scope),LEVEL_OF[n.kind]||1);if(next===mapLevel(scope)){snapshot();render();}else setLevel(next);}
 else if(own){pick(own.id);mapMount?.focus(own.id);}
 else toast('Nothing on this map matches “'+mapQuery.trim()+'”.');
}
function toast(text){const t=$('toast');if(!t)return;t.textContent=text;t.hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>{t.hidden=true;},2600);}
function mapAction(a,t){
 const id=t.dataset.id;
 if(a==='dg-level'){stopPlay();setLevel(Number(t.dataset.level));}
 else if(a==='dg-play')togglePlay();
 else if(a==='dg-crossing'){mapCrossing=!mapCrossing;render();}
 else if(a==='dg-legend'){const k=t.dataset.key;mapHidden.has(k)?mapHidden.delete(k):mapHidden.add(k);try{localStorage.setItem('meaning-map-hidden',JSON.stringify([...mapHidden]));}catch{}render();}
 else if(a==='dg-mode')navigate({mode:t.dataset.mode,node:'',pick:''});
 else if(a==='dg-close')pick('');
 else if(a==='dg-reveal'){pick(id);mapMount?.focus(id);}
 else if(a==='dg-drill')drill(id);
 else if(a==='dg-open')openFromMap(id);
 else if(a==='dg-ask'||a==='dg-pin')operationOn(a==='dg-ask'?'discuss':'pin',graph.nodes.find(n=>n.id===id));
}
