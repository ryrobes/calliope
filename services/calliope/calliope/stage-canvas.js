/* Spatial projections of the existing, authorized Stage ledger. No new work runs. */
(() => {
  'use strict';
  const MODES = {stage:'Stage', topics:'Topics', journey:'Journey', evidence:'Evidence'};
  const W = 264, H = 192, GAP = 36, GROUP_HEAD = 78;
  const clean = (v, limit = 180) => String(v ?? '').replace(/\s+/g, ' ').trim().slice(0, limit);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const list = v => Array.isArray(v) ? v : [];
  const titleCase = v => clean(v).replace(/["_-]/g,' ').replace(/\b\w/g, c=>c.toUpperCase());
  const time = s => Date.parse(s.created_at) || 0;
  const order = (a,b) => time(a)-time(b) || Number(a.ordinal||0)-Number(b.ordinal||0) || String(a.id).localeCompare(String(b.id));
  const keyFor = s => s.lineage_key ? `lineage:${s.lineage_key}` : `surface:${s.id}`;
  const refId = r => clean(typeof r==='object' ? r?.surface_id || r?.ref || r?.id : r).replace(/^surface:/,'');
  function topic(s) {
    const src=s.source||{}, area=src.meaning_area;
    if(area?.key || area?.name) return {key:`area:${area.key||area.name}`,label:clean(area.name||titleCase(area.key),90),basis:'Meaning area'};
    const tables=list(s.spatial?.tables).filter(x=>typeof x==='string').sort();
    if(tables.length) return {key:`table:${tables[0]}`,label:titleCase(tables[0].split('.').pop()),basis:`Source · ${tables[0]}`};
    const intent=clean(src.exploration?.intent || src.question,90);
    if(intent) return {key:`question:${intent.toLowerCase()}`,label:intent,basis:'Recorded question'};
    if(s.artifact_slug) return {key:`artifact:${s.artifact_slug}`,label:clean(s.title || titleCase(s.artifact_slug),90),basis:'Artifact family'};
    if(['action','workflow','work_order','decision','input'].includes(s.kind)) return {key:'activity',label:'Team & decisions',basis:'Activity'};
    if(['image','sketch'].includes(s.kind)) return {key:'visual',label:'Visual exploration',basis:'Visual work'};
    return {key:'other',label:'Findings & materials',basis:'Other saved work'};
  }
  function buildGraph(surfaces, turns=[]) {
    const nodes=new Map(list(surfaces).filter(s=>s?.id).map(s=>[String(s.id),s]));
    const families=new Map(), edges=[], seen=new Set();
    const connect=(from,to,kind,label)=>{
      from=refId(from); to=refId(to);
      if(!nodes.has(from)||!nodes.has(to)||from===to)return;
      const key=`${from}:${to}:${kind}`;if(seen.has(key))return;seen.add(key);
      edges.push({from,to,kind,label,key});
    };
    for(const s of nodes.values()) {
      const key=keyFor(s); if(!families.has(key))families.set(key,[]);families.get(key).push(s);
      const origin=s.source?.derived_from;
      if(origin?.surface_id)connect(origin.surface_id,s.id,'derived','Derived from');
      if(s.parent_surface_id && refId(origin?.surface_id)!==String(s.parent_surface_id))connect(s.parent_surface_id,s.id,'revision','Earlier version');
      for(const r of list(s.payload?.evidence_refs))connect(r,s.id,'citation','Cited by');
      if(s.kind==='board')for(const r of [...list(s.payload?.members),...list(s.payload?.cells).map(c=>c.ref)])connect(r,s.id,'member','Included in');
      if(s.kind==='image' && s.payload?.source_surface_id)connect(s.payload.source_surface_id,s.id,'capture','Captured from');
    }
    // Captures reference the exact artifact version, never the latest version.
    const artifacts=new Map();
    for(const s of nodes.values())if(s.kind==='artifact'&&s.artifact_slug&&s.artifact_version!=null)artifacts.set(`${s.artifact_slug}@${s.artifact_version}`,s.id);
    for(const s of nodes.values())if(s.kind==='image'&&s.artifact_slug&&s.artifact_version!=null)connect(artifacts.get(`${s.artifact_slug}@${s.artifact_version}`),s.id,'capture','Captured version');
    for(const f of families.values())f.sort(order);
    const latest=new Set([...families.values()].map(f=>String(f[f.length-1].id)));
    const topics=new Map([...nodes].map(([id,s])=>[id,topic(s)]));
    // Explicit composition/derivation can carry a source grouping to an otherwise
    // unclassified output. Similar words never become provenance edges.
    for(const e of edges)if(['other','visual'].includes(topics.get(e.to).key)&&!['other','visual','activity'].includes(topics.get(e.from).key))topics.set(e.to,topics.get(e.from));
    return {nodes,edges,families,latest,topics,turns:new Map(turns.map(t=>[String(t.id),t]))};
  }
  function projection(graph,mode,selected,versions=false) {
    let ids=[...graph.nodes.keys()].filter(id=>versions||mode==='journey'||graph.latest.has(id)||id===selected);
    if(mode==='evidence'&&selected&&graph.nodes.has(selected)) {
      // Show the whole recorded component, including historical versions. A
      // shared topic alone is not evidence of a connection.
      const neighbors=new Map();
      for(const {from,to} of graph.edges){
        if(!neighbors.has(from))neighbors.set(from,[]);
        if(!neighbors.has(to))neighbors.set(to,[]);
        neighbors.get(from).push(to);neighbors.get(to).push(from);
      }
      const connected=new Set([selected]);
      for(const id of connected)for(const neighbor of neighbors.get(id)||[])connected.add(neighbor);
      ids=[...connected];
    }
    const allowed=new Set(ids);
    return {nodes:ids.map(id=>graph.nodes.get(id)).sort(order),edges:graph.edges.filter(e=>allowed.has(e.from)&&allowed.has(e.to))};
  }
  function layout(graph,projected,mode,selected,previous=new Map()) {
    const positions=new Map(),groups=[];
    if(mode==='evidence'&&selected) {
      const incoming=new Set(graph.edges.filter(e=>e.to===selected).map(e=>e.from));
      const outgoing=new Set(graph.edges.filter(e=>e.from===selected).map(e=>e.to));
      const columns=[projected.nodes.filter(s=>incoming.has(String(s.id))),projected.nodes.filter(s=>String(s.id)===selected),projected.nodes.filter(s=>!incoming.has(String(s.id))&&outgoing.has(String(s.id)))];
      const placed=new Set(columns.flat().map(s=>String(s.id)));
      const others=projected.nodes.filter(s=>!placed.has(String(s.id)));
      if(others.length)columns.push(others);
      columns.forEach((rows,i)=>{
        const used=new Set([...previous.values()].filter(p=>p.group===i).map(p=>p.slot));let next=0,max=0;
        for(const s of rows){const old=previous.get(String(s.id));let slot=old?.group===i?old.slot:null;if(slot==null){while(used.has(next))next++;slot=next++;used.add(slot);}max=Math.max(max,slot);positions.set(String(s.id),{x:i*360,y:GROUP_HEAD+slot*(H+GAP),group:i,slot});}
        groups.push({key:`evidence:${i}`,label:['Inputs & earlier work','Connections around','Uses & later work','Related work'][i],basis:'Recorded links',x:i*360,y:0,w:W,h:GROUP_HEAD+(max+1)*(H+GAP)});
      });
    } else {
      const buckets=new Map();
      for(const s of projected.nodes){const t=graph.turns.get(String(s.turn_id));const group=mode==='journey'?{key:String(s.turn_id),label:`Turn ${t?.ordinal??'·'}`,basis:clean(t?.user_message||t?.assistant_message||s.created_at,100)}:graph.topics.get(String(s.id));if(!buckets.has(group.key))buckets.set(group.key,{...group,nodes:[]});buckets.get(group.key).nodes.push(s);}
      // Reuse slots as results stream in. New arrivals append without shuffling
      // existing cards or the camera; a removed card's slot can remain empty.
      const bucketOrder=[...buckets.values()].sort((a,b)=>{
        const ai=previous.get(`group:${a.key}`)?.index,bi=previous.get(`group:${b.key}`)?.index;
        if(ai!=null||bi!=null)return (ai??1e9)-(bi??1e9);
        return mode==='journey'?order(a.nodes[0],b.nodes[0]):a.label.localeCompare(b.label);
      });
      let groupX=0;
      bucketOrder.forEach((g,i)=>{
        const columns=previous.get(`group:${g.key}`)?.columns || Math.min(mode==='journey'?4:8,Math.max(mode==='journey'?1:2,Math.ceil(Math.sqrt(g.nodes.length*H/W))));
        const span=columns*(W+GAP)+90,x=previous.get(`group:${g.key}`)?.x??groupX;groupX=Math.max(groupX,x+span);
        const used=new Set();
        for(const p of previous.values())if(p?.group===g.key&&p.slot!=null)used.add(p.slot);
        let next=0,max=0;
        for(const s of g.nodes){const old=previous.get(String(s.id));let slot=old?.group===g.key?old.slot:null;if(slot==null){while(used.has(next))next++;slot=next++;used.add(slot);}max=Math.max(max,slot);positions.set(String(s.id),{x:x+(slot%columns)*(W+GAP),y:GROUP_HEAD+Math.floor(slot/columns)*(H+GAP),slot,group:g.key});}
        positions.set(`group:${g.key}`,{index:previous.get(`group:${g.key}`)?.index??i,columns,x});
        groups.push({key:g.key,label:g.label,basis:g.basis,x,y:0,w:columns*W+(columns-1)*GAP,h:GROUP_HEAD+(Math.floor(max/columns)+1)*(H+GAP)});
      });
    }
    const points=[...positions].filter(([id])=>graph.nodes.has(id)).map(([,p])=>p);
    return {positions,groups,width:Math.max(600,...points.map(p=>p.x+W)),height:Math.max(360,...points.map(p=>p.y+H))};
  }
  function create({root,scroll,control,onChange,onSelect,onStage,onOpen,renderSurface,renderDetail,captureDrafts,author,loadContents,loadQueryPreview,exploreQuery,renderResult,renderSql=esc,renderEngine}) {
    let session='',graph=buildGraph([]),view={mode:'stage',selected:null,topic:null,versions:false,cameras:{}},drawing=null,projected=null;
    let fullGraph=graph,historyTime=null,timeChanged=false;
    let menu=false,inspect=false,inspectSource=false,fitPending=false,centerPending=null,stageTop=0,restoreTop=false,resize=null,drag=null,pinches=new Map(),pinch=null;
    let positions=new Map(),positionMode='',frame=0,lastViewport=null,evidenceAnchor=null;
    const drafts=new Map(),navigation=[];let pendingDrafts=null;
    let detailIds=new Set(),detailFrame=0,closeDetail=false;
    let filterIds=null,filterKey='',inside=null,contentsRequest=null;
    const previewCache=new Map(),previewRequests=new Map(),querySurfaces=new Map(),refreshQueries=new Set();
    let visibleQueries=new Set(),previewFrame=0,capturePoll=null;
    const queryKey=(sid,qid)=>`inside:${sid}:${qid}`;
    function cancelInside(){
      contentsRequest?.abort();contentsRequest=null;clearTimeout(capturePoll);capturePoll=null;
      for(const [key,request] of previewRequests){request.abort();if(previewCache.get(key)?.status==='loading')previewCache.delete(key);}
      previewRequests.clear();refreshQueries.clear();visibleQueries=new Set();cancelAnimationFrame(previewFrame);previewFrame=0;
    }
    function previewChanged(){if(!previewFrame)previewFrame=requestAnimationFrame(()=>{previewFrame=0;if(inside)onChange();});}
    function previewState(id){return previewCache.get(queryKey(inside?.surfaceId,id));}
    function requestPreview(q,refresh=false){
      const current=inside,key=queryKey(current.surfaceId,q.id);
      const existing=previewCache.get(key);
      if(!loadQueryPreview||!q.can_explore||previewRequests.has(key)||(!refresh&&existing&&(existing.status!=='ready'||Date.now()-existing.at<300000)))return;
      refreshQueries.delete(key);const request=new AbortController();previewRequests.set(key,request);previewCache.set(key,{status:'loading'});previewChanged();
      loadQueryPreview(current.surfaceId,q.id,request.signal,refresh).then(value=>{
        if(request.signal.aborted)return;
        if(!Array.isArray(value?.result?.rows)||!Array.isArray(value?.result?.columns))throw Error('No query result was returned. Try again.');
        previewCache.delete(key);previewCache.set(key,{status:'ready',value,at:Date.now()});
        querySurfaces.set(key,{id:key,kind:'query',title:q.title,source:{sql:q.sql},payload:value.result});
        while(previewCache.size>64){const old=[...previewCache.keys()].find(k=>!previewRequests.has(k));if(!old)break;previewCache.delete(old);querySurfaces.delete(old);}
      }).catch(error=>{if(!request.signal.aborted)previewCache.set(key,{status:'error',error:error.message||'Unable to read this query.'});})
        .finally(()=>{if(previewRequests.get(key)===request)previewRequests.delete(key);if(!request.signal.aborted&&inside===current)previewChanged();});
    }
    function syncPreviews(){
      if(!inside?.data||!lastViewport)return;
      const c=camera(),next=new Set(),w=lastViewport.clientWidth,h=lastViewport.clientHeight;
      for(const q of inside.data.items){const p=positions.get(q.id);if(p&&c.z>=.32&&p.x*c.z+c.x<w&&(p.x+344)*c.z+c.x>0&&p.y*c.z+c.y<h&&(p.y+278)*c.z+c.y>0)next.add(q.id);}
      if(inside.selected)next.add(inside.selected);if(inside.opened)next.add(inside.opened);
      if(next.size!==visibleQueries.size||[...next].some(id=>!visibleQueries.has(id))){visibleQueries=next;previewChanged();}
      const ordered=[...inside.data.items].sort((a,b)=>Number(b.id===inside.opened||b.id===inside.selected)-Number(a.id===inside.opened||a.id===inside.selected));
      for(const q of ordered){if(previewRequests.size>=2)break;if(next.has(q.id))requestPreview(q,refreshQueries.has(queryKey(inside.surfaceId,q.id)));}
    }
    function sourceVersion(surface){
      if(!surface?.artifact_slug||!surface.artifact_version)return null;
      if(surface.kind==='artifact')return surface;
      if(surface.kind!=='image')return null;
      return [...graph.nodes.values()].find(s=>s.kind==='artifact'&&s.artifact_slug===surface.artifact_slug&&s.artifact_version===surface.artifact_version)||surface;
    }
    const nodeLabel=s=>s.kind==='artifact'?`Dashboard · v${s.artifact_version}`:s.kind==='image'&&sourceVersion(s)?`Screenshot · v${s.artifact_version}`:titleCase(s.kind);
    function openQuery(id){if(!inside?.data?.items.some(q=>q.id===id))return;rememberView();inside.selected=id;inside.opened=id;inside.showSql=false;onChange();}
    function goBack(){const restore=navigation.pop();setMode(restore?.mode||'stage',{remember:false,restore});}
    const cameraKey=()=>inside?'inside:'+inside.surfaceId:filterIds?'search:'+view.mode:view.mode==='topics'&&view.topic?'topic:'+view.topic:view.mode;
    function setFilter(ids,key=''){
      if(key!==filterKey){
        const wasFiltered=Boolean(filterIds);filterKey=key;filterIds=ids;
        if(!timeChanged)for(const name of Object.keys(view.cameras))if(name.startsWith('search:'))delete view.cameras[name];
        if(!inside&&!timeChanged){positions=new Map();positionMode='';inspect=false;detailIds.clear();centerPending=null;
          fitPending=Boolean(ids)||wasFiltered&&!view.cameras[cameraKey()];}
      }else filterIds=ids;
    }
    const camera=()=>view.cameras[cameraKey()]||(view.cameras[cameraKey()]={x:36,y:70,z:1});
    function save(){if(!session)return;try{localStorage.setItem(`calliope-stage-map-v1:${session}`,JSON.stringify(view));}catch{}}
    function historicalId(id){
      if(graph.nodes.has(id))return id;
      const original=fullGraph.nodes.get(id);
      return original?[...(fullGraph.families.get(keyFor(original))||[])].reverse().find(s=>s.kind===original.kind&&graph.nodes.has(String(s.id)))?.id||null:null;
    }
    function update(sid,surfaces,turns,eligible=surfaces,cutoff=null){
      timeChanged=historyTime!==cutoff;historyTime=cutoff;
      if(timeChanged){inspect=false;detailIds.clear();centerPending=null;}
      if(session!==String(sid||'')){
        session=String(sid||'');view={mode:'stage',selected:null,topic:null,versions:false,cameras:{}};
        try{const saved=JSON.parse(localStorage.getItem(`calliope-stage-map-v1:${session}`)||'null');if(saved&&Object.hasOwn(MODES,saved.mode)){view.mode=saved.mode;view.versions=saved.versions===true;view.selected=typeof saved.selected==='string'?saved.selected:null;for(const [k,c]of Object.entries(saved.cameras||{}))if(Object.hasOwn(MODES,k)&&[c.x,c.y,c.z].every(Number.isFinite))view.cameras[k]={x:Math.max(-1e7,Math.min(1e7,c.x)),y:Math.max(-1e7,Math.min(1e7,c.y)),z:Math.max(.04,Math.min(3,c.z))};}}catch{}
        cancelInside();previewCache.clear();querySurfaces.clear();inside=null;
        inspect=false;evidenceAnchor=null;positions=new Map();positionMode='';drafts.clear();navigation.length=0;stageTop=0;fitPending=view.mode!=='stage'&&!view.cameras[view.mode];
        detailIds.clear();closeDetail=false;cancelAnimationFrame(detailFrame);detailFrame=0;
      }
      fullGraph=buildGraph(surfaces,turns);graph=eligible===surfaces?fullGraph:buildGraph(eligible,turns);
      if(!graph.nodes.has(view.selected)){const prior=view.selected;view.selected=historicalId(prior);inspect=false;if(prior)onSelect?.(view.selected);}
      if(!fullGraph.nodes.has(evidenceAnchor))evidenceAnchor=null;
      if(view.mode==='evidence'){
        if(!evidenceAnchor)evidenceAnchor=view.selected||[...fullGraph.nodes.values()].sort(order).reverse().find(s=>['artifact','answer','board','chart','map'].includes(s.kind))?.id||[...fullGraph.latest].at(-1)||null;
      }
      if(inside&&!graph.nodes.has(inside.surfaceId)){cancelInside();inside=null;}
      control.hidden=!session;
      paintControl();
    }
    const icon='<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="2" width="5" height="5" rx="1"/><rect x="13" y="3" width="5" height="5" rx="1"/><rect x="7" y="13" width="6" height="5" rx="1"/><path d="M7 5h6M5 7l4 6m6-5-4 5"/></svg>';
    function paintControl(){
      const context=document.getElementById('stage-view-context'),anchor=graph.nodes.get(historicalId(evidenceAnchor))||fullGraph.nodes.get(evidenceAnchor),parent=graph.nodes.get(inside?.surfaceId);
      if(context){context.hidden=!(inside||view.mode==='evidence'&&anchor);context.textContent=inside?`Inside ${parent?.title||'artifact'} · v${parent?.artifact_version||''}`:view.mode==='evidence'&&anchor?`Connections around ${anchor.title}`:'';}
      const rail=control.closest('.stage-rail');if(rail)rail.dataset.inside=String(Boolean(inside));
      const markup=`<button type="button" class="stage-view-trigger" aria-label="Stage view: ${MODES[view.mode]}" title="Change Stage view" aria-expanded="${menu}" aria-controls="stage-view-menu">${icon}${view.mode!=='stage'?`<span>${MODES[view.mode]}</span>`:''}</button><button type="button" class="stage-view-back" data-stage-back aria-label="Back to previous view" title="Back" ${view.mode==='stage'&&!navigation.length?'disabled':''}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m12 4-6 6 6 6"/></svg></button><div id="stage-view-menu" class="stage-view-menu" role="menu" aria-label="Stage view" ${menu?'':'hidden'}>${Object.entries(MODES).map(([k,label])=>`<button type="button" role="menuitemradio" aria-checked="${view.mode===k}" data-stage-mode="${k}"><b>${label}</b><small>${{stage:'Chronological Stage',topics:'Questions and shared sources',journey:'Turns and revisions',evidence:'Connections around one artifact'}[k]}</small></button>`).join('')}</div>`;
      if(control.dataset.markup!==markup){control.innerHTML=markup;control.dataset.markup=markup;}
    }
    function rememberView(){
      navigation.push({mode:view.mode,topic:view.topic,selected:view.selected,versions:view.versions,evidenceAnchor,inside:inside?{...inside}:null,filtered:Boolean(filterIds),camera:{...camera()}});
      if(navigation.length>40)navigation.shift();
    }
    function setMode(mode,{remember=true,restore=null}={}){
      if(!Object.hasOwn(MODES,mode))return;
      if(mode===view.mode&&!restore&&!inside){menu=false;paintControl();return;}
      if(remember)rememberView();
      drafts.set(view.mode,captureDrafts?.());pendingDrafts=drafts.get(mode)||null;
      if(view.mode==='stage'&&mode!=='stage')stageTop=scroll.scrollTop;
      restoreTop=mode==='stage'&&view.mode!=='stage';menu=false;inspect=false;view.mode=mode;
      cancelInside();inside=restore?.inside?{...restore.inside}:null;
      if(restore){view.topic=restore.topic;view.selected=restore.selected;view.versions=restore.versions;evidenceAnchor=restore.evidenceAnchor;if(restore.filtered===Boolean(filterIds))view.cameras[cameraKey()]={...restore.camera};onSelect?.(view.selected);}
      else if(mode==='evidence'){evidenceAnchor=view.selected;}
      positions=new Map();positionMode='';centerPending=null;fitPending=mode!=='stage'&&!view.cameras[cameraKey()];
      detailIds.clear();closeDetail=false;cancelAnimationFrame(detailFrame);detailFrame=0;
      save();onChange();control.querySelector('.stage-view-trigger')?.focus({preventScroll:true});
      if(inside&&(!inside.data||['loading','busy'].includes(inside.data.capture_status)))fetchContents();
    }
    async function fetchContents(){
      const current=inside,parent=graph.nodes.get(current?.surfaceId);if(!current||!parent||!loadContents)return;
      contentsRequest?.abort();const request=new AbortController();contentsRequest=request;current.loading=true;current.error=null;onChange();
      try{const data=await loadContents(parent,request.signal);if(request.signal.aborted||inside!==current)return;
        if(String(data.surface_id)!==String(parent.id)||data.slug!==parent.artifact_slug||Number(data.version)!==Number(parent.artifact_version))throw Error('This query inventory does not match the selected artifact version.');
        fitPending=!current.data;current.data=data;current.loading=false;onChange();
        clearTimeout(capturePoll);if(['loading','busy'].includes(data.capture_status))capturePoll=setTimeout(()=>{if(inside===current)fetchContents();},2500);
      }catch(error){if(request.signal.aborted||inside!==current)return;current.error=error.message||'Unable to load this artifact.';current.loading=false;onChange();}
    }
    function unwrap(id){
      const parent=sourceVersion(graph.nodes.get(id));if(!parent||!loadContents)return;id=String(parent.id);
      rememberView();if(view.mode==='stage'){stageTop=scroll.scrollTop;view.mode='evidence';}
      cancelInside();inside={surfaceId:id,data:null,selected:null,opened:null,showSql:false,loading:true};inspect=false;detailIds.clear();centerPending=null;fitPending=true;
      fetchContents();
    }
    function insideMarkup(){
      const previews=new Map((inside.data?.items||[]).map(q=>[q.id,previewState(q.id)]));
      const output=inside.data?globalThis.CalliopeStageUnwrap.render(inside.data,inside.selected,inside.showSql,renderSql,renderEngine,{previews,visible:visibleQueries,opened:inside.opened,renderResult:(q,value,expanded)=>renderResult?.(querySurfaces.get(queryKey(inside.surfaceId,q.id)),expanded)}):null;
      projected={nodes:[],edges:[]};drawing={width:output?.width||500,height:output?.height||300,positions:output?.positions||new Map(),groups:[]};positions=drawing.positions;
      return `<section class="stage-canvas stage-canvas-inside" aria-label="Inside published artifact" data-stage-key="canvas-inside">
        <div class="stage-canvas-viewport" tabindex="0" role="region" aria-label="Internal queries. Click empty space to clear selection. Drag to pan and scroll to zoom.">
          <div class="stage-canvas-world" style="width:${drawing.width}px;height:${drawing.height}px" data-stage-key="canvas-world">${output?.world||''}</div>
          ${!output?`<div class="canvas-empty" role="status">${inside.loading?'Loading query definitions…':`${esc(inside.error)} <button type="button" data-unwrap-retry>Try again</button>`}</div>`:''}
        </div>
        <div class="canvas-toolbar"><span class="canvas-count">${output?`${output.count} versioned definitions`:'Inside artifact'}</span><button type="button" data-canvas-action="out" aria-label="Zoom out">−</button><span data-canvas-zoom>100%</span><button type="button" data-canvas-action="in" aria-label="Zoom in">+</button><button type="button" data-canvas-action="fit">Fit</button></div>
        ${output?.details||`<p class="unwrap-note">${esc(output?.note||'Opening saved query definitions…')}</p>`}${output?.inspector||''}
      </section>`;
    }
    control.addEventListener('click',e=>{if(e.target.closest('[data-stage-back]')){goBack();return;}const mode=e.target.closest('[data-stage-mode]');if(mode){setMode(mode.dataset.stageMode);return;}if(e.target.closest('.stage-view-trigger')){menu=!menu;paintControl();if(menu)control.querySelector('[aria-checked="true"]')?.focus();}});
    control.addEventListener('keydown',e=>{if(e.key==='Escape'){menu=false;paintControl();control.querySelector('button')?.focus();}if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();menu=true;paintControl();const buttons=[...control.querySelectorAll('[data-stage-mode]')],i=buttons.indexOf(document.activeElement);buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowUp'?-1:1)+buttons.length)%buttons.length]?.focus();}});
    document.addEventListener('pointerdown',e=>{if(menu&&!control.contains(e.target)){menu=false;paintControl();}});
    function thumbnail(s){
      let url=s.payload?.image_url||s.payload?.thumbnail_url;
      if(s.kind==='artifact'){const capture=[...graph.nodes.values()].filter(c=>c.kind==='image'&&c.artifact_slug===s.artifact_slug&&c.artifact_version===s.artifact_version&&c.payload?.image_url).sort(order).at(-1);url=capture?.payload?.image_url;}
      if(!url)return '';
      try{const u=new URL(url,location.origin);return ['http:','https:'].includes(u.protocol)?u.href:'';}catch{return '';}
    }
    function preview(s){
      const image=thumbnail(s);if(image)return `<img src="${esc(image)}" alt="" loading="lazy" decoding="async">`;
      if(s.kind==='query'){
        const columns=list(s.payload?.columns).slice(0,3).map(c=>typeof c==='object'?c.name:c);
        const rows=list(s.payload?.rows).slice(0,3);
        return columns.length?`<table aria-hidden="true"><thead><tr>${columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${columns.map((c,i)=>`<td>${esc(clean(Array.isArray(r)?r[i]:r?.[c],30))}</td>`).join('')}</tr>`).join('')}</tbody></table>`:'';
      }
      return `<p>${esc(clean(s.payload?.answer||s.payload?.subtitle||s.source?.exploration?.intent||s.source?.question||s.payload?.description||s.payload?.summary||list(s.spatial?.columns).join(' · '),190))}</p>`;
    }
    function markup(){
      if(view.mode==='stage')return '';
      if(inside)return insideMarkup();
      let focus=view.mode==='evidence'?historicalId(evidenceAnchor):view.selected;
      if(filterIds&&!filterIds.has(String(focus)))focus=null;
      projected=filterIds
        ? {nodes:[...graph.nodes.values()].filter(s=>filterIds.has(String(s.id))).sort(order),edges:graph.edges.filter(e=>filterIds.has(e.from)&&filterIds.has(e.to))}
        : view.mode==='evidence'&&evidenceAnchor&&!focus?{nodes:[],edges:[]}:projection(graph,view.mode,focus,view.versions);
      if(!filterIds&&view.mode==='topics'&&view.topic){projected.nodes=projected.nodes.filter(s=>graph.topics.get(String(s.id)).key===view.topic);const ids=new Set(projected.nodes.map(s=>String(s.id)));projected.edges=projected.edges.filter(e=>ids.has(e.from)&&ids.has(e.to));}
      if(positionMode!==cameraKey()){positions=new Map();positionMode=cameraKey();}
      drawing=layout(fullGraph,projected,view.mode,view.mode==='evidence'?evidenceAnchor:focus,positions);
      positions=new Map([...positions,...drawing.positions]);drawing.positions=positions;
      const overview=!filterIds&&view.mode==='topics'&&!view.topic;
      const clusters=[];
      if(overview){
        const buckets=new Map();for(const s of projected.nodes){const g=graph.topics.get(String(s.id));if(!buckets.has(g.key))buckets.set(g.key,{...g,nodes:[]});buckets.get(g.key).nodes.push(s);}
        [...buckets.values()].sort((a,b)=>a.label.localeCompare(b.label)).forEach(g=>{const key=`topic:${g.key}`,i=positions.get(key)?.slot??[...positions.keys()].filter(k=>k.startsWith('topic:')).length;positions.set(key,{slot:i});clusters.push({...g,x:(i%3)*320,y:Math.floor(i/3)*250});});
        drawing={positions:new Map(),groups:[],width:Math.max(280,...clusters.map(g=>g.x+280)),height:Math.max(220,...clusters.map(g=>g.y+220))};
      }
      const selected=(!filterIds||filterIds.has(String(view.selected)))&&graph.nodes.get(view.selected),related=new Set([view.selected]);
      for(const e of graph.edges)if(e.from===view.selected||e.to===view.selected){related.add(e.from);related.add(e.to);}
      const edges=overview?[]:projected.edges.filter(e=>view.mode==='journey'||view.mode==='evidence'||e.from===view.selected||e.to===view.selected);
      const lines=edges.map(e=>{const a=positions.get(e.from),b=positions.get(e.to);if(!a||!b)return '';const right=b.x>=a.x,x1=a.x+(right?W:0),x2=b.x+(right?0:W),y1=a.y+H/2,y2=b.y+H/2,bend=(x1+x2)/2;return `<path class="canvas-edge ${e.kind}" d="M${x1},${y1} C${bend},${y1} ${bend},${y2} ${x2},${y2}" marker-end="url(#canvas-arrow)"><title>${esc(`${graph.nodes.get(e.from).title} → ${graph.nodes.get(e.to).title} · ${e.label}`)}</title></path>`;}).join('');
      const connections=(filterIds?projected.edges:graph.edges).filter(e=>e.from===view.selected||e.to===view.selected);
      return `<section class="stage-canvas" aria-label="${MODES[view.mode]} canvas" data-stage-key="canvas">
        <div class="stage-canvas-viewport" tabindex="0" role="region" aria-label="Artifact canvas. Click empty space to clear selection. Drag to pan. Scroll to zoom. Use plus and minus to zoom, Home to fit, and arrow keys to pan.">
          <div class="stage-canvas-world" style="width:${drawing.width}px;height:${drawing.height}px;--canvas-group-head:${GROUP_HEAD}px" data-stage-key="canvas-world">
            <svg class="stage-canvas-edges" width="${drawing.width}" height="${drawing.height}" aria-hidden="true"><defs><marker id="canvas-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z"/></marker></defs>${lines}</svg>
            ${drawing.groups.map(g=>`<div class="canvas-group" data-stage-key="group:${esc(g.key)}" style="left:${g.x-14}px;top:${g.y}px;width:${g.w+28}px;height:${g.h+12}px"><header><b>${esc(g.label)}</b><small>${esc(g.basis)}</small></header></div>`).join('')}
            ${overview?clusters.map(g=>`<button type="button" class="canvas-topic" data-stage-key="topic:${esc(g.key)}" data-canvas-group="${esc(g.key)}" style="left:${g.x}px;top:${g.y}px"><small>${esc(g.basis)}</small><strong>${esc(g.label)}</strong><span class="canvas-topic-count">${g.nodes.length} artifact${g.nodes.length===1?'':'s'} <i aria-hidden="true">↗</i></span><span class="canvas-topic-titles">${g.nodes.slice(-3).reverse().map(s=>`<span>${esc(s.title)}</span>`).join('')}</span></button>`).join(''):projected.nodes.map(s=>{const p=positions.get(String(s.id)),f=graph.families.get(keyFor(s)),rich=!inspect&&detailIds.has(String(s.id));return `<button type="button" class="canvas-card ${rich?'has-detail':''} ${view.selected===s.id?'is-selected':''} ${!filterIds&&view.selected&&!related.has(s.id)?'is-dimmed':''}" data-stage-key="canvas:${esc(s.id)}" data-canvas-select="${esc(s.id)}" data-node-kind="${s.kind==='image'&&sourceVersion(s)?'capture':esc(s.kind)}" style="left:${p.x}px;top:${p.y}px" aria-pressed="${view.selected===s.id}" aria-label="${esc(s.title||s.kind)}"><span class="canvas-card-meta"><span>${esc(nodeLabel(s))}</span><span>${esc(rich?'Open to interact':author?.(s)||s.source?.specialist_key||'Callie')}</span></span><strong>${esc(s.title||titleCase(s.kind))}</strong><span class="canvas-card-preview">${preview(s)}</span><span class="canvas-card-foot"><time>${esc(time(s)?new Date(time(s)).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'Saved artifact')}</time>${f.length>1?`<span>${f.indexOf(s)+1}/${f.length}</span>`:''}</span></button>${rich?`<div class="canvas-card-detail" data-stage-key="canvas-detail:${esc(s.id)}" data-surface-id="${esc(s.id)}" style="left:${p.x+8}px;top:${p.y+40}px" inert aria-hidden="true"><div class="canvas-detail-body">${renderDetail(s)}</div></div>`:''}`;}).join('')}
          </div>
          ${!projected.nodes.length?`<div class="canvas-empty">${historyTime!==null?'No matching saved work at this point. Move forward or return to Now.':filterIds?'No matching work. Try another phrase or clear the filters above.':'Artifacts will appear here as the conversation develops.'}</div>`:''}
        </div>
        <div class="canvas-toolbar">${!filterIds&&view.mode==='topics'&&view.topic?'<button type="button" data-canvas-action="topics">← All topics</button>':''}<span class="canvas-count">${projected.nodes.length} ${filterIds?'matches':'artifacts'}${overview?` · ${clusters.length} topics`:view.mode==='evidence'?' · recorded links':''}</span>${view.mode==='evidence'||filterIds?'':`<button type="button" data-canvas-action="versions" aria-pressed="${view.versions||view.mode==='journey'}" ${view.mode==='journey'?'disabled':''}>Versions</button>`}<button type="button" data-canvas-action="out" aria-label="Zoom out">−</button><span data-canvas-zoom>100%</span><button type="button" data-canvas-action="in" aria-label="Zoom in">+</button><button type="button" data-canvas-action="fit">Fit</button></div>
        ${selected?`<aside class="canvas-selection" aria-label="Selected artifact" data-stage-key="canvas-selection"><div class="canvas-selection-head"><strong>${esc(selected.title)}</strong>${sourceVersion(selected)?`<button type="button" data-stage-unwrap="${esc(selected.id)}">${selected.kind==='image'?'Unwrap source':'Unwrap'}</button>${selected.kind==='image'?`<button type="button" data-canvas-source="${esc(selected.id)}">Open source version</button>`:''}`:''}<button type="button" data-canvas-action="inspect">Open</button><button type="button" data-canvas-action="stage">In Stage</button>${view.mode==='evidence'&&!filterIds?`<button type="button" data-canvas-action="follow" ${evidenceAnchor===view.selected?'disabled':''}>Follow connections</button>`:''}<button type="button" data-canvas-action="clear" aria-label="Clear canvas selection">×</button></div><div class="canvas-connections">${connections.length?connections.slice(0,30).map(e=>{const incoming=e.to===view.selected,other=graph.nodes.get(incoming?e.from:e.to);return `<button type="button" data-canvas-link="${esc(other.id)}"><span>${esc(incoming?{revision:'Earlier version',derived:'Derived from',citation:'Cites',member:'Includes',capture:'Capture of'}[e.kind]:e.label)}</span>${esc(other.title)}</button>`;}).join(''):`<span>${filterIds?'No recorded connections among these matches. Clear the search to see all connections.':'No explicit connections recorded for this artifact.'}</span>`}${connections.length>30?`<span>+${connections.length-30} more connections in the canvas</span>`:''}</div></aside>`:''}
        ${inspect&&selected?`<section class="canvas-inspector" role="region" aria-label="Open artifact" data-stage-key="canvas-inspector"><header><span>Artifact</span><button type="button" data-canvas-action="close" aria-label="Close artifact">Back to canvas ×</button></header><div class="canvas-inspector-body">${renderSurface(inspectSource?{...selected,kind:'artifact'}:selected)}</div></section>`:''}
      </section>`;
    }
    function fit(initial=false){if(!drawing||!lastViewport)return;const c=camera(),w=lastViewport.clientWidth,h=lastViewport.clientHeight;c.z=Math.max(.04,Math.min(1,(w-80)/drawing.width,(h-90)/drawing.height));c.x=(w-drawing.width*c.z)/2;c.y=(h-drawing.height*c.z)/2+14;
      if(initial&&inside){c.z=Math.max(.55,Math.min(1,(w-60)/drawing.width));c.x=(w-drawing.width*c.z)/2;c.y=22;paintCamera();save();return;}
      if(initial&&!inside&&!filterIds&&view.mode!=='evidence'&&!(view.mode==='topics'&&!view.topic)&&c.z<.6){c.z=.8;const focus=view.selected||[...projected.nodes].reverse().find(s=>['artifact','answer','board'].includes(s.kind))?.id||projected.nodes.at(-1)?.id;center(focus);}else paintCamera();save();}
    function center(id){const p=positions.get(String(id));if(!p||!lastViewport)return;const c=camera();c.z=Math.min(1,Math.max(.7,c.z));c.x=(lastViewport.clientWidth-W*c.z)/2-p.x*c.z;c.y=(lastViewport.clientHeight-H*c.z)/2-p.y*c.z;paintCamera();save();}
    function zoom(factor,x=lastViewport?.clientWidth/2,y=lastViewport?.clientHeight/2){if(!lastViewport)return;const c=camera(),z=Math.max(.04,Math.min(3,c.z*factor)),k=z/c.z;c.x=x-(x-c.x)*k;c.y=y-(y-c.y)*k;c.z=z;paintCamera();}
    function paintCamera(){
      const world=root.querySelector('.stage-canvas-world');if(!world||!lastViewport)return;const c=camera();world.style.transform=`translate(${c.x}px,${c.y}px) scale(${c.z})`;
      root.querySelector('[data-canvas-zoom]').textContent=`${Math.round(c.z*100)}%`;
      closeDetail=c.z>=(closeDetail?1.3:1.5);
      root.querySelector('.stage-canvas').dataset.detail=closeDetail?'close':c.z<.32?'overview':c.z<.6?'compact':'full';
      if(inside){syncPreviews();return;}
      const width=lastViewport.clientWidth,height=lastViewport.clientHeight;
      for(const card of root.querySelectorAll('[data-canvas-select]')){const p=positions.get(card.dataset.canvasSelect);if(!p)continue;const visible=(p.x+W)*c.z+c.x>-300&&p.x*c.z+c.x<width+300&&(p.y+H)*c.z+c.y>-250&&p.y*c.z+c.y<height+250;card.style.visibility=visible?'':'hidden';}
      // Help headings at overview scales, but let them grow with the cards
      // above 100%. Their bottom edge stays anchored just above the first row.
      root.querySelector('.stage-canvas').dataset.labelDetail=c.z<.72?'compact':'full';
      for(const label of root.querySelectorAll('.canvas-group header'))label.style.setProperty('--canvas-label-scale',Math.max(1,Math.min(1.75,1/c.z)));
      // Native renderers only enter the DOM for nearby close-up cards. Keeping
      // their budget small bounds dashboard reads, timers, and grid instances.
      const candidates=historyTime===null&&closeDetail&&renderDetail&&!inspect&&(filterIds||!(view.mode==='topics'&&!view.topic))
        ? projected.nodes.filter(s=>['query','artifact'].includes(s.kind)).map(s=>{
          const p=positions.get(String(s.id));
          return {id:String(s.id),x:p.x*c.z+c.x,y:p.y*c.z+c.y};
        }).filter(p=>p.x+W*c.z>0&&p.x<width&&p.y+H*c.z>0&&p.y<height)
          .sort((a,b)=>Math.hypot(a.x+W*c.z/2-width/2,a.y+H*c.z/2-height/2)-Math.hypot(b.x+W*c.z/2-width/2,b.y+H*c.z/2-height/2))
        : [];
      const next=new Set(candidates.slice(0,3).map(p=>p.id));
      if(next.size!==detailIds.size||[...next].some(id=>!detailIds.has(id))){
        detailIds=next;
        if(!detailFrame)detailFrame=requestAnimationFrame(()=>{detailFrame=0;if(view.mode!=='stage')onChange();});
      }
    }
    function mounted(){
      scroll.classList.toggle('is-canvas',view.mode!=='stage');
      if(view.mode==='stage'){detailIds.clear();closeDetail=false;cancelAnimationFrame(detailFrame);detailFrame=0;resize?.disconnect();lastViewport=null;if(restoreTop){scroll.scrollTop=stageTop;restoreTop=false;}return;}
      scroll.scrollTop=0;const viewport=root.querySelector('.stage-canvas-viewport');
      if(lastViewport!==viewport){resize?.disconnect();lastViewport=viewport;resize=new ResizeObserver(()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{if(fitPending){fitPending=false;fit(true);}else paintCamera();});});resize.observe(viewport);}
      if(fitPending&&viewport.clientWidth){fitPending=false;fit(true);}else paintCamera();
      if(centerPending){center(centerPending);centerPending=null;}
    }
    function select(id,{open=false,centerView=false,source=false}={}){
      if(!graph.nodes.has(id))return;
      if(open)rememberView();
      // A citation or Data origin opens a real Stage surface, even when the
      // current canvas is looking inside another artifact.
      if(inside){if(!open)rememberView();cancelInside();inside=null;positions=new Map();positionMode='';}
      if(open&&!filterIds&&view.mode==='evidence'&&!projection(graph,view.mode,evidenceAnchor,view.versions).nodes.some(s=>String(s.id)===id)){
        evidenceAnchor=id;positions=new Map();positionMode='';
      }
      if(!filterIds&&view.mode==='topics'&&view.topic!==graph.topics.get(id).key){view.topic=graph.topics.get(id).key;fitPending=true;}
      view.selected=id;inspectSource=source;inspect=open&&!onOpen?.(graph.nodes.get(id));centerPending=centerView?id:null;onSelect?.(id);save();onChange();
    }
    function clearSelection(){view.selected=null;inspect=false;onSelect?.(null);onChange();save();}
    root.addEventListener('click',e=>{
      const unwrapping=e.target.closest('[data-stage-unwrap]');if(unwrapping){e.preventDefault();e.stopPropagation();unwrap(unwrapping.dataset.stageUnwrap);return;}
      if(view.mode==='stage')return;
      const source=e.target.closest('[data-canvas-source]');if(source){const target=sourceVersion(graph.nodes.get(source.dataset.canvasSource));if(target){select(String(target.id),{open:true,centerView:true,source:true});}return;}
      if(inside){
        const item=e.target.closest('[data-unwrap-item]'),explore=e.target.closest('[data-unwrap-explore]');
        if(item){if(e.detail>1)return;inside.selected=inside.selected===item.dataset.unwrapItem?null:item.dataset.unwrapItem;inside.showSql=false;onChange();return;}
        if(explore){exploreQuery?.(inside.surfaceId,explore.dataset.unwrapExplore,previewState(explore.dataset.unwrapExplore)?.value?.preview_id);return;}
        const open=e.target.closest('[data-unwrap-open]'),refresh=e.target.closest('[data-unwrap-refresh]');
        if(open){openQuery(open.dataset.unwrapOpen);return;}
        if(e.target.closest('[data-unwrap-close]')){goBack();return;}
        if(refresh){const q=inside.data.items.find(q=>q.id===refresh.dataset.unwrapRefresh);if(q){refreshQueries.add(queryKey(inside.surfaceId,q.id));syncPreviews();}return;}
        if(e.target.closest('[data-unwrap-sql]')){inside.showSql=!inside.showSql;onChange();return;}
        if(e.target.closest('[data-unwrap-clear]')){inside.selected=null;inside.showSql=false;onChange();return;}
        if(e.target.closest('[data-unwrap-retry]')){fetchContents();return;}
      }
      const group=e.target.closest('[data-canvas-group]');if(group){rememberView();view.topic=group.dataset.canvasGroup;view.selected=null;inspect=false;fitPending=true;onSelect?.(null);onChange();save();return;}
      const card=e.target.closest('[data-canvas-select]'),link=e.target.closest('[data-canvas-link]'),action=e.target.closest('[data-canvas-action]');
      if(card){if(e.detail>1)return;if(view.selected===card.dataset.canvasSelect)clearSelection();else select(card.dataset.canvasSelect);return;}if(link){select(link.dataset.canvasLink);return;}
      if(!action)return;const a=action.dataset.canvasAction;
      if(a==='topics'){rememberView();view.topic=null;view.selected=null;inspect=false;onSelect?.(null);onChange();save();return;}
      if(a==='in'||a==='out'){zoom(a==='in'?1.25:.8);save();return;}if(a==='fit'){fit();return;}
      if(a==='versions'){view.versions=!view.versions;onChange();save();return;}
      if(a==='follow'){rememberView();evidenceAnchor=view.selected;positions=new Map();positionMode='';fitPending=true;onChange();return;}
      if(a==='stage'){const id=view.selected;setMode('stage');onStage?.(id);return;}
      if(a==='inspect'){select(view.selected,{open:true});root.querySelector('[data-canvas-action="close"]')?.focus({preventScroll:true});return;}
      if(a==='close'){goBack();root.querySelector('[data-canvas-action="inspect"]')?.focus({preventScroll:true});return;}
      if(a==='clear')clearSelection();
    });
    root.addEventListener('contextmenu',e=>{if(view.mode==='stage'||!e.target.closest('.stage-canvas'))return;e.preventDefault();e.stopPropagation();goBack();});
    root.addEventListener('dblclick',e=>{const q=e.target.closest('[data-unwrap-item]');if(q&&inside){openQuery(q.dataset.unwrapItem);return;}const c=e.target.closest('[data-canvas-select]');if(c)select(c.dataset.canvasSelect,{open:true});});
    root.addEventListener('wheel',e=>{if(view.mode==='stage'||!e.target.closest('.stage-canvas-viewport'))return;e.preventDefault();const r=lastViewport.getBoundingClientRect();zoom(Math.exp(-e.deltaY*((e.ctrlKey||e.metaKey)? .007 : .0025)),e.clientX-r.left,e.clientY-r.top);save();},{passive:false});
    root.addEventListener('pointerdown',e=>{
      if(view.mode==='stage'||!e.target.closest('.stage-canvas-viewport')||e.button!==0||e.target.closest('.canvas-card,.canvas-topic,button,a,input,textarea,select,[contenteditable="true"]'))return;
      e.preventDefault();lastViewport.focus({preventScroll:true});lastViewport.setPointerCapture(e.pointerId);pinches.set(e.pointerId,{x:e.clientX,y:e.clientY});
      if(pinches.size>1){const [a,b]=[...pinches.values()];pinch={distance:Math.hypot(a.x-b.x,a.y-b.y)};drag=null;}else drag={id:e.pointerId,x:e.clientX,y:e.clientY,cx:camera().x,cy:camera().y,moved:false};
    });
    root.addEventListener('pointermove',e=>{
      if(!pinches.has(e.pointerId))return;pinches.set(e.pointerId,{x:e.clientX,y:e.clientY});
      if(pinches.size===2&&pinch){const [a,b]=[...pinches.values()],distance=Math.hypot(a.x-b.x,a.y-b.y),r=lastViewport.getBoundingClientRect();zoom(distance/Math.max(1,pinch.distance),(a.x+b.x)/2-r.left,(a.y+b.y)/2-r.top);pinch.distance=distance;return;}
      if(drag?.id===e.pointerId){
        if(Math.hypot(e.clientX-drag.x,e.clientY-drag.y)>5)drag.moved=true;
        if(drag.moved){camera().x=drag.cx+e.clientX-drag.x;camera().y=drag.cy+e.clientY-drag.y;paintCamera();}
      }
    });
    const release=e=>{
      if(!pinches.has(e.pointerId))return;
      const tap=e.type==='pointerup'&&drag?.id===e.pointerId&&!drag.moved&&Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<=5;
      pinches.delete(e.pointerId);drag=null;pinch=null;save();
      if(tap){
        if(inside){if(inside.selected){inside.selected=null;inside.showSql=false;onChange();}}
        else if(view.selected)clearSelection();
      }
    };
    root.addEventListener('pointerup',release);root.addEventListener('pointercancel',release);root.addEventListener('lostpointercapture',release);
    root.addEventListener('keydown',e=>{
      if(view.mode==='stage')return;
      if(e.key==='Escape'&&inside?.opened){goBack();return;}
      if(e.key==='Escape'&&inspect){goBack();root.querySelector('[data-canvas-action="inspect"]')?.focus({preventScroll:true});return;}
      if(e.target!==lastViewport)return;
      if(['+','=','-','Home','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key))e.preventDefault();else return;
      if(e.key==='Home')fit();else if(['+','=','-'].includes(e.key))zoom(e.key==='-'?.8:1.25);else {camera().x+=e.key==='ArrowLeft'?80:e.key==='ArrowRight'?-80:0;camera().y+=e.key==='ArrowUp'?80:e.key==='ArrowDown'?-80:0;paintCamera();}save();
    });
    return {update,setFilter,markup,mounted,setMode,select,querySurface:id=>querySurfaces.get(id),get active(){return view.mode!=='stage';},get mode(){return view.mode;},takeDrafts(){const saved=pendingDrafts;pendingDrafts=null;return saved;}};
  }
  globalThis.CalliopeStageCanvas={buildGraph,projection,layout,create};
})();
