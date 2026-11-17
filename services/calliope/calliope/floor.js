(() => {
"use strict";

async function api(path,options={}){
  const response=await fetch(path,{credentials:'same-origin',headers:{accept:'application/json',...(options.headers||{})},...options});
  let data={};try{data=await response.json()}catch(_e){}
  if(response.status===401){window.location.assign('/login?next='+encodeURIComponent(window.location.pathname));throw new Error('Sign-in required')}
  if(!response.ok)throw new Error(data?.error?.message||data?.error?.code||('Request failed · '+response.status));
  return data;
}

const LANE_ORDER=['awaiting_you','in_motion','standing','done','noticed','archived'];
const TIMELINE_LANES=['awaiting_you','in_motion','standing'];
const LANE_LABEL={awaiting_you:'Awaiting You',in_motion:'In Motion',standing:'Standing',done:'Done',noticed:'Noticed',archived:'Archived'};
const FLOOR_INTAKE_STORAGE_KEY='calliope.floorIntake.v1';
const FLOOR_INTAKE_DB_NAME='calliope-floor-intake-v1';
const FLOOR_INTAKE_DB_STORE='handoffs';
const FLOOR_INTAKE_HEADLINE_ROTATION_MS=10_000;
const FLOOR_INTAKE_HEADLINE_FADE_MS=520;
const FLOOR_INTAKE_HEADLINES=Object.freeze([
  'What are we working on?',
  'Ideas become things here.',
  'Tell me what you’re trying to understand.',
  'You don’t need to know the data. You need to know what problem you can’t stop thinking about.',
  'Ask for the thing you wish existed.',
  'Start with the question. Keep what works.'
]);
const floorIntakeMotionPreference=window.matchMedia('(prefers-reduced-motion: reduce)');
let floorIntakeHeadlineIndex=0,floorIntakeHeadlineQueue=[],floorIntakeHeadlineTimer=null,floorIntakeHeadlineSwapTimer=null;
const state={cards:[],liveCards:[],laneRows:[],liveLaneRows:[],snapshots:[],liveTimelineFrame:null,people:new Map(),viewer:null,starters:[],hiddenStarterKeys:new Set(),startersExpanded:true,starterLaunching:false,agentRoster:[],agentRosterAt:0,agentRosterPromise:null,config:null,designProfiles:[],designProfilesPromise:null,intakeAgentKey:'calliope',intakeTeamMode:true,intakeDesignVersionId:null,intakeAttachments:[],intakePlaybooks:[],intakeEditor:null,intakeObjectCache:new Map(),intakeDiscovery:{query:'',items:[],searched:[],status:'idle',timer:null,controllers:[],pending:0,errors:0,sequence:0,cache:new Map(),promoting:false},ghosts:{index:null,indexAt:0,indexPromise:null,query:'',applied:'',matches:[],values:new Map(),timer:null,matchTimer:null,controller:null},view:'board',gridMode:false,hideNotices:true,viewBeforeGrid:'board',gridLoadStartedAt:0,gridLoadedIn:0,quietReload:false,expanded:new Set(),laneQueries:new Map(),searchOpen:new Set(),fitTimer:null,scrubTimer:null,scrubRaf:0,scrubPointer:null,scrubPreviewAt:null,timelineRenderCount:0,stagedAction:null,refreshing:false,timeMode:'live',selectedGeneration:null,selectedSnapshotAt:null,selectedExpectedTotal:null,frameToken:0,frameLoading:false};
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
// One portal for native hints, including cards mounted by the lane virtualizer
// and controls supplied by the shared theme. Keep titles out of the browser's
// tooltip system without requiring every renderer to know about this layer.
function initializeFloorTooltips(){
  const tip=document.createElement('div');
  tip.id='floorTooltip';tip.className='floor-tooltip';tip.setAttribute('role','tooltip');
  const content=document.createElement('span');content.className='floor-tooltip-content';tip.append(content);
  document.body.append(tip);
  let anchor=null,hovered=null,focused=null,showTimer=null,hideTimer=null,dismissed=null;
  const descriptions=new WeakMap(),names=new WeakMap();
  function adopt(el){
    if(!el.hasAttribute('title'))return;
    const value=el.getAttribute('title');
    el.removeAttribute('title');
    el.dataset.floorTooltip=value;
    // Preserve title's naming fallback for otherwise unnamed icon controls,
    // and its descriptive text without adding hundreds of new tab stops.
    if(!el.hasAttribute('aria-description')||el.getAttribute('aria-description')===descriptions.get(el)){
      el.setAttribute('aria-description',value);descriptions.set(el,value);
    }
    if(el.getAttribute('aria-label')===names.get(el)||
      (!el.hasAttribute('aria-label')&&!el.hasAttribute('aria-labelledby')&&
       el.matches('button,a,input,select,textarea,[role="button"]')&&
       !el.textContent.trim()&&!el.querySelector('img[alt]'))){
      el.setAttribute('aria-label',value);names.set(el,value);
    }
  }
  function scan(root){
    if(root.nodeType!==Node.ELEMENT_NODE)return;
    adopt(root);root.querySelectorAll('[title]').forEach(adopt);
  }
  function target(node){
    const el=node?.closest?.('[title],[data-floor-tooltip]');
    if(el)adopt(el);
    return el?.dataset.floorTooltip?.trim()?el:null;
  }
  function hide(){
    clearTimeout(showTimer);clearTimeout(hideTimer);
    if(anchor){
      const ids=(anchor.getAttribute('aria-describedby')||'').split(/\s+/).filter(id=>id&&id!==tip.id);
      if(ids.length)anchor.setAttribute('aria-describedby',ids.join(' '));
      else anchor.removeAttribute('aria-describedby');
    }
    anchor=null;tip.removeAttribute('data-open');
  }
  function position(){
    if(!anchor?.isConnected||!anchor.getClientRects().length){hide();return}
    const rect=anchor.getBoundingClientRect(),gap=12,pad=12;
    const width=document.documentElement.clientWidth,height=window.innerHeight;
    const box=tip.getBoundingClientRect();
    const above=rect.bottom+gap+box.height>height-pad&&rect.top>height-rect.bottom;
    const left=Math.max(pad,Math.min(width-pad-box.width,rect.left+rect.width/2-box.width/2));
    const top=Math.max(pad,Math.min(height-pad-box.height,above?rect.top-gap-box.height:rect.bottom+gap));
    tip.style.left=left+'px';tip.style.top=top+'px';
    tip.style.setProperty('--tooltip-arrow',Math.max(16,Math.min(box.width-16,rect.left+rect.width/2-left))+'px');
    tip.dataset.side=above?'above':'below';
  }
  function show(el,delay=180){
    clearTimeout(hideTimer);
    if(el===anchor||el===dismissed)return;
    hide();
    if(!el)return;
    showTimer=setTimeout(()=>{
      if(!el.isConnected||!el.dataset.floorTooltip?.trim())return;
      anchor=el;content.textContent=el.dataset.floorTooltip;
      const ids=new Set((el.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean));
      ids.add(tip.id);el.setAttribute('aria-describedby',[...ids].join(' '));
      position();if(anchor)tip.dataset.open='true';
    },delay);
  }
  // Focus keeps a tooltip open only when it is keyboard focus: a mouse click
  // leaves focus behind on the button, and without this check the tooltip
  // lingered after the pointer moved away ("Back to the Floor" on Me).
  const keyboardFocused=()=>focused&&focused.isConnected&&focused.matches(':focus-visible')?focused:null;
  function leave(){
    clearTimeout(showTimer);clearTimeout(hideTimer);
    hideTimer=setTimeout(()=>{
      if(tip.matches(':hover'))return;
      const next=hovered||keyboardFocused();
      if(next)show(next,0);else hide();
    },120);
  }
  document.addEventListener('pointerover',event=>{
    if(event.pointerType==='touch')return;
    if(tip.contains(event.target)){clearTimeout(hideTimer);return}
    const next=target(event.target);
    if(next===hovered)return;
    hovered=next;dismissed=null;show(next);
  });
  document.addEventListener('pointerout',event=>{
    if(event.pointerType==='touch')return;
    if(tip.contains(event.relatedTarget))return;
    const next=target(event.relatedTarget);
    if(next===hovered&&!tip.contains(event.target))return;
    hovered=next;if(next!==dismissed)dismissed=null;leave();
  });
  document.addEventListener('focusin',event=>{
    focused=target(event.target);dismissed=null;
    const next=keyboardFocused();if(next)show(next,0);
  });
  document.addEventListener('focusout',event=>{
    focused=target(event.relatedTarget);leave();
  });
  function dismiss(){dismissed=anchor||hovered||focused;hide()}
  document.addEventListener('keydown',event=>{if(event.key==='Escape')dismiss()},true);
  document.addEventListener('click',dismiss,true);
  document.addEventListener('dragstart',dismiss,true);
  document.addEventListener('scroll',event=>{if(!tip.contains(event.target))dismiss()},true);
  window.addEventListener('resize',()=>{if(anchor)position()});
  window.addEventListener('blur',dismiss);
  // Safety net for a missed pointerout (small targets, tiles moving under a
  // still cursor, anchors re-rendered mid-hover): while a tooltip is open,
  // pointer movement outside both the anchor and the tooltip closes it.
  let moveFrame=0;
  document.addEventListener('pointermove',event=>{
    if(!anchor||event.pointerType==='touch')return;
    const x=event.clientX,y=event.clientY;
    cancelAnimationFrame(moveFrame);
    moveFrame=requestAnimationFrame(()=>{
      if(!anchor)return;
      const within=el=>{const r=el.getBoundingClientRect();return x>=r.left-4&&x<=r.right+4&&y>=r.top-4&&y<=r.bottom+4};
      if(within(anchor)||(tip.dataset.open&&within(tip)))return;
      // A direct move to another hinted control is a handoff, not an exit.
      // Keep the new hover instead of cancelling pointerout's pending show.
      if(hovered&&hovered!==anchor&&hovered.isConnected&&within(hovered)){show(hovered);return}
      if(keyboardFocused()===anchor)return;
      hovered=null;hide();
    });
  },{passive:true});
  document.documentElement.addEventListener('mouseleave',()=>{hovered=null;leave()});
  scan(document.body);
  new MutationObserver(records=>{
    for(const record of records){
      if(record.type==='attributes')adopt(record.target);
      else for(const node of record.addedNodes)scan(node);
    }
    if(anchor){
      if(!anchor.isConnected||!anchor.dataset.floorTooltip?.trim())hide();
      else if(content.textContent!==anchor.dataset.floorTooltip){content.textContent=anchor.dataset.floorTooltip;position()}
    }
  }).observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['title']});
}
initializeFloorTooltips();
const icon={
  layers:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m12 3 8 4-8 4-8-4 8-4Z"/><path d="m4 12 8 4 8-4M4 17l8 4 8-4"/></svg>',
  signal:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z"/><path d="M10 21h4"/></svg>',
  eye:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg>',
  users:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="8" r="3"/><path d="M3 20c0-4 2-7 6-7s6 3 6 7M16 5a3 3 0 0 1 0 6M18 13c2 .7 3 3 3 6"/></svg>',
  search:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/></svg>',
  spark:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 2.8c.5 5.1 2.3 7 7.4 7.5-5.1.5-6.9 2.3-7.4 7.5-.5-5.2-2.3-7-7.4-7.5 5.1-.5 6.9-2.4 7.4-7.5Z"/><path d="M19 16.4c.2 2.1 1 2.9 3.1 3.1-2.1.2-2.9 1-3.1 3.1-.2-2.1-1-2.9-3.1-3.1 2.1-.2 2.9-1 3.1-3.1Z"/></svg>'
};
function jsonObject(value){if(value&&typeof value==='object')return value;try{return JSON.parse(String(value||'{}'))}catch(_e){return {}}}
const laneWindows=new Map(),laneHeights=new Map();
state.total=0;state.liveTotal=0;state.facets={};state.pages={};
let filterTimer=null;
function floorParams(){const f=currentFilters();return {thumbs:'1',...(state.gridMode?{limit:'100',...(state.hideNotices?{hide_notices:'1'}:{})}:{}),q:f.q,person:f.person,kind:f.kind,freshness:f.fresh,lane_queries:JSON.stringify(Object.fromEntries(state.laneQueries)),...(state.timeMode==='history'?{as_of:state.selectedSnapshotAt}:{})}}
function floorPageUrl(params){return '/api/calliope/floor/pieces?'+new URLSearchParams(params)}
const stream=new FloorStream((params,signal)=>api(floorPageUrl(params),{signal}),(data,event)=>{
  if(event.loading){if(event.lane)laneWindows.get(state.view==='list'?'list':event.lane)?.status(true,null);else if(!state.quietReload){setFloorLoading(true);$('status').textContent='Loading selected view · current cards remain visible…'}return}
  if(event.error){if(event.lane)laneWindows.get(state.view==='list'?'list':event.lane)?.status(false,event.error);else{state.pageLoading=false;state.quietReload=false;setFloorLoading(false,true);$('status').textContent='Showing previous cards · Floor could not load · '+event.error.message;showToast('The view could not be loaded. Use Refresh to retry.')}return}
  const before=event.reset?capturePieceRects():new Map();
  setFloorLoading(false);state.pageLoading=false;state.quietReload=false;state.cards=data.cards||[];state.laneRows=data.lanes||[];state.total=Number(data.total)||0;state.facets=data.facets||{};state.pages=data.pages||{};
  if(event.reset){hydrateFilters();render();animatePieces(before)}else if(state.view==='board'){
    const lane=event.lane;laneWindows.get(lane)?.update(state.cards.filter(c=>c.column_state===lane),laneMatched(lane));
  }else if(state.view==='grid')renderGrid(state.cards);
  else laneWindows.get('list')?.update(state.cards.filter(c=>!c.silhouette),state.laneRows.reduce((n,r)=>n+Number(r.matched??r.pieces),0));
  $('status').textContent=`${state.timeMode==='history'?'Past · '+localStamp(state.selectedSnapshotAt,true):'Live'} · ${state.total.toLocaleString()} pieces · ${Number(data.matched_total??data.total).toLocaleString()} matching`;
  window.floorInitialVeil?.finish();
});
function laneMatched(name){const row=state.laneRows.find(r=>r.column_state===name);return Number(row?.matched??row?.pieces)||0}
function clearLaneWindows(){for(const view of laneWindows.values())view.destroy();laneWindows.clear()}
function setFloorLoading(loading,stale=false){
  const workspace=$('workspace');workspace.classList.toggle('view-loading',loading);workspace.classList.toggle('view-stale',stale);
  workspace.setAttribute('aria-busy',String(loading));
  workspace.querySelectorAll('.piece,.starter-card,.floor-list-row[data-open],.grid-tile').forEach(card=>{card.inert=loading||stale});
}
function reloadFloorPieces(options={}){stopPresentationRefresh();clearTimeout(filterTimer);state.pageLoading=true;state.quietReload=Boolean(options.quiet);return stream.reset(floorParams()).then(()=>{if(state.timeMode==='live'&&!state.pageLoading)refreshPresentations()})}

function scheduleFloorSearch(){stream.cancel();clearTimeout(filterTimer);filterTimer=setTimeout(reloadFloorPieces,220)}
function laneRowsFromCards(cards){return LANE_ORDER.map(column_state=>{const rows=cards.filter(c=>c.column_state===column_state);return {column_state,pieces:rows.length,active_now:rows.filter(c=>boolish(c.active_now)).length,hidden_by_default:rows.filter(c=>boolish(c.default_hidden)).length}}).filter(r=>r.pieces)}
function liveCounts(){return Object.fromEntries(state.liveLaneRows.map(r=>[r.column_state,Number(r.pieces)||0]))}
function generationValue(snapshot){const raw=snapshot&&snapshot.generation;if(raw===null||raw===undefined||String(raw).trim()==='')return null;const n=Number(raw);return Number.isFinite(n)?n:null}
function timelinePoints(){
  const captures=state.snapshots.filter(s=>Number.isFinite(Date.parse(s.snapshot_at))).slice().sort((a,b)=>Date.parse(a.snapshot_at)-Date.parse(b.snapshot_at));
  // Old/unavailable APIs carry the last known capture forward, never invent
  // a zero or splice viewer-filtered board counts into whole-system history.
  const current=state.liveTimelineFrame||captures.at(-1);
  if(!current)return captures;
  const live={...current,live:true,estimated:!state.liveTimelineFrame,snapshot_at:new Date().toISOString()};
  return [...captures,live];
}

function timeBounds(points=timelinePoints()){const min=Date.parse(points[0]?.snapshot_at),max=Date.parse(points[points.length-1]?.snapshot_at);return {min:Number.isFinite(min)?min:Date.now(),max:Number.isFinite(max)?max:Date.now()}}
function snapshotAtOrBefore(ms){let found=null;for(const s of state.snapshots){if(Date.parse(s.snapshot_at)<=ms)found=s;else break}return found||state.snapshots[0]||null}
function interpolatedFrame(points,ms){
  const {min,max}=timeBounds(points),at=Math.max(min,Math.min(max,Number(ms)||max));if(points.length===1)return {...points[0],snapshot_at:new Date(at).toISOString(),live:true};
  let hi=points.findIndex(p=>Date.parse(p.snapshot_at)>=at);if(hi<0)hi=points.length-1;if(hi===0)return {...points[0],snapshot_at:new Date(at).toISOString(),live:false,preview:true};
  const a=points[hi-1],b=points[hi],ta=Date.parse(a.snapshot_at),tb=Date.parse(b.snapshot_at),mix=Math.max(0,Math.min(1,(at-ta)/Math.max(1,tb-ta))),ac=jsonObject(a.counts_by_column_state),bc=jsonObject(b.counts_by_column_state),counts={};
  LANE_ORDER.forEach(l=>counts[l]=Math.round((Number(ac[l])||0)+((Number(bc[l])||0)-(Number(ac[l])||0))*mix));
  return {snapshot_at:new Date(at).toISOString(),total_items:Math.round((Number(a.total_items)||0)+((Number(b.total_items)||0)-(Number(a.total_items)||0))*mix),counts_by_column_state:counts,generation:generationValue(a),live:at>=max-500,estimated:Boolean(b.estimated)&&at>=max-500,preview:true};
}
function localStamp(value,detail=false){const d=new Date(value);if(!Number.isFinite(d.getTime()))return '—';return d.toLocaleString([],{weekday:detail?'short':undefined,month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}
function capturePieceRects(){return new Map([...document.querySelectorAll('.piece')].map(el=>[el.dataset.id,el.getBoundingClientRect()]))}
function animatePieces(before){if(!before.size||matchMedia('(prefers-reduced-motion:reduce)').matches)return;requestAnimationFrame(()=>requestAnimationFrame(()=>document.querySelectorAll('.piece').forEach(el=>{const old=before.get(el.dataset.id),now=el.getBoundingClientRect();if(!old){el.animate([{opacity:0,transform:'scale(.96)'},{opacity:1,transform:'scale(1)'}],{duration:260,easing:'ease-out'});return}const dx=old.left-now.left,dy=old.top-now.top;if(Math.abs(dx)>2||Math.abs(dy)>2)el.animate([{transform:`translate(${dx}px,${dy}px)`,opacity:.72},{transform:'translate(0,0)',opacity:1}],{duration:460,easing:'cubic-bezier(.2,.75,.18,1)'})})))}
function laneCount(point,lane){return Number(jsonObject(point?.counts_by_column_state)[lane])||0}
function timelineCountGeometry(points,lane,xFor,yFor){const coords=points.map(point=>[xFor(point.snapshot_at),yFor(laneCount(point,lane))]);return `M ${coords.map(([x,y])=>`${x} ${y}`).join(' L ')}`}
function renderTimeline(){
  overview.updateHistory(Boolean(state.snapshots.length));
  if(!state.snapshots.length){document.body.classList.remove('history-mode');return}
  if(overview.mode!=='history')return;
  const points=timelinePoints(),{min,max}=timeBounds(points),committed=state.timeMode==='history'?Date.parse(state.selectedSnapshotAt):max,target=Math.max(min,Math.min(max,state.scrubPreviewAt??committed)),selected=interpolatedFrame(points,target),previewing=state.scrubPreviewAt!==null,isLive=target>=max-500,range=$('timeRange'),viz=$('timeViz'),svg=$('timeSvg'),box=svg.getBoundingClientRect(),width=Math.max(320,Math.round(box.width)),height=Math.max(82,Math.round(box.height)),left=72,right=width-8,plotTop=4,plotBottom=64,markerY=82,ratio=(target-min)/Math.max(1,max-min),xFor=v=>left+(right-left)*((Date.parse(v)-min)/Math.max(1,max-min)),selectedX=xFor(selected.snapshot_at);
  document.body.classList.toggle('history-mode',!isLive);svg.setAttribute('viewBox',`0 0 ${width} ${height}`);range.max='1000';range.value=String(Math.round(ratio*1000));range.disabled=false;viz.setAttribute('aria-valuenow',range.value);viz.setAttribute('aria-valuetext',`${isLive?'Live':previewing?'Preview':'Past'} · ${localStamp(selected.snapshot_at,true)}`);viz.dataset.geometry=`${width}x${height}`;viz.dataset.renderSeq=String(++state.timelineRenderCount);
  const laneOrder=TIMELINE_LANES,colors={standing:'var(--floor-stand)',in_motion:'var(--floor-move)',awaiting_you:'var(--floor-wait)'},shortLabels={awaiting_you:'WAIT',in_motion:'MOVE',standing:'STAND'},selectedCounts=jsonObject(selected.counts_by_column_state),allCounts=points.flatMap(point=>laneOrder.map(lane=>laneCount(point,lane))),rawMin=Math.min(...allCounts),rawMax=Math.max(...allCounts),axisPadding=Math.max(1,(rawMax-rawMin||Math.max(1,Math.abs(rawMax)))*.12),axisMin=rawMin-axisPadding,axisMax=rawMax+axisPadding,yForCount=value=>plotBottom-((Number(value)-axisMin)/Math.max(1,axisMax-axisMin))*(plotBottom-plotTop);
  viz.dataset.axisMin=String(axisMin);viz.dataset.axisMax=String(axisMax);
  const legend=laneOrder.map((lane,index)=>{const y=10+index*13,count=Math.round(Number(selectedCounts[lane])||0);return `<line class="delta-key" data-lane="${lane}" x1="1" x2="6" y1="${y}" y2="${y}" stroke="${colors[lane]}"></line><text class="delta-label" data-lane="${lane}" x="9" y="${y+2.25}">${shortLabels[lane]} ${count.toLocaleString()}</text>`}).join('');
  const gridLines=[plotTop,(plotTop+plotBottom)/2,plotBottom].map(y=>`<line class="delta-baseline" x1="${left}" x2="${right}" y1="${y}" y2="${y}"></line>`).join(''),seriesLines=laneOrder.map(lane=>`<path class="delta-line" data-lane="${lane}" d="${timelineCountGeometry(points,lane,xFor,yForCount)}" stroke="${colors[lane]}"></path>`).join('');
  // Dots only on the rail — the old per-capture vertical "ghost" lines read
  // as a dense grid within days of captures accumulating.
  const ticks=points.map(p=>{const x=xFor(p.snapshot_at),generation=generationValue(p)!==null;return `<circle class="time-dot ${generation?'travel':''}" cx="${x}" cy="${markerY}" r="${generation?3.3:2.4}"><title>${esc(localStamp(p.snapshot_at,true))} · ${Number(p.total_items||0).toLocaleString()} pieces · click or drag here</title></circle>`}).join('');
  const selectedTotal=Math.max(1,laneOrder.reduce((sum,lane)=>sum+(Number(selectedCounts[lane])||0),0)),lensX=Math.max(left,Math.min(right-20,selectedX-9)),railDotRadius={awaiting_you:3.2,in_motion:2.65,standing:2.2},selectedDots=laneOrder.map(lane=>`<circle class="delta-dot" data-lane="${lane}" cx="${selectedX}" cy="${yForCount(Number(selectedCounts[lane])||0)}" r="${railDotRadius[lane]}" fill="${colors[lane]}"></circle>`).join('');
  const futureWidth=Math.max(0,right-selectedX),future=isLive?'':`<defs><pattern id="futureHatch" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="9" stroke="var(--warn)" stroke-width="2"></line></pattern></defs><rect class="time-future" x="${selectedX}" y="${plotTop}" width="${futureWidth}" height="${markerY-plotTop}"></rect><rect class="time-future-hatch" x="${selectedX}" y="${plotTop}" width="${futureWidth}" height="${markerY-plotTop}"></rect>${futureWidth>110?`<text class="time-future-label" x="${selectedX+futureWidth/2}" y="${(plotTop+markerY)/2+2}" text-anchor="middle">AFTER THIS FRAME</text>`:''}`,label=isLive?'':localStamp(selected.snapshot_at,true),labelX=selectedX>right-180?selectedX-36:selectedX+36,anchor=selectedX>right-180?'end':'start',selectedLabel=label?`<text class="time-selected-label" x="${labelX}" y="${height-1}" text-anchor="${anchor}">${esc(label)}</text>`:'';
  svg.innerHTML=`${gridLines}${legend}${seriesLines}${ticks}${future}<line class="time-guide" x1="${selectedX}" x2="${selectedX}" y1="${plotTop}" y2="${markerY}"></line><rect class="time-lens-frame" x="${lensX}" y="${plotTop}" width="20" height="${markerY-plotTop+2}" rx="4"></rect>${selectedDots}<circle class="time-handle" cx="${selectedX}" cy="${markerY}" r="5"></circle>${selectedLabel}`;
  const mixOrder=TIMELINE_LANES,mixLabels={awaiting_you:'Awaiting',in_motion:'Moving',standing:'Standing'},signature=[Math.round(ratio*1000),...mixOrder.map(l=>Number(selectedCounts[l])||0)].join('|'),mix=$('timeMix');
  if(mix.children.length!==mixOrder.length)mix.innerHTML=mixOrder.map(l=>`<span data-lane="${l}"></span>`).join('');[...mix.children].forEach((el,i)=>{const l=mixOrder[i],count=Number(selectedCounts[l])||0;el.style.flexBasis=`${count/selectedTotal*100}%`;el.style.background=colors[l];el.style.opacity=count?1:.18;el.title=`${mixLabels[l]} · ${count.toLocaleString()}`});
  mix.dataset.signature=signature;mix.setAttribute('aria-label',mixOrder.map(l=>`${mixLabels[l]} ${Number(selectedCounts[l])||0}`).join(', '));$('timeComposition').textContent=`${Number(selectedCounts.awaiting_you||0).toLocaleString()} wait · ${Number(selectedCounts.in_motion||0).toLocaleString()} move · ${Number(selectedCounts.standing||0).toLocaleString()} stand`;
  $('timeCaption').innerHTML=`${state.snapshots.length} captures · system-wide active work on one scale<span class="frame-key">observed ${rawMin.toLocaleString()}–${rawMax.toLocaleString()} · 12% breathing room · dots capture</span>`;
  $('timeKicker').textContent=isLive?'At live edge':previewing?'Dragging through history':'Viewing history';$('frameBadge').textContent=isLive?'Live frame':previewing?'Preview frame':'Past frame';$('timeStart').textContent=localStamp(points[0].snapshot_at);$('timeEnd').textContent='Live';$('timeWhen').textContent=isLive?'Live · now':localStamp(selected.snapshot_at,true);
  const total=Number(selected.total_items)||0;$('timeMeta').textContent=isLive?`${total.toLocaleString()} across system · ${state.liveTotal.toLocaleString()} visible${selected.estimated?' · latest capture':''}`:previewing?`${total.toLocaleString()} across system · previewing composition`:`${total.toLocaleString()} across system · ${state.total.toLocaleString()} visible AS-OF`;
  $('liveBtn').classList.toggle('on',isLive);$('liveBtn').disabled=isLive;$('liveBtn').textContent=isLive?'At live edge':'↺ Return live';$('liveBtn').setAttribute('aria-label',isLive?'Currently at live edge':'Return to the live floor');
}
function timeAtClientX(clientX){const box=$('timeViz').getBoundingClientRect(),points=timelinePoints(),{min,max}=timeBounds(points),left=72,right=Math.max(left+1,box.width-8),ratio=Math.max(0,Math.min(1,(clientX-box.left-left)/(right-left)));return min+ratio*(max-min)}
function queueTime(ms,delay=180){clearTimeout(state.scrubTimer);state.scrubTimer=setTimeout(()=>selectTime(ms),delay)}
function previewScrubAt(ms,delay=180){stream.cancel();const {min,max}=timeBounds(),target=Math.max(min,Math.min(max,ms));state.scrubPreviewAt=target;if(state.frameLoading){state.frameToken++;state.frameLoading=false}cancelAnimationFrame(state.scrubRaf);state.scrubRaf=requestAnimationFrame(()=>{renderTimeline();if(target<max-500){$('liveDot').className='live-dot past';$('status').textContent=`Preview · ${localStamp(target,true)} · cards follow after pause`}});queueTime(target,delay)}
async function selectTime(ms){
  const {min,max}=timeBounds(),target=Math.max(min,Math.min(max,Number(ms)||max));if(target>=max-500)return showLive();
  const checkpoint=snapshotAtOrBefore(target);state.frameToken++;state.frameLoading=false;state.scrubPreviewAt=null;state.timeMode='history';state.selectedGeneration=generationValue(checkpoint);state.selectedSnapshotAt=new Date(target).toISOString();
  $('liveDot').className='live-dot past';renderTimeline();await reloadFloorPieces();
}
async function showLive(){clearTimeout(state.scrubTimer);state.frameToken++;state.frameLoading=false;state.scrubPreviewAt=null;state.timeMode='live';state.selectedGeneration=null;state.selectedSnapshotAt=null;state.selectedExpectedTotal=null;$('liveDot').className='live-dot ok';renderTimeline();await reloadFloorPieces()}
async function load(quiet=false){
  if(state.refreshing)return;
  state.refreshing=true;
  if(!quiet){$('reload').setAttribute('aria-busy','true');$('status').textContent='Connecting to live data…';$('liveDot').className='live-dot'}
  try{
    const t0=performance.now();
    const refreshRoster=!state.agentRoster.length||Date.now()-state.agentRosterAt>=300000;
    const rosterRequest=refreshRoster?loadAgentRoster():null;
    const navigation=stream.revision;
    const data=await api('/api/calliope/floor');
    const {lanes:laneRows=[],cards=[],people=[],titles=[],snapshots=[],timeline_live=null,viewer=null,starters=[],hidden_starter_keys=[],daily_brief=null}=data;
    state.dailyBrief=daily_brief;state.liveTotal=Number(data.total)||0;
    const presentationMap=new Map((titles||[]).map(t=>[String(t.item_id),{title:String(t.semantic_title||'').trim(),status:String(t.semantic_status||'').trim()}]));
    state.hiddenStarterKeys=new Set((hidden_starter_keys||[]).map(String));
    state.liveLaneRows=laneRows;state.liveTimelineFrame=timeline_live;state.liveCards=cards.map(c=>{const p=presentationMap.get(String(c.item_id))||{};return {...c,semantic_title:p.title||null,semantic_status:p.status||null}});state.viewer=viewer;overview.refresh(viewer,!quiet);state.starters=buildFloorStarters(state.liveCards,starters);state.snapshots=(snapshots||[]).slice().sort((a,b)=>Date.parse(a.snapshot_at)-Date.parse(b.snapshot_at));
    if(state.timeMode==='live'&&navigation===stream.revision){
      hydratePeople(people);
      const params=floorParams(),filtered=params.q||params.person!=='all'||params.kind!=='all'||params.freshness!=='all'||state.laneQueries.size;
      // The grid pins the people filter to the viewer, so every live tick
      // counts as "filtered"; reload it quietly — no veil, tiles reconcile in place.
      if(filtered)await reloadFloorPieces({quiet:state.gridMode});else await stream.reset(params,{...data,cards:state.liveCards});
    }else renderTimeline();
    if(state.timeMode==='live')refreshPresentations();
    if(rosterRequest)rosterRequest.then(agentRoster=>{if(state.timeMode!=='live')return;hydratePeople(people,agentRoster);hydrateFilters();render()});
    $('liveDot').className='live-dot ok';
    $('status').textContent=state.timeMode==='live'?`Live · ${state.liveTotal.toLocaleString()} pieces · ${Math.round(performance.now()-t0)} ms`:`Past · ${localStamp(state.selectedSnapshotAt,true)} · live index refreshed`;
  }catch(err){
    console.error(err);$('status').textContent='Live refresh failed: '+(err.message||err);if(!state.cards.length)$('workspace').innerHTML='<div class="empty">The Floor could not reach its live work graph.</div>';
  }finally{state.refreshing=false;if(!quiet)$('reload').removeAttribute('aria-busy');window.floorInitialVeil?.finish()}
}
let presentationTimer=null,presentationEpoch=0;
function stopPresentationRefresh(){presentationEpoch++;clearTimeout(presentationTimer)}
function refreshPresentations(){
  stopPresentationRefresh();
  const epoch=presentationEpoch,revision=stream.revision,deadline=Date.now()+900000;
  async function poll(){
    if(epoch!==presentationEpoch||revision!==stream.revision||state.timeMode!=='live'||document.visibilityState!=='visible')return;
    try{
      const data=await api('/api/calliope/floor/presentations');
      if(epoch!==presentationEpoch||revision!==stream.revision||state.timeMode!=='live')return;
      if(stream.applyPresentations(data.titles||[],revision)){
        // Update existing windows: preserve search, lane expansion, scroll and focus.
        state.cards=stream.data.cards;
        for(const [lane,window] of laneWindows){
          window.update(lane==='list'?state.cards.filter(c=>!c.silhouette):state.cards.filter(c=>c.column_state===lane),
            lane==='list'?state.laneRows.reduce((n,r)=>n+Number(r.matched??r.pieces),0):laneMatched(lane));
        }
      }
      if(data.pending&&Date.now()<deadline)presentationTimer=setTimeout(poll,2000);
    }catch(error){console.warn('Floor labels will retry on the next refresh',error)}
  }
  poll();
}
// Keep a reader's loaded history in place; resume quiet refresh at the top.
function safeRefresh(){if(document.visibilityState==='visible'&&state.timeMode==='live'&&![...laneWindows.values()].some(view=>view.body.scrollTop>0)&&!document.querySelector('input:focus,select:focus,.dragging'))return load(true)}
function normalizeAgents(value){
  if(Array.isArray(value))return value.filter(Boolean);
  if(!value)return[];
  const s=String(value).trim();
  try{const j=JSON.parse(s);if(Array.isArray(j))return j.filter(Boolean)}catch(_e){}
  return s.replace(/^\{|\}$/g,'').split(',').map(x=>x.replace(/^"|"$/g,'').trim()).filter(Boolean);
}
function displayAgent(key){const known=state.people.get(String(key||''));if(known&&known.display_name)return known.display_name;if(String(key||'').includes('@'))return String(key);return String(key||'').replace(/[-_]+/g,' ').replace(/\b\w/g,c=>c.toUpperCase()).replace(/^Dashboard Studio$/,'Iris').replace(/^Analytical Skeptic$/,'Cassandra')}
function avatarColor(key){const colors=['#087c78','#415c83','#8f4e58','#72568f','#4f7650','#986a2c','#4b6970'];let n=0;for(const c of String(key))n=(n*31+c.charCodeAt(0))>>>0;return colors[n%colors.length]}
function initials(key){const d=displayAgent(key).split(/\s+/).filter(Boolean);return (d.length>1?d[0][0]+d[d.length-1][0]:d[0]?.slice(0,2)||'?').toUpperCase()}
function boolish(v){return v===true||v==='true'||v===1||v==='1'}
function safeImageUrl(value){const s=String(value||'').trim();return /^(?:https:\/\/|\/)/i.test(s)?s:null}
function safeOpenUrl(value){const s=String(value||'').trim();if(!s)return null;try{const url=new URL(s,window.location.origin);return /^(?:http:|https:)$/.test(url.protocol)?url.href:null}catch(_error){return null}}
async function loadAgentRoster(){
  if(state.agentRoster.length&&Date.now()-state.agentRosterAt<300000)return state.agentRoster;
  if(state.agentRosterPromise)return state.agentRosterPromise;
  state.agentRosterPromise=api('/api/calliope/config').then(data=>{
    state.config=data;state.agentRoster=Array.isArray(data.chat_agents)?data.chat_agents:[];state.agentRosterAt=Date.now();renderFloorIntakeAgent();return state.agentRoster;
  }).catch(err=>{console.warn('Agent avatars unavailable',err);return state.agentRoster}).finally(()=>{state.agentRosterPromise=null});
  return state.agentRosterPromise;
}
function hydratePeople(rows,agents=state.agentRoster){
  state.people=new Map((rows||[]).map(p=>[String(p.specialist_key),{...p,has_portrait:boolish(p.has_portrait),avatar_url:safeImageUrl(p.avatar_url)}]));
  for(const agent of agents||[]){const key=String(agent.key||'');if(!key)continue;const old=state.people.get(key)||{specialist_key:key,is_human:false};state.people.set(key,{...old,...agent,display_name:agent.name||old.display_name,avatar_url:safeImageUrl(agent.avatar_url),avatar_day_url:safeImageUrl(agent.avatar_day_url),avatar_night_url:safeImageUrl(agent.avatar_night_url)})}
}
function humanBackground(key){const a=avatarColor(key),b=avatarColor(String(key).split('').reverse().join(''));return `conic-gradient(from 210deg,${a},${b},${a})`}
function participantAvatar(key,isHuman=false){const meta=state.people.get(String(key)),label=isHuman?(meta&&meta.display_name||String(key)):displayAgent(key),hour=new Date().getHours(),calliopeImage=hour>=7&&hour<19?meta?.avatar_day_url:meta?.avatar_night_url,image=meta&&(calliopeImage||meta.avatar_url||safeImageUrl(meta.avatar_source_url)),fallback=isHuman?String(label).slice(0,1).toUpperCase():initials(key);return `<span class="avatar ${isHuman?'human':''}" style="background:${isHuman?humanBackground(key):avatarColor(key)}" title="${esc(label)}"><span class="avatar-fallback">${esc(fallback)}</span>${image?`<img data-floor-avatar="${esc(String(key))}" src="${esc(image)}" alt="${esc(label)}" loading="lazy" decoding="async">`:''}</span>`}
function avatars(card){
  // A room shows its people before its agents, and who has Callie working.
  const agents=normalizeAgents(card.agents),owner=String(card.owner_email||'').trim();
  const members=(Array.isArray(card.room_members)?card.room_members:[]).map(m=>String(m||'').trim()).filter(m=>m&&m!==owner&&!agents.includes(m));
  const humans=[...(owner?[owner]:[]),...members],driver=String(card.driver_email||'').trim();
  const participants=[...humans,...agents].slice(0,6),label=participants.map(displayAgent).join(', ');
  const shownHumans=humans.slice(0,4);
  const faces=shownHumans.map((k,i)=>participantAvatar(k,i===0&&k===owner)).join('')+agents.slice(0,Math.max(0,5-shownHumans.length)).map(k=>participantAvatar(k)).join('');
  // A room's faces already say who is in it; its line says who Callie is working for.
  const who=members.length?(driver?`${displayAgent(driver)} driving`:'')
    :(driver&&driver!==owner?`${displayAgent(driver)} driving`:(owner?displayAgent(owner):''));
  return `<div class="participant-wrap"><div class="avatars" aria-label="${esc(label)}">${faces}</div>${members.length?'<span class="room-mark" title="A room: several people work here">Room</span>':''}${who?`<span class="owner-email" title="${esc(driver||owner)}">${esc(who)}</span>`:''}</div>`;
}
function displayedAgentKeys(){const keys=new Set();document.querySelectorAll('.piece:not(.fit-overflow)').forEach(el=>{const c=state.cards.find(x=>String(x.item_id)===String(el.dataset.id));normalizeAgents(c&&c.agents).forEach(k=>keys.add(String(k)))});return [...keys]}
async function ensureVisibleAvatars(){return undefined}
function age(seconds){const s=Number(seconds);if(!Number.isFinite(s))return '—';if(s<60)return 'now';if(s<3600)return Math.floor(s/60)+'m';if(s<86400)return Math.floor(s/3600)+'h';if(s<604800)return Math.floor(s/86400)+'d';if(s<2592000)return Math.floor(s/604800)+'w';return Math.floor(s/2592000)+'mo'}
function kindLabel(k){return ({work_item:'notification',curated_dream:'Dream',duty_cycle:'cycle',workflow_run:'workflow',playbook:'playbook · SOP',overlay:'overlay',decision:'decision',input_request:'question',dream:'notice',chat:'chat',page:'Page'})[k]||String(k||'piece').replace(/_/g,' ')}
function compactDoneTitle(title){
  const quoted=title.match(/[“\"]([^\"”]{3,64})[”\"]/);
  if(quoted)return quoted[1].replace(/,\s*$/,'');
  return title
    .replace(/^Calliope\s+(?:has\s+)?(?:created|developed|built|published|produced|generated|proposed|proposes|completed|delivered)\s+/i,'')
    .replace(/^The user requested and received\s+/i,'')
    .replace(/^(?:an?|the)\s+/i,'')
    .replace(/^high-quality,?\s*/i,'')
    .replace(/^enterprise-style\s+/i,'')
    .replace(/[.!]\s*$/,'');
}
function presentationTitle(card){
  if(card.item_kind==='page')return String(card.display_title||'Living Page');
  let t=String(card.semantic_title||card.display_title||'Untitled piece');
  if(card.item_kind==='playbook')t=t.replace(/^playbook\./i,'').replace(/~[0-9a-f-]+$/i,'').replace(/[-_]+/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
  if(!card.semantic_title&&card.column_state==='awaiting_you'&&card.signal_kind==='blocked')t=t.replace(/^Nightly duty cycle failing:\s*Calliope\s*/i,'').replace(/^(.)/,c=>c.toUpperCase())+' is failing';
  if(!card.semantic_title&&card.column_state==='done')t=compactDoneTitle(t);
  return t;
}
function nextMoment(value){const at=Date.parse(value);if(!Number.isFinite(at))return '';const seconds=Math.floor((at-Date.now())/1000);if(seconds<=0)return 'due now';if(seconds<3600)return `next in ${Math.max(1,Math.floor(seconds/60))}m`;if(seconds<86400)return `next in ${Math.floor(seconds/3600)}h`;return `next in ${Math.floor(seconds/86400)}d`}
function playbookApproved(card){return card.item_kind==='playbook'&&/^approved/i.test(String(card.detail_state||''))}
function actionVerb(card){if(card.item_kind==='curated_dream')return 'Explore';if(['decision','input_request'].includes(card.item_kind))return 'Answer';if(playbookApproved(card))return 'Run';if(card.item_kind==='playbook')return 'Review';if(card.item_kind==='overlay')return 'Approve';if(card.signal_kind==='blocked')return 'Investigate';return 'Review'}
function canLaunchPiece(card){if(playbookApproved(card))return true;return card.item_kind!=='duty_cycle'&&(Boolean(card.action_prompt)||card.column_state==='awaiting_you'||card.column_state==='noticed')}
function primaryMarkup(card){if(state.timeMode==='history')return '';if(card.column_state!=='awaiting_you'||card.silhouette)return '';const asideBtn=`<button type="button" class="piece-action ghost" data-piece-aside="${esc(card.item_id)}" title="Set aside — not done, stays findable in Archived" aria-label="Set aside">✕</button>`;if(!card.action_prompt)return asideBtn;const asyncBtn=card.item_kind==='work_item'&&!card.engaged_session_id?`<button type="button" class="piece-action ghost" data-piece-async="${esc(card.item_id)}" title="Have Callie start without me" aria-label="Have Callie start without me">⇢</button>`:'';return `<button type="button" class="piece-action" data-piece-action="${esc(card.item_id)}" title="${card.item_kind==='input_request'?'Answer this question here':playbookApproved(card)?'Asks for optional direction, then opens a chat and runs':'Opens a chat with Callie and starts now'}">${esc(actionVerb(card))}</button>${asyncBtn}${asideBtn}`}
function healthMarkup(card){if(card.item_kind!=='duty_cycle')return '';const streak=Number(card.streak)||0,pinned=boolish(card.pinned),warn=streak>0||Boolean(String(card.last_status||'').match(/fail|error|unknown/i));return `<span class="health-state ${warn?'warn':''}" title="${esc(`${pinned?'Pinned':'Unpinned'} · failure streak ${streak}`)}"><i></i>${pinned?'pinned ✓ · ':''}streak ${streak}</span>`}
function cronParts(value){
  const s=String(value||'');
  const m=s.match(/['\"]expr['\"]\s*:\s*['\"]([^'\"]+)['\"]/i);
  return m?m[1].trim().split(/\s+/):[];
}
function clockTime(hour,minute){const h=Number(hour),m=Number(minute),suffix=h>=12?'PM':'AM',display=h%12||12;return `${display}:${String(m).padStart(2,'0')} ${suffix}`}
function localRunInstant(value){if(value===null||value===undefined||String(value).trim()==='')return null;const d=new Date(value);return Number.isFinite(d.getTime())?d:null}
function localClock(value,fallbackHour,fallbackMinute){const d=localRunInstant(value);return d?d.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):clockTime(fallbackHour,fallbackMinute)}
function localWeekday(value,fallback){const d=localRunInstant(value);return d?d.toLocaleDateString([],{weekday:'long'}):fallback}
function localWeekdayRange(value){const d=localRunInstant(value),days=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];if(!d)return 'weekdays';let shift=(d.getDay()-d.getUTCDay()+7)%7;if(shift>3)shift-=7;return shift===0?'weekdays':`${days[(1+shift+7)%7]}–${days[(5+shift+7)%7]}`}
function cadenceKind(value){const p=cronParts(value);if(p.length!==5)return 'Scheduled';if(/^\d+$/.test(p[0])&&/^\d+$/.test(p[1])){if(p[2]==='*'&&p[3]==='*'&&p[4]==='*')return 'Daily';if(p[2]==='*'&&p[3]==='*'&&p[4]==='1-5')return 'Weekdays';if(p[2]==='*'&&p[3]==='*'&&/^\d$/.test(p[4]))return 'Weekly';if(/^\d+$/.test(p[2])&&p[3]==='*'&&p[4]==='*')return 'Monthly'}return 'Custom'}
function cadenceLabel(value,nextRunAt){
  const p=cronParts(value);if(p.length!==5)return 'Scheduled cadence';
  const [minute,hour,day,month,dow]=p;if(!/^\d+$/.test(minute)||!/^\d+$/.test(hour))return 'Custom recurring schedule';
  const instant=localRunInstant(nextRunAt),at=instant?localClock(nextRunAt,hour,minute):`${clockTime(hour,minute)} UTC`,days=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  if(day==='*'&&month==='*'&&dow==='*')return `Runs every day at ${at}`;
  if(day==='*'&&month==='*'&&dow==='1-5')return `Runs ${localWeekdayRange(nextRunAt)} at ${at}`;
  if(day==='*'&&month==='*'&&/^\d$/.test(dow))return `Runs every ${localWeekday(nextRunAt,days[Number(dow)]||'week')} at ${at}`;
  if(/^\d+$/.test(day)&&month==='*'&&dow==='*')return `Runs monthly on day ${instant?instant.getDate():Number(day)} at ${at}`;
  return 'Custom recurring schedule';
}
function dreamPercent(value){
  if(value===null||value===undefined||value==='')return null;
  const number=Number(value);return Number.isFinite(number)&&number>=0&&number<=1?`${Math.round(number*100)}%`:null;
}
function dreamStage(dream){return dream?.portfolio_state==='backlog'?'On deck':dream?.status==='exploring'?'Exploring':'Awake'}
function dreamTileMeta(card){
  if(card.item_kind!=='curated_dream'||!card.dream)return '';
  const d=card.dream,confidence=dreamPercent(d.confidence),impact=['low','medium','high'].includes(d.impact)?d.impact:null;
  const labels=[impact?`<span data-impact="${impact}" title="Estimated impact">${esc(impact[0].toUpperCase()+impact.slice(1))} impact</span>`:'',confidence?`<span title="Calliope’s confidence estimate">${confidence} confidence</span>`:''].filter(Boolean);
  return labels.length?`<span class="dream-tile-meta">${labels.join('')}</span>`:'';
}
function dreamDetailMeta(dream){
  const rows=[['Status',dreamStage(dream)],['Impact',dream.impact],['Confidence (estimated)',dreamPercent(dream.confidence)],['Effort',dream.effort],['Novelty',dreamPercent(dream.novelty)],['Editorial score',dreamPercent(dream.rank_score)],['Portfolio rank',dream.portfolio_state==='promoted'&&Number(dream.portfolio_rank)>0?`#${Number(dream.portfolio_rank)}`:null]];
  return `<dl class="dream-detail-meta">${rows.filter(([,value])=>value!==null&&value!==undefined&&value!=='').map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;
}
function chipMarkup(card){
  const out=[];
  if(card.dream_origin)out.push('<span class="chip" title="The original idea and evidence are saved on this thread’s Stage">From a Dream</span>');
  if(card.item_kind==='curated_dream')out.push(`<span class="chip" title="Dream · ${esc(dreamStage(card.dream))}">${!card.session_id&&card.dream?.portfolio_state==='backlog'?'On deck':'Dream'}</span>`);
  if(card.item_kind==='page')out.push('<span class="chip page-chip">Page</span>');
  if(card.active_now)out.push(`<span class="chip live">${state.timeMode==='history'?'Active then':'Live'}</span>`);
  if(card.column_state==='awaiting_you'&&card.signal_kind==='blocked')out.push('<span class="chip blocked">Blocked</span>');
  if(card.column_state==='awaiting_you'&&String(card.urgency).match(/high|critical/i))out.push('<span class="chip high">'+esc(card.urgency)+'</span>');
  if(card.item_kind==='duty_cycle')out.push('<span class="chip schedule">'+esc(cadenceKind(card.schedule))+'</span>');
  if(!out.length&&card.item_kind!=='chat')out.push('<span class="chip">'+esc(kindLabel(card.item_kind))+'</span>');
  return out.length?`<div class="chips">${out.slice(0,2).join('')}</div>`:'';
}
function contextLine(card){
  if(card.item_kind==='page')return card.summary||'Refresh when you need a current answer';
  if(state.timeMode==='history'&&card.state_reason)return card.state_reason;
  if(card.item_kind==='duty_cycle'&&card.column_state==='in_motion'){const phase=card.last_status==='claimed'?'Run claimed':'Run in progress',elapsed=age(card.idle_seconds);return `${phase} · ${elapsed} elapsed`}
  if(card.column_state==='standing'){const cadence=card.schedule?cadenceLabel(card.schedule,card.next_run_at):(card.state_reason||''),next=nextMoment(card.next_run_at);return [cadence,next].filter(Boolean).join(' · ')}
  if(card.semantic_status)return card.semantic_status;
  if(card.column_state==='awaiting_you')return card.action_prompt||card.summary||card.state_reason||'Waiting for your review';
  if(card.column_state==='in_motion'){const facts=[card.active_now?'Working now':'Recently active',Number(card.active_child_count)?`${Number(card.active_child_count)} active collaborator${Number(card.active_child_count)===1?'':'s'}`:'',Number(card.surface_count)?`${Number(card.surface_count)} artifacts`:''].filter(Boolean);return facts.join(' · ')}
  if(card.column_state==='done')return card.summary||[Number(card.surface_count)?`Shipped with ${Number(card.surface_count)} artifacts`:'Finished',age(card.idle_seconds)+' ago'].join(' · ');
  if(card.column_state==='noticed')return card.summary||card.state_reason||'Recorded for context';
  if(card.column_state==='archived')return card.state_reason||'Archived from the active floor';
  return card.state_reason||'';
}
function viewerFirstName(){
  let value=String(state.viewer?.display_name||state.viewer?.email||document.querySelector('.warehouse-account-email')?.textContent||'').trim();
  if(!value)return 'there';
  if(value.includes('@'))value=value.split('@')[0];
  value=value.split(/\s+/)[0].replace(/[._-]+/g,' ').trim();
  return value?value.charAt(0).toUpperCase()+value.slice(1):'there';
}
function starterMeta(card){
  const facts=[],surfaces=Number(card.surface_count)||0,signals=Number(card.related_signal_count)||0;
  if(surfaces)facts.push(`${surfaces} artifact${surfaces===1?'':'s'}`);
  if(signals)facts.push(`${signals} signal${signals===1?'':'s'}`);
  if(card.column_state==='standing'&&card.schedule)facts.push(cadenceKind(card.schedule));
  if(!facts.length&&Number.isFinite(Number(card.idle_seconds)))facts.push(age(card.idle_seconds));
  return facts.slice(0,2);
}
function starterFromCard(card){
  const title=presentationTitle(card),context=contextLine(card)||card.summary||card.state_reason||'';
  if(card.item_kind==='page')return {title,kicker:'Your living Page',summary:context,meta:['Refresh on demand'],sourceKind:'page',sourceItemId:card.item_id,dismissKey:'promo:item:'+card.item_id,action:{kind:'page',id:card.page_id,session_id:card.session_id}};
  let kicker='A place to begin',prompt=`Help me explore “${title}”. Explain what it is, show me the useful evidence behind it, and suggest a practical next step.`;
  if(card.column_state==='noticed'){
    kicker='Calliope noticed';
    prompt=`You noticed “${title}”${context?` with this context: “${context}”`:''}. Help me understand why it may matter now, show me the underlying evidence, and suggest what I could do next.`;
  }else if(card.column_state==='done'){
    kicker=Number(card.surface_count)?'Something to build on':'From recent work';
    prompt=`Take me through “${title}”${context?` — ${context}`:''}. Show me the useful outputs and evidence, then help me decide whether to revisit, reuse, or extend it.`;
  }else if(card.column_state==='standing'){
    kicker='Always watching';
    prompt=`Tell me what “${title}” is watching${context?` — ${context}`:''}. Explain what has changed recently and whether there is anything I should care about.`;
  }else if(card.column_state==='awaiting_you'){
    kicker='Could use your eye';
    prompt=`Help me resolve “${title}”${context?` — ${context}`:''}. Explain the situation in plain language, show me the relevant evidence, and recommend what I should do next.`;
  }
  return {title,kicker,summary:context||'Calliope can gather the context and help you decide where to go next.',meta:starterMeta(card),prompt,sourceItemId:card.item_id,sourceKind:card.item_kind,dismissKey:'promo:item:'+String(card.item_id||'')};
}
function starterFromPromotion(item){
  const action=item?.action&&typeof item.action==='object'?item.action:{};
  return {id:String(item?.id||''),dismissKey:item?.spark_id?null:('promo:'+String(item?.id||'')),sparkId:item?.spark_id?String(item.spark_id):null,sparkKind:item?.spark_kind?String(item.spark_kind):null,renderMode:['artifact','metric','dream'].includes(item?.render_mode)?item.render_mode:'prompt',title:String(item?.title||'A place to begin'),kicker:String(item?.kicker||'A place to begin'),summary:String(item?.summary||''),meta:Array.isArray(item?.meta)?item.meta.filter(Boolean).slice(0,2).map(String):[],prompt:String(item?.prompt||''),thumbnailUrl:safeImageUrl(item?.thumbnail_url),value:item?.value,unit:String(item?.unit||''),deltaPercent:item?.delta_percent===null||item?.delta_percent===undefined?null:Number(item.delta_percent),dataAsOf:item?.data_as_of||null,action};
}
function buildFloorStarters(cards,promotions=[]){
  const idle=value=>value===null||value===undefined||!Number.isFinite(Number(value))?Infinity:Number(value);
  const ranked=(lane,predicate=()=>true)=>cards.filter(card=>card.column_state===lane&&predicate(card)).sort((a,b)=>(Number(b.attention_rank)||0)-(Number(a.attention_rank)||0)||idle(a.idle_seconds)-idle(b.idle_seconds));
  const chosen=[],seen=new Set();
  const hidden=state.hiddenStarterKeys||new Set();const take=card=>{if(!card)return;if(hidden.has('promo:item:'+String(card.item_id||'')))return;const title=presentationTitle(card).toLowerCase();if(!title||seen.has(title))return;seen.add(title);chosen.push(starterFromCard(card))};
  for(const promotion of promotions.slice(0,5)){const starter=starterFromPromotion(promotion),title=starter.title.toLowerCase();if(starter.dismissKey&&hidden.has(starter.dismissKey))continue;if(!title||seen.has(title)||!(starter.action.kind||starter.sparkId||starter.prompt))continue;seen.add(title);chosen.push(starter)}
  take(ranked('noticed',card=>Number(card.related_signal_count)>0)[0]||ranked('noticed')[0]);
  take(ranked('done',card=>Number(card.surface_count)>0)[0]||ranked('done')[0]);
  take(ranked('standing')[0]);
  for(const card of [...ranked('noticed'),...ranked('done',card=>Number(card.surface_count)>0),...ranked('awaiting_you',card=>card.signal_kind!=='blocked')]){if(chosen.length>=6)break;take(card)}
  const fallbacks=[
    {title:'Show me what is here',kicker:'A gentle first look',summary:'Get a plain-English tour of the data, dashboards, and company knowledge Calliope can reach.',meta:['company context'],prompt:'Give me a plain-English tour of the data, dashboards, and company knowledge available here, then suggest one useful place to start.'},
    {title:'Help me make something useful',kicker:'Start with an outcome',summary:'Calliope can ask a few simple questions and turn the answers into a useful first artifact.',meta:['guided start'],prompt:'Ask me a few simple questions, then help me make a useful dashboard or artifact from the data available here.'}
  ];
  for(const fallback of fallbacks){if(chosen.length>=Math.min(4,Math.max(2,cards.length?4:2)))break;if(!seen.has(fallback.title.toLowerCase()))chosen.push(fallback)}
  return chosen.slice(0,6);
}
// Other people's private work shows as presence, not content: who is in the
// room and that the room is busy — never the title, the summary, or a way
// in. A summons (view grant) upgrades the shell to a full piece.
function silhouetteMarkup(card){
  const person=state.people.get(String(card.owner_email||''));
  const who=person&&person.display_name?person.display_name:displayAgent(card.owner_email);
  const agents=normalizeAgents(card.agents).filter(a=>a!==card.owner_email).map(displayAgent);
  const line=agents.length?`with ${agents.slice(0,3).join(' + ')}`:'working privately';
  return `<article class="piece silhouette${card.active_now?' active':''}" aria-label="${esc(who)} · private work in motion">
    <div class="piece-top"><div class="piece-title">${esc(who)}</div></div>
    <div class="reason now">${esc(line)}</div>
    <div class="piece-foot">${avatars(card)}<span class="foot-spacer"></span>${state.timeMode==='history'?'':`<button type="button" class="piece-action piece-knock" data-piece-knock="${esc(card.item_id)}" title="Ask ${esc(who)} to let you into this chat">${state.knocked&&state.knocked.has(card.item_id)?'Knocked':'Knock'}</button>`}<span class="age">${esc(state.timeMode==='history'?'Then':age(card.idle_seconds))}</span></div>
  </article>`;
}
// Knock: ask to join a private chat you can see is in motion. The owner gets
// a card to let you in; once they do, the chat shows here as a full piece.
async function knockOnSilhouette(button){
  if(!button||button.disabled)return;
  const id=String(button.dataset.pieceKnock||'');
  const reason=window.prompt('Knock on this chat. Add a note for its owner (optional):','');
  if(reason===null)return;
  button.disabled=true;
  try{
    await api(`/api/calliope/floor/silhouettes/${encodeURIComponent(id.replace(/^shadow:/,''))}/knock`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({reason})});
    state.knocked=state.knocked||new Set();state.knocked.add(id);
    button.textContent='Knocked';
    button.title='Its owner will see your knock. Once they let you in, the chat appears on your Floor.';
  }catch(error){
    button.disabled=false;
    window.alert(error&&error.message?error.message:'Could not knock');
  }
}
function isMine(card){const me=String(state.viewer?.email||'').toLowerCase();return Boolean(me&&String(card.owner_email||'').toLowerCase()===me)}
// The whole drag grammar in one table: (piece, lane) -> contract. Drag is
// sugar over the same doors the buttons open — drops gesture, contracts
// confirm; only reversible receipted moves (set aside / restore) fire
// instantly. The server re-validates every one of these regardless.
function legalMoves(card){
  if(state.timeMode==='history'||card.silhouette)return [];
  const mine=!card.owner_email||isMine(card);
  const moves=[];
  if(mine&&card.item_kind==='curated_dream'&&card.column_state==='noticed'&&!card.session_id){
    moves.push({target:'in_motion',hint:'Have Callie explore this Dream without you',act:()=>engageDream(card)});
  }
  if(playbookApproved(card)){
    moves.push({target:'in_motion',hint:'Run now — asks for optional direction first',act:()=>launchPieceChat(card)});
    moves.push({target:'standing',hint:'Schedule — run it on a heartbeat',act:()=>openPlaybookSchedule(card)});
  }
  if(mine&&(card.column_state==='awaiting_you'||card.column_state==='noticed')){
    moves.push({target:'archived',hint:'Set aside — not done, stays findable',act:()=>setAsidePiece(card)});
  }
  if(mine&&card.column_state==='awaiting_you'&&card.item_kind==='work_item'&&!card.engaged_session_id){
    moves.push({target:'in_motion',hint:'Hand to Callie — she starts without you',act:()=>engagePiece(card)});
  }
  if(mine&&card.column_state==='archived'&&card.set_aside){
    moves.push({target:'awaiting_you',hint:'Restore to the Floor — returns to its current lane',act:()=>restorePiece(card)});
  }
  return moves;
}
// Same contract as the drawer's standing door, reachable without the drawer
// (kanban drag drops a playbook here). Fetches fresh so the modal always
// schedules the currently approved version's title.
async function openPlaybookSchedule(card){
  const playbookId=String(card.item_id||'').split(':')[1];if(!playbookId)return;
  try{
    const data=await api(`/api/calliope/playbooks/${encodeURIComponent(playbookId)}`);
    const pb=data.playbook||data||{};
    openScheduleModal({
      title:String(pb.title||presentationTitle(card)),
      defaultMission:'Run this playbook for the current period and post the results to this thread. Flag anything that needs my attention.',
      onSubmit:async(s)=>{
        const saved=await api(`/api/calliope/playbooks/${encodeURIComponent(playbookId)}/schedule`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({schedule:s.cron,mission:s.mission})});
        showToast(`Scheduled — runs ${s.echo}. The standing thread is on your Floor.`);
        document.getElementById('drawerBackdrop')?.classList.remove('open');
        load(true).catch(()=>{});
        return saved;
      },
    });
  }catch(err){console.error(err);showToast('Could not read that playbook. Please try again.')}
}
function channelMarkup(card){
  if(card.silhouette||card.item_kind!=='chat')return '';
  const channel=String(card.channel||'').trim().toLowerCase();
  if(!channel||channel==='web')return '';
  const label=({google_chat:'Google Chat',slack:'Slack',discord:'Discord',telegram:'Telegram',whatsapp:'WhatsApp',signal:'Signal',sms:'SMS',teams:'Microsoft Teams',msteams:'Microsoft Teams'})[channel]||channel.replace(/[_-]+/g,' ');
  const description=`${label} · mirrored conversation`;
  return `<span class="piece-channel" role="img" aria-label="${esc(description)}" title="${esc(description)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M10 5h4M11 19h2"/></svg></span>`;
}
function favoriteMarkup(card){return card.favorite?`<span class="piece-favorite" title="Favorite thread" aria-label="Favorite thread"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.8 5.7 6.3.9-4.55 4.45 1.08 6.28L12 17.36l-5.63 2.97 1.08-6.28L2.9 9.6l6.3-.9Z"/></svg></span>`:''}
function cardMarkup(card){
  if(card.silhouette)return silhouetteMarkup(card);
  const reason=contextLine(card);
  const title=presentationTitle(card);
  const child=Number(card.child_thread_count)||0,signals=Number(card.related_signal_count)||0,surfaces=Number(card.surface_count)||0;
  const canDrag=legalMoves(card).length>0;
  const thumbnail=safeImageUrl(card.thumbnail_url);
  return `<article class="piece ${card.favorite?'is-favorite ':''}${card.item_kind==='page'?'is-page ':''}${thumbnail?'has-texture ':''}${card.active_now?'active ':''}${isMine(card)?'mine ':''}${canDrag?'can-drag ':''}${card.column_state==='awaiting_you'&&card.signal_kind==='blocked'?'attention':''}" draggable="${canDrag}" tabindex="0" data-id="${esc(card.item_id)}" aria-label="${esc(title)}">
    ${thumbnail?`<span class="piece-texture" aria-hidden="true"><img src="${esc(thumbnail)}" alt="" loading="lazy" decoding="async" draggable="false"></span>`:''}
    <div class="piece-top"><div class="piece-title">${favoriteMarkup(card)}${esc(title)}</div>${chipMarkup(card)}${canDrag?`<span class="piece-grip" title="Drag to a lane — valid targets light up" aria-hidden="true">⠿</span>`:''}${channelMarkup(card)}</div>
    ${reason?`<div class="reason${card.item_kind==='duty_cycle'?' schedule-line':' now'}" title="${esc(reason)}">${esc(reason)}</div>`:''}
    ${dreamTileMeta(card)}
    <div class="piece-foot">${avatars(card)}${primaryMarkup(card)}<span class="foot-spacer"></span>${healthMarkup(card)}${child?`<span class="metric" title="Collaborators">${icon.users}${child}</span>`:''}${signals?`<span class="metric" title="Open signals">${icon.signal}${signals}</span>`:''}${surfaces?`<span class="metric" title="Artifacts">${icon.layers}${surfaces}</span>`:''}<span class="age">${esc(state.timeMode==='history'?'Then':age(card.idle_seconds))}</span></div>
  </article>`;
}
function currentFilters(){$('floorPages').setAttribute('aria-pressed',String($('kinds').value==='page'));return {q:$('search').value.trim().toLowerCase(),person:$('people').value,kind:$('kinds').value,fresh:$('freshness').value}}
function filteredCards(){return [...state.cards].sort((a,b)=>Number(Boolean(b.favorite))-Number(Boolean(a.favorite)))}
function laneQuery(name){return String(state.laneQueries.get(name)||'').trim().toLowerCase()}
function laneMatch(card,query){if(!query)return true;if(card.silhouette)return false;return [presentationTitle(card),contextLine(card),card.summary,card.state_reason,card.item_kind,card.owner_email,...normalizeAgents(card.agents).map(displayAgent)].join(' ').toLowerCase().includes(query)}
// The Daily Brief tile is not a piece: it is the day's edition standing at
// the door. Awaiting until opened, Done for the rest of the day, back
// tomorrow — state derives entirely from the brief row, no ledger.
function briefTileMarkup(lane){
  const b=state.dailyBrief;if(!b||state.timeMode==='history')return '';
  if(lane==='awaiting_you'&&b.state==='awaiting'){
    const dateLabel=new Date(b.date+'T00:00:00').toLocaleDateString([],{month:'long',day:'numeric'});
    const teaser=b.prior?`Yesterday's edition carried ${Number(b.prior.item_count||0).toLocaleString()} items. Today's assembles when you open it.`:'Calendar, mentions, and work that moved — assembled when you open it.';
    return `<a class="brief-tile" href="${esc(b.open_url)}" aria-label="Open today's brief"><span class="brief-tile-kicker">${icon.spark}<b>Daily Brief</b></span><strong>${esc(dateLabel)}</strong><span class="brief-tile-summary">${esc(teaser)}</span><span class="brief-tile-go">Open the brief&nbsp;→</span></a>`;
  }
  if(lane==='done'&&b.state==='done'){
    const readAt=b.read_at?new Date(b.read_at):null;
    const when=readAt&&!Number.isNaN(readAt.getTime())?readAt.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):'today';
    return `<a class="brief-tile done" href="${esc(b.open_url)}" aria-label="Reopen today's brief"><span class="brief-tile-kicker">${icon.spark}<b>Daily Brief</b></span><strong>Read · ${esc(when)}</strong><span class="brief-tile-summary">${Number(b.item_count||0).toLocaleString()} grounded items · reopen anytime</span></a>`;
  }
  return '';
}
function laneMarkup(name,cards,baseCount){
  const q=laneQuery(name),open=state.searchOpen.has(name),count=q?`${laneMatched(name)}/${baseCount}`:String(laneMatched(name));
  const hide=state.expanded.has(name)?`<button type="button" class="lane-action collapse-lane" data-collapse="${name}" title="Collapse lane">hide</button>`:'';
  const briefTile=briefTileMarkup(name);
  return `<section class="lane" data-lane="${name}"><div class="lane-head"><span>${LANE_LABEL[name]}</span><span class="count">${count}</span><div class="lane-actions"><button type="button" class="lane-action ${q?'active':''}" data-lane-find="${name}" aria-label="Search ${LANE_LABEL[name]}" aria-pressed="${open}">${icon.search}</button>${hide}</div></div><label class="lane-search-row ${open?'open':''}">${icon.search}<input type="search" data-lane-query="${name}" value="${esc(q)}" placeholder="Search this lane" aria-label="Search ${LANE_LABEL[name]}"><button type="button" data-lane-clear="${name}" aria-label="Clear lane search">×</button></label><div class="lane-body"></div></section>`
}
function railMarkup(name,count){return `<aside class="rail" data-lane="${name}"><button data-expand="${name}" aria-label="Expand ${LANE_LABEL[name]}">${icon.eye}<span class="rail-label">${LANE_LABEL[name]}</span><span class="rail-hint"></span><span class="rail-count">${Number(count||0).toLocaleString()}</span></button></aside>`}
function formatStarterMetric(value,unit){const number=Number(value);if(!Number.isFinite(number))return String(value??'—');const options={maximumFractionDigits:Math.abs(number)<10&&number%1?2:0};if(Math.abs(number)>=1000000)options.notation='compact';let formatted=new Intl.NumberFormat([],options).format(number),cleanUnit=String(unit||'').toLowerCase();if(/usd|dollar|currency/.test(cleanUnit))formatted='$'+formatted;else if(/percent|percentage|%/.test(cleanUnit))formatted+='%';return formatted}
function starterVisualMarkup(starter){
  if(starter.renderMode==='artifact')return `<span class="starter-artifact-preview"><span class="starter-artifact-fallback" aria-hidden="true">▦</span>${starter.thumbnailUrl?`<img data-floor-starter-thumb src="${esc(starter.thumbnailUrl)}" alt="" loading="lazy" decoding="async">`:''}<span class="starter-artifact-badge">Published</span></span>`;
  if(starter.renderMode==='metric'){const delta=starter.deltaPercent,deltaCopy=delta===null?'Latest reading':Math.abs(delta)<.005?'Steady':`${delta>0?'↑':'↓'} ${Math.abs(delta).toLocaleString([],{maximumFractionDigits:1})}%`;return `<span class="starter-metric-display"><span class="starter-metric-value" title="${esc(starter.value)}">${esc(formatStarterMetric(starter.value,starter.unit))}</span><span class="starter-metric-delta ${delta===null||Math.abs(delta)<.005?'steady':delta>0?'up':'down'}">${esc(deltaCopy)}</span></span>`}
  if(starter.renderMode==='dream')return `<span class="starter-dream-mark" aria-hidden="true"><svg class="starter-dream-arcs" viewBox="0 0 160 42" preserveAspectRatio="xMinYMax meet" focusable="false"><path d="M 16 41 A 20 20 0 0 1 56 41"/><path d="M 4 41 A 32 32 0 0 1 68 41"/><path d="M -8 41 A 44 44 0 0 1 80 41"/></svg><b>possibility</b></span>`;
  return '';
}
function starterCardMarkup(starter,index){return `<button type="button" class="starter-card mode-${esc(starter.renderMode||'prompt')}" data-floor-starter="${index}" title="${starter.sourceKind==='page'?'Open your saved Page — refresh only when you ask':'Starts a new chat immediately — no preview'}" aria-label="${starter.sourceKind==='page'?'Open Page ·':'Start with'} ${esc(starter.title)}">${(starter.sparkId||starter.dismissKey)?`<span class="starter-dismiss" data-starter-dismiss="${index}" role="button" tabindex="0" aria-label="Not now" title="Not now — this won't be offered again">×</span>`:''}${starterVisualMarkup(starter)}<span class="starter-kicker">${icon.spark}${esc(starter.kicker)}</span><strong>${esc(starter.title)}</strong><span class="starter-summary">${esc(starter.summary)}</span><span class="starter-foot"><span class="starter-meta">${starter.meta.map(item=>`<span>${esc(item)}</span>`).join('')}</span><span class="starter-go">${starter.sourceKind==='page'?'Open Page':'Start here'}&nbsp; →</span></span></button>`}
function starterLaneMarkup(){return `<section class="lane starter-lane" aria-label="Suggestions for ${esc(viewerFirstName())}"><div class="lane-head"><span>Hi, ${esc(viewerFirstName())}</span><span class="count">${state.starters.length}</span><div class="lane-actions"><button type="button" class="lane-action collapse-lane" data-collapse-starters aria-label="Hide suggestions from Calliope" title="Hide suggestions">hide</button></div></div><div class="starter-intro">A few useful threads Calliope can pick up with you.</div><div class="lane-body starter-body">${state.starters.map(starterCardMarkup).join('')}</div></section>`}
function starterRailMarkup(){return `<aside class="rail starter-rail"><button type="button" data-expand-starters aria-label="Show suggestions from Calliope">${icon.spark}<span class="rail-label">For ${esc(viewerFirstName())}</span><span class="rail-hint"></span><span class="rail-count">${state.starters.length}</span></button></aside>`}
function renderBoard(cards){
  const base=Object.fromEntries(LANE_ORDER.map(n=>[n,cards.filter(c=>c.column_state===n)]));
  const grouped=base;
  const scrolls=new Map([...laneWindows].map(([name,view])=>[name,view.body.scrollTop]));
  clearLaneWindows();
  const cls=['board',state.startersExpanded?'':'starters-collapsed',state.expanded.has('noticed')?'expand-noticed':'',state.expanded.has('archived')?'expand-archived':''].filter(Boolean).join(' ');
  $('workspace').innerHTML=`<div class="${cls}" id="board">${state.startersExpanded?starterLaneMarkup():starterRailMarkup()}${LANE_ORDER.map(n=>(n==='noticed'||n==='archived')&&!state.expanded.has(n)?railMarkup(n,laneMatched(n)):laneMarkup(n,grouped[n],Number(state.laneRows.find(r=>r.column_state===n)?.pieces)||0)).join('')}</div>`;
  bindBoardEvents();
  for(const lane of document.querySelectorAll('.lane[data-lane]')){
    const name=lane.dataset.lane; if(!laneHeights.has(name))laneHeights.set(name,new Map());
    laneWindows.set(name,new FloorWindow(lane.querySelector('.lane-body'),{items:grouped[name],total:laneMatched(name),markup:cardMarkup,header:briefTileMarkup(name),heights:laneHeights.get(name),onRender:body=>{bindPieceEvents(body);ensureVisibleAvatars()},onMore:()=>{if(!state.pageLoading&&state.scrubPreviewAt===null)stream.request(name)}}));
    if(scrolls.has(name)){const view=laneWindows.get(name);view.body.scrollTop=scrolls.get(name);view.draw()}
  }
}
function fitLanes(){for(const view of laneWindows.values())view.draw()}
function renderList(cards){
  clearLaneWindows();cards=cards.filter(c=>!c.silhouette);
  $('workspace').innerHTML='<div class="listview floor-stream-list"></div>';
  const body=$('workspace').firstElementChild;
  laneWindows.set('list',new FloorWindow(body,{items:cards,total:state.laneRows.reduce((n,r)=>n+Number(r.matched??r.pieces),0),header:'<div class="floor-list-row floor-list-head" data-window-header><span>Piece</span><span>Now</span><span>Lane</span><span>Kind</span><span>People</span><span>Activity</span></div>',markup:c=>`<div class="floor-list-row ${c.favorite?'is-favorite ':''}${c.item_kind==='page'?'is-page':''}" data-open="${esc(c.item_id)}" tabindex="0"><strong>${favoriteMarkup(c)}${esc(presentationTitle(c))}</strong><span>${esc(contextLine(c))}</span><span>${esc(LANE_LABEL[c.column_state]||c.column_state)}</span><span>${esc(kindLabel(c.item_kind))}</span><span>${esc([c.owner_email,...normalizeAgents(c.agents).map(displayAgent)].filter(Boolean).join(', '))}</span><span>${esc(age(c.idle_seconds))}</span></div>`,onRender:root=>root.querySelectorAll('[data-open]').forEach(el=>{if(el.dataset.floorBound)return;el.dataset.floorBound='1';el.addEventListener('click',()=>openDrawer(el.dataset.open));el.addEventListener('keydown',e=>{if(e.key==='Enter')openDrawer(el.dataset.open)})}),onMore:()=>{const lane=LANE_ORDER.find(n=>state.pages[n]?.has_more);if(lane)stream.request(lane)}}));
}
// ── Grid view ("Me") ──────────────────────────────────────────────────────
// Every piece the viewer is part of as square tiles, newest activity first,
// row-major, tinted by lane, with the thread's newest published artifact as
// the thumbnail when it has one. Click opens the thread; shift/alt-click
// opens the drawer. Same stream, filters and search as the board — it just
// drains every lane instead of windowing, on purpose, to see how far that goes.
function gridHref(c){return c.item_kind!=='playbook'&&c.session_id?`/calliope?${new URLSearchParams({session:String(c.session_id),view:'zen'})}`:''}
function gridSorted(cards){return cards.filter(c=>!c.silhouette&&(!state.hideNotices||c.column_state!=='noticed')).sort((a,b)=>{const at=Date.parse(a.last_activity_at||'')||0,bt=Date.parse(b.last_activity_at||'')||0;return (Number(Boolean(b.favorite))-Number(Boolean(a.favorite)))||(bt-at)||((Number(a.idle_seconds)||0)-(Number(b.idle_seconds)||0))})}
function gridTileMarkup(c){
  const title=presentationTitle(c),reason=contextLine(c)||String(c.summary||''),href=gridHref(c);
  const thumb=c.thumbnail_url?`<img class="grid-thumb" src="${esc(c.thumbnail_url)}" alt="" loading="lazy" decoding="async">`:'';
  return `<a class="grid-tile ${c.favorite?'is-favorite ':''}${c.item_kind==='page'?'is-page ':''}lane-${esc(c.column_state)}${c.active_now?' active':''}${isMine(c)?' mine':''}${thumb?' has-thumb':''}" href="${esc(href||'#')}"${href?'':' data-drawer-only="1"'} data-id="${esc(c.item_id)}" title="${esc(title)}"><span class="grid-tint" aria-hidden="true"></span>${thumb}<span class="grid-content"><span class="grid-body"><span class="grid-kicker"><b>${esc(LANE_LABEL[c.column_state]||c.column_state)}</b> · ${esc(kindLabel(c.item_kind))}${c.item_kind==='curated_dream'?' · '+esc(dreamStage(c.dream)):''}</span>${!thumb&&reason?`<span class="grid-reason">${esc(reason)}</span>`:''}</span><span class="grid-foot"><strong class="grid-title">${favoriteMarkup(c)}${esc(title)}</strong>${dreamTileMeta(c)}<span class="grid-meta">${avatars(c)}<span class="age">${esc(age(c.idle_seconds))}</span></span></span></span></a>`;
}
function gridSignature(c){return [c.favorite?1:0,c.item_kind,c.page_id,c.column_state,c.active_now?1:0,isMine(c)?1:0,presentationTitle(c),contextLine(c)||String(c.summary||''),c.thumbnail_url||'',c.session_id||'',JSON.stringify(c.dream||null)].join('|')}
// Keyed reconciliation: a live tick replaces only tiles that changed, moves
// only tiles that re-ranked, and never rebuilds the container — so scroll
// position, loaded thumbnails and hover state all survive the refresh.
// Rows follow the real column width so every tile is a square the track can
// see; re-measured whenever the grid resizes.
function syncGridRows(grid){const first=grid?.firstElementChild;if(!first)return;const width=Math.round(first.getBoundingClientRect().width);if(width>0&&grid.dataset.tile!==String(width)){grid.dataset.tile=String(width);grid.style.setProperty('--tile',`${width}px`)}}
function renderGrid(cards){
  clearLaneWindows();const sorted=gridSorted(cards);
  let grid=$('floorGrid');
  if(!grid){
    $('workspace').innerHTML='<div class="floor-grid" id="floorGrid"></div>';grid=$('floorGrid');
    grid.addEventListener('click',e=>{const tile=e.target.closest('.grid-tile');if(!tile)return;if(e.shiftKey||e.altKey||tile.dataset.drawerOnly){e.preventDefault();openDrawer(tile.dataset.id)}});
    grid.addEventListener('error',e=>{const img=e.target;if(img?.classList?.contains('grid-thumb')){img.closest('.grid-tile')?.classList.remove('has-thumb');img.remove()}},true);
    if(typeof ResizeObserver==='function')new ResizeObserver(()=>syncGridRows(grid)).observe(grid);
  }
  const wanted=new Set(sorted.map(c=>String(c.item_id)));
  for(const el of [...grid.children]){if(!wanted.has(el.dataset.id))el.remove()}
  const tpl=document.createElement('template');
  sorted.forEach((c,position)=>{
    const id=String(c.item_id),sig=gridSignature(c);
    let el=grid.querySelector(`:scope>.grid-tile[data-id="${CSS.escape(id)}"]`);
    if(el&&el.dataset.sig!==sig){tpl.innerHTML=gridTileMarkup(c);const fresh=tpl.content.firstElementChild;fresh.dataset.sig=sig;el.replaceWith(fresh);el=fresh}
    else if(el){const ageEl=el.querySelector('.age');if(ageEl)ageEl.textContent=state.timeMode==='history'?'Then':age(c.idle_seconds)}
    else{tpl.innerHTML=gridTileMarkup(c);el=tpl.content.firstElementChild;el.dataset.sig=sig}
    const current=grid.children[position]||null;if(current!==el)grid.insertBefore(el,current);
  });
  syncGridRows(grid);ensureVisibleAvatars();
  const remaining=LANE_ORDER.filter(n=>state.pages[n]?.has_more&&(!state.hideNotices||n!=='noticed'));
  if(remaining.length&&!state.pageLoading&&state.scrubPreviewAt===null)stream.request(remaining[0]);
  if(!remaining.length&&state.gridLoadStartedAt){state.gridLoadedIn=(performance.now()-state.gridLoadStartedAt)/1000;state.gridLoadStartedAt=0}
  const thumbs=sorted.filter(c=>c.thumbnail_url).length;
  $('summaryLine').innerHTML=`<strong>${sorted.length.toLocaleString()} of my threads</strong> · newest first${state.hideNotices?' · notices hidden':''}${thumbs?` · ${thumbs} with artifacts`:''}${remaining.length?' · still loading…':state.gridLoadedIn?` · loaded in ${state.gridLoadedIn.toFixed(1)}s`:''}`;
}
function render(){
  const active=document.activeElement,laneFocus=active?.dataset?.laneQuery,selection=laneFocus?active.selectionStart:null;
  renderTimeline();
  const cards=filteredCards(),count=n=>Number(state.laneRows.find(r=>r.column_state===n)?.pieces)||0,total=state.total;
  const lead=state.timeMode==='history'?`<strong>Past · ${esc(localStamp(state.selectedSnapshotAt,true))}</strong> · ${total.toLocaleString()} pieces`:`<strong>${total.toLocaleString()} pieces</strong> across the system`;
  $('summaryLine').innerHTML=`${lead} · ${count('awaiting_you')} await you · ${count('in_motion')} in motion · ${count('standing')} standing · ${count('noticed')} notices hidden`;
  if(state.pageLoading)$('summaryLine').textContent='Loading this view…';
  if(state.view==='board')renderBoard(cards);else if(state.view==='grid')renderGrid(cards);else renderList(cards);
  if(laneFocus){const input=document.querySelector(`[data-lane-query="${laneFocus}"]`);if(input){input.focus({preventScroll:true});if(selection!==null)input.setSelectionRange(selection,selection)}}
}
function endFloorDrag(){
  state.drag=null;hideDragHint();
  document.getElementById('board')?.classList.remove('drag-live');
  document.querySelectorAll('.drop-ok,.drop-no,.drag-target').forEach(el=>el.classList.remove('drop-ok','drop-no','drag-target'));
}
function moveDragHint(x,y,text){
  let el=document.getElementById('floorDragHint');
  if(!el){el=document.createElement('div');el.id='floorDragHint';el.className='drag-hint';document.body.appendChild(el)}
  el.textContent=text;el.style.left=`${x+14}px`;el.style.top=`${y+18}px`;el.hidden=false;
}
function hideDragHint(){const el=document.getElementById('floorDragHint');if(el)el.hidden=true}
function bindPieceEvents(root){
  root.querySelectorAll('.piece-texture img').forEach(img=>{if(img.dataset.floorBound)return;img.dataset.floorBound='1';const clear=()=>{img.closest('.piece')?.classList.remove('has-texture');img.parentElement?.remove()};if(img.complete&&!img.naturalWidth)clear();else img.addEventListener('error',clear,{once:true})});
  root.querySelectorAll('.piece[data-id]').forEach(el=>{
    if(el.dataset.floorBound)return;el.dataset.floorBound='1';
    el.addEventListener('click',()=>openDrawer(el.dataset.id));el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openDrawer(el.dataset.id)}});
    el.addEventListener('dragstart',e=>{
      const card=state.cards.find(c=>c.item_id===el.dataset.id);
      const moves=card?legalMoves(card):[];
      if(!moves.length){e.preventDefault();return}
      state.drag={id:el.dataset.id,moves};
      el.classList.add('dragging');e.dataTransfer.setData('text/plain',el.dataset.id);e.dataTransfer.effectAllowed='move';
      // No re-render here: pulling the source node mid-drag aborts native
      // HTML5 drag. Targets light up by class; rails widen into drop strips.
      document.getElementById('board')?.classList.add('drag-live');
      document.querySelectorAll('.lane[data-lane],.rail[data-lane]').forEach(zone=>{
        zone.classList.toggle('drop-ok',moves.some(m=>m.target===zone.dataset.lane));
        zone.classList.toggle('drop-no',!moves.some(m=>m.target===zone.dataset.lane));
      });
    });
    el.addEventListener('dragend',()=>{el.classList.remove('dragging');endFloorDrag()});
  });
  root.querySelectorAll('[data-piece-action]').forEach(b=>{if(b.dataset.floorBound)return;b.dataset.floorBound='1';b.addEventListener('click',e=>{e.stopPropagation();const c=state.cards.find(x=>x.item_id===b.dataset.pieceAction);if(c&&['decision','input_request'].includes(c.item_kind)){openDrawer(c.item_id);return}launchPieceChat(c)});});
  root.querySelectorAll('[data-piece-async]').forEach(b=>{if(b.dataset.floorBound)return;b.dataset.floorBound='1';b.addEventListener('click',e=>{e.stopPropagation();const c=state.cards.find(x=>x.item_id===b.dataset.pieceAsync);engagePiece(c)});});
  root.querySelectorAll('[data-piece-aside]').forEach(b=>{if(b.dataset.floorBound)return;b.dataset.floorBound='1';b.addEventListener('click',e=>{e.stopPropagation();const c=state.cards.find(x=>x.item_id===b.dataset.pieceAside);setAsidePiece(c)});});
  root.querySelectorAll('[data-piece-knock]').forEach(b=>{if(b.dataset.floorBound)return;b.dataset.floorBound='1';if(state.knocked&&state.knocked.has(b.dataset.pieceKnock))b.disabled=true;b.addEventListener('click',e=>{e.stopPropagation();knockOnSilhouette(b)});});
}
function bindBoardEvents(){
  document.querySelectorAll('.lane[data-lane],.rail[data-lane]').forEach(zone=>{
    const laneName=zone.dataset.lane;
    zone.addEventListener('dragover',e=>{
      const move=state.drag?.moves.find(m=>m.target===laneName);
      if(!move)return;               // no preventDefault -> browser shows no-drop
      e.preventDefault();e.dataTransfer.dropEffect='move';
      zone.classList.add('drag-target');
      moveDragHint(e.clientX,e.clientY,move.hint);
    });
    zone.addEventListener('dragleave',()=>{zone.classList.remove('drag-target');hideDragHint()});
    zone.addEventListener('drop',e=>{
      const drag=state.drag;
      const move=drag?.moves.find(m=>m.target===laneName);
      if(!move)return;
      e.preventDefault();
      const card=state.cards.find(c=>c.item_id===drag.id);
      endFloorDrag();
      if(card)move.act();
    });
  });
  document.querySelectorAll('[data-floor-starter]').forEach(button=>button.addEventListener('click',event=>{const dismiss=event.target.closest?.('[data-starter-dismiss]');const starter=state.starters[Number(button.dataset.floorStarter)];if(dismiss){event.preventDefault();event.stopPropagation();dismissStarter(starter);return}beginFloorStarter(starter)}));
  if(state.startersExpanded)noteSparkImpressions();
  document.querySelectorAll('[data-expand-starters]').forEach(button=>button.addEventListener('click',()=>{state.startersExpanded=true;render()}));
  document.querySelectorAll('[data-collapse-starters]').forEach(button=>button.addEventListener('click',()=>{state.startersExpanded=false;render()}));
  document.querySelectorAll('[data-expand]').forEach(b=>b.addEventListener('click',()=>{state.expanded.add(b.dataset.expand);render()}));
  document.querySelectorAll('[data-collapse]').forEach(b=>b.addEventListener('click',()=>{state.expanded.delete(b.dataset.collapse);render()}));
  document.querySelectorAll('[data-lane-find]').forEach(b=>b.addEventListener('click',()=>{const n=b.dataset.laneFind;state.searchOpen.has(n)?state.searchOpen.delete(n):state.searchOpen.add(n);render();if(state.searchOpen.has(n))requestAnimationFrame(()=>document.querySelector(`[data-lane-query="${n}"]`)?.focus())}));
  document.querySelectorAll('[data-lane-clear]').forEach(b=>b.addEventListener('click',()=>{const n=b.dataset.laneClear;state.laneQueries.delete(n);const input=document.querySelector(`[data-lane-query="${n}"]`);if(input)input.value='';scheduleFloorSearch()}));
  document.querySelectorAll('[data-lane-query]').forEach(input=>input.addEventListener('input',e=>{const n=input.dataset.laneQuery,value=e.target.value;value?state.laneQueries.set(n,value):state.laneQueries.delete(n);state.searchOpen.add(n);scheduleFloorSearch()}));
}
const sparkImpressed=new Set();
function sendSparkEvents(events){if(!events.length)return;api('/api/calliope/floor/sparks/events',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({events})}).catch(()=>{})}
function noteSparkImpressions(){sendSparkEvents(state.starters.filter(s=>s.sparkId&&!sparkImpressed.has(s.sparkId)).map(s=>{sparkImpressed.add(s.sparkId);return{id:s.sparkId,kind:'impressed'}}))}
function dismissStarter(starter){if(!starter)return;if(starter.action?.kind==='dream'&&starter.action.id)return updateFloorDream({item_id:'curated_dream:'+starter.action.id},'dismiss');if(starter.sparkId){sendSparkEvents([{id:starter.sparkId,kind:'hidden'}]);state.starters=state.starters.filter(s=>s.sparkId!==starter.sparkId)}else if(starter.dismissKey){sendSparkEvents([{kind:'hidden',dedupe_key:starter.dismissKey,title:starter.title}]);(state.hiddenStarterKeys||(state.hiddenStarterKeys=new Set())).add(starter.dismissKey);state.starters=state.starters.filter(s=>s!==starter)}else return;render();showToast('Put away — it will not be offered again')}
async function beginFloorStarter(starter){
  if(!starter||state.starterLaunching)return;
  if(starter.sourceKind==='input_request'){openDrawer(starter.sourceItemId);return}
  if(starter.sourceKind==='page'){
    let session=starter.action?.session_id;
    try{
      if(!session&&starter.action?.id){const data=await api(`/api/calliope/pages/${encodeURIComponent(starter.action.id)}/open`,{method:'POST',headers:{'content-type':'application/json'},body:'{}'});session=data.page?.session_id}
      if(session)window.location.assign('/calliope?'+new URLSearchParams({session:String(session),view:'zen'}));
      else showToast('This Page could not be opened. Find it in the Pages library.');
    }catch(error){showToast(error.message)}
    return;
  }
  if(starter.sparkId)sendSparkEvents([{id:starter.sparkId,kind:'entered'}]);
  state.starterLaunching=true;showToast(`Starting with “${starter.title}”…`);
  const handoffId=`floor-${Date.now()}-${Math.random().toString(36).slice(2,10)}`,action=starter.action||{};
  try{
    let frozen=null;
    if(action.kind==='artifact'&&action.slug)frozen=await api(`/api/calliope/gallery/artifacts/${encodeURIComponent(action.slug)}/ask`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({origin:'floor'})});
    else if(action.kind==='metric'&&action.name)frozen=await api(`/api/calliope/gallery/metrics/${encodeURIComponent(action.name)}/ask`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({params:action.params||{},origin:'floor'})});
    else if(action.kind==='subject'&&action.key)frozen=await api(`/api/calliope/floor/areas/${encodeURIComponent(action.key)}/ask`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({origin:'floor'})});
    else if(action.kind==='dream'&&action.id)frozen=await api(`/api/calliope/dreams/${encodeURIComponent(action.id)}/handoff`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({origin:'floor',direction:starter.direction||''})});
    if(action.kind==='dream'&&frozen?.session?.id&&frozen?.url){window.location.assign(frozen.url);return}
    if(['metric','dream','subject'].includes(action.kind)&&!(frozen?.session?.id&&frozen?.surface?.id&&frozen?.url))throw new Error('Saved context could not be prepared');
    if(frozen?.session?.id&&frozen?.surface?.id&&frozen?.url){
      const destination=new URL(frozen.url,window.location.origin),prompt=destination.searchParams.get('prompt')||starter.prompt;
      await writeFloorIntakeHandoff({id:handoffId,launch_mode:'starter_surface',prompt,created_at:Date.now(),agent_key:'calliope',team_mode:false,attachments:[],session_id:frozen.session.id,surface_id:frozen.surface.id,source_item_id:starter.id||starter.sourceItemId||null,source_kind:action.kind});
      destination.searchParams.delete('prompt');destination.searchParams.set('view','zen');window.location.assign(destination.pathname+destination.search);return;
    }
    const payload={id:handoffId,launch_mode:'suggestion',prompt:starter.prompt,created_at:Date.now(),agent_key:'calliope',team_mode:false,attachments:[],source_item_id:starter.sourceItemId||starter.id||null,source_kind:starter.sourceKind||action.kind||null};
    await writeFloorIntakeHandoff(payload);window.location.assign('/calliope?view=zen&start=1');
  }
  catch(err){console.error(err);state.starterLaunching=false;showToast('That starting point could not be opened. Please try again.');return false}
}
function stagePrimary(id){const c=state.cards.find(x=>x.item_id===id);if(!c)return;state.stagedAction=id;openDrawer(id);showToast(`${actionVerb(c)} staged for Callie · prototype only`)}
// The attended launch: a CTA verb performs the verb. One click composes the
// piece's loaded prompt (action_prompt + context) and drops into a chat where
// the work is already starting — never a paste-and-paraphrase.
async function launchComposedChat(card,prompt){
  state.starterLaunching=true;showToast(`${actionVerb(card)} with Callie — opening…`);
  try{
    await writeFloorIntakeHandoff({id:`floor-${Date.now()}-${Math.random().toString(36).slice(2,10)}`,launch_mode:'suggestion',prompt,created_at:Date.now(),agent_key:'calliope',team_mode:false,attachments:[],source_item_id:card.item_id,source_kind:card.item_kind});
    window.location.assign('/calliope?view=zen&start=1');
  }catch(err){console.error(err);state.starterLaunching=false;showToast('Could not open the chat. Please try again.')}
}
// A Playbook is reviewed, revised or copied in its own thread.
async function openFloorPlaybookHome(card){
  const playbookId=String(card.item_id||'').split(':')[1];
  if(!playbookId||state.starterLaunching)return;
  state.starterLaunching=true;showToast('Opening the playbook’s thread…');
  try{
    const data=await api(`/api/calliope/playbooks/${encodeURIComponent(playbookId)}/home`,{method:'POST',headers:{'content-type':'application/json'},body:'{}'});
    window.location.assign(data.url);
  }catch(err){console.error(err);state.starterLaunching=false;showToast('Could not open the playbook. Please try again.')}
}
async function launchPieceChat(card){
  if(!card||state.starterLaunching)return;
  if(card.item_kind==='input_request'){openDrawer(card.item_id);return}
  if(card.item_kind==='playbook'&&!playbookApproved(card)){return openFloorPlaybookHome(card)}
  if(card.item_kind==='curated_dream'){return beginFloorStarter({title:presentationTitle(card),id:card.item_id,sourceItemId:card.item_id,sourceKind:'curated_dream',prompt:`Explore the Dream “${presentationTitle(card)}” using its saved evidence.`,direction:document.getElementById('dreamDirection')?.value||'',action:{kind:'dream',id:String(card.item_id).split(':')[1]}})}
  const title=presentationTitle(card);
  const context=[card.state_reason,card.summary&&card.summary!==card.state_reason?card.summary:null].filter(Boolean).join(' — ');
  if(playbookApproved(card)){
    // The run auto-starts its chat, so this is the one moment to hand Callie
    // direction (scope, period, audience). Blank is a first-class answer —
    // plenty of SOPs carry their instructions in the contract itself.
    openDirectionModal({
      title,
      onSubmit:(direction)=>{
        const base=`Run my playbook “${title}” (${card.route||card.item_id}). Read its approved contract first, confirm any inputs or choices you need from me, then execute it and show the receipts as you go.`;
        return launchComposedChat(card,direction?`${base}\n\nMy direction for this run:\n${direction}\n\nFold that into how you execute the contract — it steers this run; it does not change the playbook.`:base);
      },
    });
    return;
  }
  const prompt=card.action_prompt
    ?`${card.action_prompt}\n\nThis concerns the piece “${title}” (${card.route||card.item_id})${context?` — ${context}`:''}. Gather the relevant evidence first, explain the situation plainly, then proceed.`
    :starterFromCard(card).prompt;
  await launchComposedChat(card,prompt);
}
// The async half of triage: hand the piece to Callie without joining. The
// item leaves Awaiting You immediately (the board never shows in-flight work
// as waiting on a human) and the born chat lands on the floor in motion.
const dreamEngaging=new Set();
async function engageDream(card,direction=''){
  if(!card||dreamEngaging.has(card.item_id))return;
  dreamEngaging.add(card.item_id);showToast('Handing this Dream to Callie…');
  const button=document.getElementById('asyncDreamEngage');if(button)button.disabled=true;
  try{
    const data=await api(`/api/calliope/dreams/${encodeURIComponent(String(card.item_id).split(':')[1])}/handoff`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({origin:'floor',background:true,direction})});
    if(!data.session?.id)throw new Error('The Dream notebook could not be started');
    closeDrawer();await reloadFloorPieces();showToast(data.already_engaged?'This Dream already has a thread — open it from the Floor':"Callie is exploring it — you can open the thread any time");
  }catch(error){showToast(error.message||'Could not start this Dream')}
  finally{dreamEngaging.delete(card.item_id);if(button?.isConnected)button.disabled=false}
}
async function engagePiece(card){
  if(!card)return;
  const itemId=String(card.item_id||'').split(':')[1];
  if(!itemId)return;
  showToast('Handing to Callie…');
  try{
    const data=await api(`/api/calliope/work-items/${encodeURIComponent(itemId)}/engage`,{method:'POST',headers:{'content-type':'application/json'},body:'{}'});
    card.column_state='in_motion';card.engaged_session_id=data.session?.id||card.engaged_session_id;card.session_id=data.session?.id||card.session_id;card.open_url=data.session?.open_url||card.open_url;card.active_now=true;
    const live=state.liveCards.find(x=>x.item_id===card.item_id);if(live&&live!==card)Object.assign(live,card);
    closeDrawer();render();showToast("Callie's on it — it'll land back on your floor");
  }catch(err){console.error(err);showToast('Could not hand this to Callie. Please try again.')}
}
// Set aside: not Done (no work happened) and never deleted — the piece moves
// to Archived carrying who set it aside, and stays findable. New evidence on
// the same subject arrives as a new piece, so nothing rots silently.
async function setAsidePiece(card){
  if(!card)return;
  if(card.item_kind==='curated_dream')return updateFloorDream(card,'dismiss');
  try{
    await api('/api/calliope/floor/pieces/archive',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({item_id:card.item_id})});
    // Refresh the server projection so lane counts, search windows, and the
    // replacement card all agree with the receipt we just committed.
    closeDrawer();await reloadFloorPieces();showToast('Set aside — findable in Archived, not marked done');
  }catch(err){console.error(err);showToast('Could not set this aside. Please try again.')}
}
async function restorePiece(card){
  if(!card)return;
  try{
    await api('/api/calliope/floor/pieces/archive',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({item_id:card.item_id,restore:true})});
    // The source may have moved since it was set aside; restore its current
    // state instead of inventing an Awaiting You state in the browser.
    closeDrawer();await reloadFloorPieces();showToast('Restored to the Floor');
  }catch(err){console.error(err);showToast('Could not restore this piece. Please try again.')}
}
function openDrawer(id){
  const c=state.cards.find(x=>x.item_id===id);if(!c)return;
  $('drawerTitle').textContent=presentationTitle(c);
  // Eyebrow in the setup-rail idiom: lane in amber, kind after it.
  const eyebrow=$('drawerEyebrow');if(eyebrow)eyebrow.innerHTML=`<b>${esc(LANE_LABEL[c.column_state]||c.column_state||'Piece')}</b> · ${esc(kindLabel(c.item_kind))}`;
  const people=[c.owner_email,...normalizeAgents(c.agents).map(displayAgent)].filter(Boolean).join(', ')||'—';
  const now=contextLine(c),failureHistory=c.item_kind==='duty_cycle'?`<div>Failure streak</div><div>${Number(c.streak)||0}</div><div>Failures · 14d</div><div>${Number(c.failures_14)||0}</div>`:'';
  // For chat pieces the summary is the whole-thread brief (0422); the status
  // line above it already carries the present moment, so the lede is the
  // thread's story rather than a second copy of the status.
  const lede=(c.item_kind==='chat'&&c.summary)?c.summary:(c.semantic_status||c.state_reason||c.summary||'No summary yet.');
  const chips=[
    c.dream_origin?'<span class="drawer-chip">From a Dream</span>':'',
    `<span class="drawer-chip lane lane-${esc(c.column_state)}">${esc(LANE_LABEL[c.column_state]||c.column_state)}</span>`,
    `<span class="drawer-chip">${esc(kindLabel(c.item_kind))}</span>`,
    `<span class="drawer-chip">${esc(age(c.idle_seconds))} ago</span>`,
    Number(c.surface_count)?`<span class="drawer-chip">${icon.layers}${Number(c.surface_count)} artifact${Number(c.surface_count)===1?'':'s'}</span>`:'',
    Number(c.related_signal_count)?`<span class="drawer-chip">${icon.signal}${Number(c.related_signal_count)} signal${Number(c.related_signal_count)===1?'':'s'}</span>`:'',
    c.item_kind==='duty_cycle'?`<span class="drawer-chip ${Number(c.streak)?'warn':''}">streak ${Number(c.streak)||0}</span>`:'',
  ].filter(Boolean).join('');
  $('drawerBody').innerHTML=`${now?`<div class="drawer-status">${esc(now)}</div>`:''}<p class="drawer-lede">${esc(lede)}</p><div class="drawer-chips">${chips}</div><div class="drawer-people">${avatars(c)}<span class="drawer-people-names">${esc(people)}</span></div>${c.action_prompt?`<div class="action-brief"><strong>${esc(actionVerb(c))}</strong>${esc(c.action_prompt)}</div>`:''}<div id="decisionPanel"></div><div id="pieceContext"></div><div class="drawer-route" title="Canonical route">${esc(c.route||c.item_id)}</div><div class="drawer-actions"><button class="primary" id="openPiece"${c.open_url||canLaunchPiece(c)?'':' disabled'}>${c.open_url?(c.item_kind==='page'?'Open Page':'Open piece'):c.item_kind==='decision'?'Discuss with Callie →':canLaunchPiece(c)?`${esc(actionVerb(c))} with Callie →`:c.item_kind==='duty_cycle'?'Runs headless · transcripts below':'No chat attached'}</button>${c.item_kind==='work_item'&&c.column_state==='awaiting_you'&&!c.engaged_session_id?'<button id="asyncEngage">Have Callie start without me</button>':''}${state.timeMode!=='history'&&c.column_state==='awaiting_you'&&!c.silhouette?'<button id="setAsidePiece" title="Not done, never deleted — stays findable in Archived">Set aside</button>':''}${state.timeMode!=='history'&&c.column_state==='archived'&&c.set_aside?'<button id="restorePiece">Restore piece</button>':''}</div>`;
  $('drawerBackdrop').classList.add('open');
  $('openPiece').addEventListener('click',()=>{if(c.open_url)window.location.assign(c.open_url);else if(canLaunchPiece(c))launchPieceChat(c);else showToast('This piece has no conversation attached yet')});
  document.getElementById('asyncEngage')?.addEventListener('click',()=>engagePiece(c));
  document.getElementById('setAsidePiece')?.addEventListener('click',()=>setAsidePiece(c));
  document.getElementById('restorePiece')?.addEventListener('click',()=>restorePiece(c));
  if(c.item_kind==='curated_dream')loadDreamPanel(c);
  if(c.item_kind==='decision')loadDecisionPanel(c);
  if(c.item_kind==='input_request')loadInputRequestPanel(c);
  if(c.item_kind==='playbook')loadPlaybookPanel(c);
  if(c.session_id&&c.item_kind!=='curated_dream')loadPieceContext(c);
  if(c.item_kind==='duty_cycle')loadDutyPanel(c);
  else if(c.item_kind==='chat'&&c.standing_job_id)loadDutyPanel(c,{profile:'default',job:String(c.standing_job_id),steerViaThread:true});
}
// Schedule modal — standalone on purpose so a future kanban drag can call
// openScheduleModal() with the same contract. Presets compile to five-field
// cron; the mission is the runtime intent ("run for today, send it here"),
// which the playbook itself deliberately never carries.
const SCHEDULE_PRESETS=[
  {key:'daily',label:'Every day',cron:(h,m)=>`${m} ${h} * * *`,echo:(t)=>`every day at ${t}`},
  {key:'weekdays',label:'Weekdays',cron:(h,m)=>`${m} ${h} * * 1-5`,echo:(t)=>`weekdays at ${t}`},
  {key:'weekly',label:'Weekly',cron:(h,m,d)=>`${m} ${h} * * ${d}`,echo:(t,dn)=>`every ${dn} at ${t}`},
  {key:'monthly',label:'Monthly',cron:(h,m,d,date)=>`${m} ${h} ${date} * *`,echo:(t,dn,date)=>`on day ${date} of each month at ${t}`},
  {key:'custom',label:'Custom cron',cron:null,echo:null},
];
const WEEKDAY_NAMES=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
function scheduleModalState(){
  const host=document.getElementById('floorScheduleModal');if(!host)return null;
  const preset=host.querySelector('.sched-preset.on')?.dataset.preset||'daily';
  const [h,m]=(host.querySelector('#schedTime').value||'07:00').split(':').map(Number);
  const dayRaw=host.querySelector('#schedDay').value;
  const day=/^\d$/.test(dayRaw)?Number(dayRaw):1; // 0 = Sunday is a valid pick — no falsy-zero fallback

  const custom=host.querySelector('#schedCron').value.trim();
  const spec=SCHEDULE_PRESETS.find(p=>p.key===preset);
  const monthDay=Number(host.querySelector('#schedMonthDay').value)||1;
  const validTime=preset==='custom'||/^\d{2}:\d{2}$/.test(host.querySelector('#schedTime').value);
  const cron=preset==='custom'?custom:spec.cron(h,m,day,monthDay);
  const t=new Date(2000,0,1,h,m).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
  const echo=preset==='custom'?(custom?`on the cron schedule “${custom}”`:''):spec.echo(t,WEEKDAY_NAMES[day],monthDay);
  return {preset,cron,echo,monthDay,validTime,mission:host.querySelector('#schedMission').value.trim()};
}
function renderScheduleEcho(){
  const s=scheduleModalState();if(!s)return;
  const echoEl=document.querySelector('#schedEcho');
  echoEl.textContent=!s.validTime?'Choose a time.':s.echo?`Runs ${s.echo}, in the appliance's local time.${s.preset==='monthly'&&s.monthDay>28?' Months without this date are skipped.':''}`:'Enter a five-field cron expression.';
  document.querySelector('#schedDayWrap').hidden=s.preset!=='weekly';
  document.querySelector('#schedTimeWrap').hidden=s.preset==='custom';
  document.querySelector('#schedMonthDayWrap').hidden=s.preset!=='monthly';
  document.querySelector('#schedCronWrap').hidden=s.preset!=='custom';
}
// Runtime instructions use the same editor as Floor intake, without discovery,
// implicit object hints, agent switching, uploads, or design controls.
function floorRunInputMarkup(id,label,placeholder,value=''){
  return `<div class="sched-mission-label"><label for="${id}">${label}</label>
    <div class="sched-composer"><textarea id="${id}" rows="6" maxlength="4000" placeholder="${esc(placeholder)}">${esc(value)}</textarea><div id="${id}Editor" class="floor-intake-editor" hidden></div></div>
    <div class="sched-input-hint">Type <b>@</b> for agents and people, or <b>[[</b> for company objects.</div></div>`;
}
function mountFloorRunDialog(host,inputId,submit){
  const previousFocus=document.activeElement,input=host.querySelector(`#${inputId}`);
  document.querySelectorAll('.sched-backdrop').forEach(other=>other._closeRunDialog?.());
  document.body.appendChild(host);
  let editor=null,closed=false;
  const close=()=>{if(closed)return;closed=true;editor?.destroy();host.remove();if(previousFocus?.isConnected)previousFocus.focus()};
  host._closeRunDialog=close;
  host.addEventListener('click',e=>{if(e.target===host)close()});
  host.querySelectorAll('[data-run-close]').forEach(button=>button.addEventListener('click',close));
  host.addEventListener('keydown',e=>{
    if(e.defaultPrevented)return;
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();return}
    if(e.key==='Tab'&&!host.querySelector('.cm-tooltip-autocomplete')){
      const focusable=[...host.querySelectorAll('button,input,select,textarea,[tabindex="0"],[contenteditable="true"]')].filter(el=>!el.disabled&&el.getClientRects().length);
      const first=focusable[0],last=focusable.at(-1);
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}
    }
  });
  if(window.CalliopeObjectEditor?.mount){
    const target=host.querySelector(`#${inputId}Editor`);
    editor=window.CalliopeObjectEditor.mount(target,{
      variant:'composer',value:input.value,placeholder:input.placeholder,
      ariaLabel:input.labels?.[0]?.textContent||'Run instructions',maxLength:4000,submitOnEnter:false,
      lookup:lookupFloorIntakeObjects,
      agents:async()=>{await loadAgentRoster();const agents=floorChatAgents().filter(a=>!a.primary&&a.key!=='calliope');return agents.length?[{key:'all',name:'all',description:'Invite every active specialist except Calliope.',fanout:true},...agents]:[]},
      onChange:value=>{input.value=value},onSubmit:submit,
    });
    input.hidden=true;target.hidden=false;
  }else input.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='Enter'){e.preventDefault();submit()}});
  requestAnimationFrame(()=>{if(!closed)(editor||input).focus()});
  return {close,getValue:()=>editor?.getValue?.()??input.value,setDisabled:value=>{editor?.setDisabled(value);input.disabled=value}};
}
function openDirectionModal({title,onSubmit}){
  const host=document.createElement('div');host.id='floorDirectionModal';host.className='sched-backdrop';
  host.innerHTML=`<div class="sched-modal" role="dialog" aria-modal="true" aria-labelledby="dirTitle">
    <div class="sched-head"><div><div class="sched-eyebrow">Playbook · one run</div><h2 id="dirTitle">Run “${esc(title)}”</h2></div><button type="button" class="close" id="dirClose" data-run-close aria-label="Close">×</button></div>
    ${floorRunInputMarkup('dirText','Direction for this run — optional','Scope, period, audience, where to send it… Leave blank to follow the playbook as written.')}
    <div class="sched-actions"><span class="sched-shortcut">Ctrl / ⌘ + Enter to run</span><button type="button" id="dirCancel" data-run-close>Cancel</button><button type="button" class="primary" id="dirSubmit">Run with Callie</button></div>
  </div>`;
  let submitting=false;
  const submit=async()=>{
    if(submitting)return;submitting=true;
    const btn=host.querySelector('#dirSubmit');btn.disabled=true;btn.textContent='Opening…';dialog.setDisabled(true);
    try{await onSubmit(dialog.getValue().trim());dialog.close()}
    catch(err){console.error(err);submitting=false;dialog.setDisabled(false);btn.disabled=false;btn.textContent='Run with Callie';showToast('Could not open the chat. Please try again.')}
  };
  const dialog=mountFloorRunDialog(host,'dirText',submit);
  host.querySelector('#dirSubmit').addEventListener('click',submit);
}
function openScheduleModal({title,submitLabel='Start the schedule',defaultMission,onSubmit}){
  const host=document.createElement('div');host.id='floorScheduleModal';host.className='sched-backdrop';
  host.innerHTML=`<div class="sched-modal" role="dialog" aria-modal="true" aria-labelledby="schedTitle">
    <div class="sched-head"><div><div class="sched-eyebrow">Playbook · recurring run</div><h2 id="schedTitle">Schedule “${esc(title)}”</h2></div><button type="button" class="close" id="schedClose" data-run-close aria-label="Close">×</button></div>
    <fieldset class="sched-cadence"><legend>When should it run?</legend>
      <div class="sched-controls"><div class="sched-presets" role="group" aria-label="Repeat frequency">${SCHEDULE_PRESETS.map((p,i)=>`<button type="button" class="sched-preset${i===0?' on':''}" aria-pressed="${i===0}" data-preset="${p.key}">${p.label}</button>`).join('')}</div>
      <div class="sched-row"><label id="schedTimeWrap">At <input type="time" id="schedTime" value="07:00"></label>
        <label id="schedDayWrap" hidden>On <select id="schedDay">${WEEKDAY_NAMES.map((d,i)=>`<option value="${i}"${i===1?' selected':''}>${d}</option>`).join('')}</select></label>
        <label id="schedMonthDayWrap" hidden>Day <select id="schedMonthDay" aria-label="Day of the month">${Array.from({length:31},(_,i)=>`<option value="${i+1}">${i+1}</option>`).join('')}</select></label>
        <label id="schedCronWrap" hidden>Cron <input type="text" id="schedCron" aria-label="Cron expression" placeholder="0 7 * * 1-5" spellcheck="false"></label></div></div>
      <div class="sched-echo" id="schedEcho" aria-live="polite"></div>
    </fieldset>
    ${floorRunInputMarkup('schedMission','Instructions for each run','What should Callie do each time? Include the scope, audience, and where results belong.',defaultMission||'')}
    <div class="sched-actions"><span class="sched-shortcut">Ctrl / ⌘ + Enter to schedule</span><button type="button" id="schedCancel" data-run-close>Cancel</button><button type="button" class="primary" id="schedSubmit">${esc(submitLabel)}</button></div>
  </div>`;
  let submitting=false;
  const submit=async()=>{
    if(submitting)return;
    const s=scheduleModalState();
    if(!s.validTime||!s.cron||s.cron.split(/\s+/).length!==5){showToast('Choose a time or enter a five-field cron expression.',true);return}
    submitting=true;const btn=host.querySelector('#schedSubmit');btn.disabled=true;btn.textContent='Scheduling…';dialog.setDisabled(true);
    try{await onSubmit(s);dialog.close()}catch(err){console.error(err);submitting=false;dialog.setDisabled(false);btn.disabled=false;btn.textContent=submitLabel;showToast(err.message||'Could not schedule. Please try again.')}
  };
  const dialog=mountFloorRunDialog(host,'schedMission',submit);
  host.querySelectorAll('.sched-preset').forEach(b=>b.addEventListener('click',()=>{host.querySelectorAll('.sched-preset').forEach(x=>{x.classList.toggle('on',x===b);x.setAttribute('aria-pressed',String(x===b))});renderScheduleEcho()}));
  ['#schedTime','#schedDay','#schedMonthDay','#schedCron'].forEach(sel=>host.querySelector(sel).addEventListener('input',renderScheduleEcho));
  host.querySelector('#schedSubmit').addEventListener('click',submit);
  renderScheduleEcho();
}
function playbookContractMarkup(contract={}){
  const fields=[['outcome','Outcome'],['deliverable','Deliverable'],['when_to_use','When to use'],['triggers','Triggers'],['when_not_to_use','When not to use'],['context_to_gather','Context to gather'],['method','Method'],['guardrails','Guardrails'],['completion_criteria','Completion criteria'],['fallbacks','Fallbacks'],['required_capabilities','Required capabilities'],['preferred_capabilities','Preferred capabilities'],['optional_capabilities','Optional capabilities']];
  return fields.map(([key,label])=>{
    const value=contract?.[key];
    if(Array.isArray(value)){
      const items=value.filter(item=>typeof item==='string'&&item.trim());if(!items.length)return '';
      const tag=key==='method'?'ol':'ul';
      return `<section><h4>${esc(label)}</h4><${tag}>${items.map(item=>`<li>${esc(item)}</li>`).join('')}</${tag}></section>`;
    }
    return typeof value==='string'&&value.trim()?`<section><h4>${esc(label)}</h4><p>${esc(value)}</p></section>`:'';
  }).join('');
}
// Written methods are always readable; a pinned visual plan is optional.
async function loadPlaybookPanel(card){
  const host=document.getElementById('pieceContext');if(!host)return;
  const playbookId=String(card.item_id||'').split(':')[1];if(!playbookId)return;
  host.innerHTML='<div class="duty-loading">Reading the playbook…</div>';
  try{
    const data=await api(`/api/calliope/playbooks/${encodeURIComponent(playbookId)}`);
    const pb=data.playbook||data||{};
    const manages=data.can_manage===true||data.is_owner===true;
    const approvable=manages&&!pb.approved&&!pb.archived&&Number(pb.version)===Number(pb.latest_version||pb.version);
    const version=pb.version||pb.approved_version||null;
    const synopsis=String(pb.synopsis||'').trim();
    const approvedBy=String(pb.approved_by||'').trim();
    const approvedAt=pb.approved_at?new Date(pb.approved_at):null;
    const approvedLine=pb.approved||approvedBy?`Approved${version?` v${version}`:''}${approvedBy?` by ${displayAgent(approvedBy)}`:''}${approvedAt&&!Number.isNaN(approvedAt.getTime())?` · ${approvedAt.toLocaleDateString([],{month:'short',day:'numeric'})}`:''}`:'';
    const sourceSession=pb.source_session_id||card.session_id||null;
    const contract=playbookContractMarkup(pb.contract);
    const doors=[
      pb.has_sketch?`<a class="drawer-made" href="/calliope/playbooks/${encodeURIComponent(playbookId)}/sketch?version=${encodeURIComponent(version||1)}" target="_blank" rel="noopener"><span class="drawer-made-kind">visual plan</span><span class="drawer-made-title">View the pinned sketch</span></a>`:'',
      sourceSession?`<a class="drawer-made is-static" href="/calliope?session=${encodeURIComponent(sourceSession)}&view=zen"><span class="drawer-made-kind">lineage</span><span class="drawer-made-title">The conversation it was born in</span></a>`:'',
      approvable?`<button type="button" class="drawer-made is-static" id="playbookApprove"><span class="drawer-made-kind">review</span><span class="drawer-made-title">Approve v${esc(String(pb.version))} — make it the live version</span></button>`:'',
      // Revising happens in the playbook's own thread; anyone else gets a copy
      // of their own (with its own thread), and the original stays untouched.
      `<button type="button" class="drawer-made is-static" id="playbookRefine"><span class="drawer-made-kind">${manages?'revise':'adapt'}</span><span class="drawer-made-title">${manages?'Open its thread — revise it with Callie':'Make my own copy with Callie'}</span></button>`,
      pb.approved||pb.approved_version?`<button type="button" class="drawer-made is-static" id="playbookSchedule"><span class="drawer-made-kind">standing</span><span class="drawer-made-title">Schedule — run it on a heartbeat</span></button>`:'',
    ].filter(Boolean).join('');
    host.innerHTML=`${synopsis?`<div class="duty-section-title">What this playbook does</div><p class="drawer-lede">${esc(synopsis)}</p>`:''}${approvedLine?`<div class="drawer-status">${esc(approvedLine)}</div>`:''}${contract?`<details class="drawer-playbook-contract"><summary>Read the full playbook${version?` · v${esc(String(version))}`:''}</summary>${contract}</details>`:''}<div class="duty-section-title">Open</div><div class="drawer-made-strip">${doors}</div>`;
    document.getElementById('playbookSchedule')?.addEventListener('click',()=>{
      openScheduleModal({
        title:String(pb.title||presentationTitle(card)),
        defaultMission:'Run this playbook for the current period and post the results to this thread. Flag anything that needs my attention.',
        onSubmit:async(s)=>{
          const data=await api(`/api/calliope/playbooks/${encodeURIComponent(playbookId)}/schedule`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({schedule:s.cron,mission:s.mission})});
          showToast(`Scheduled — runs ${s.echo}. The standing thread is on your Floor.`);
          document.getElementById('drawerBackdrop')?.classList.remove('open');
          load(true).catch(()=>{});
          return data;
        },
      });
    });
    document.getElementById('playbookRefine')?.addEventListener('click',()=>openFloorPlaybookHome(card));
    document.getElementById('playbookApprove')?.addEventListener('click',async(event)=>{
      const button=event.currentTarget;button.disabled=true;
      try{
        await api(`/api/calliope/playbooks/${encodeURIComponent(playbookId)}/approve`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({version:Number(pb.version)})});
        showToast(`Approved ${pb.title||'the playbook'} · v${pb.version}`);
        document.getElementById('drawerBackdrop')?.classList.remove('open');
        load(true).catch(()=>{});
      }catch(err){console.error(err);button.disabled=false;showToast(err.message||'Could not approve the playbook.')}
    });
  }catch(err){console.error(err);host.innerHTML='';}
}
// Flesh for chat-backed pieces: the latest human/assistant exchange and the
// artifacts born in the session — recognition at a glance, receipts a click
// away. The pre-generated presentation stays the lede; the raw transcript
// excerpt lives down here where it belongs.
async function updateFloorDream(card,action){
  try{
    await api(`/api/calliope/dreams/${encodeURIComponent(String(card.item_id).split(':')[1])}`,{method:'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify({action,days:7})});
    closeDrawer();await load(true);showToast(action==='sleep'?'Dream snoozed for a week':'Dream dismissed');
  }catch(error){showToast(error.message||'Could not update this Dream')}
}
async function loadDreamPanel(card){
  const host=document.getElementById('pieceContext');if(!host)return;
  try{
    const data=await api(`/api/calliope/dreams/${encodeURIComponent(String(card.item_id).split(':')[1])}`);
    if(!host.isConnected)return;
    const dream=data.dream||{},output=dream.output||{};
    const sections=Array.isArray(output.sections)?output.sections:[];
    const phases=Array.isArray(output.phases)?output.phases:[];
    const evidence=Array.isArray(dream.evidence)?dream.evidence:[];
    host.innerHTML=`<div class="dream-detail">${dreamDetailMeta(dream)}${output.summary?`<p>${esc(output.summary)}</p>`:''}${sections.map(x=>`<section><h3>${esc(x.title||'Idea')}</h3><p>${esc(x.content||'')}</p></section>`).join('')}${phases.map(x=>`<section><h3>${esc(x.name||'Proposed step')}</h3><p>${esc(x.outcome||'')}</p></section>`).join('')}${dream.why_now?`<section><h3>Why now</h3><p>${esc(dream.why_now)}</p></section>`:''}${evidence.length?`<details><summary>Evidence · ${evidence.length}</summary>${evidence.map(x=>`<p>${esc(typeof x==='string'?x:(x.summary||x.observation||x.title||x.label||JSON.stringify(x)))}</p>`).join('')}</details>`:''}</div>`;
    if(state.timeMode!=='history'){
      const actions=document.querySelector('#drawerBody .drawer-actions');
      if(!card.session_id&&isMine(card)){
        const direction=document.createElement('label');direction.className='dream-direction';direction.textContent='Anything Callie should know?';
        const input=document.createElement('textarea');input.id='dreamDirection';input.rows=2;input.maxLength=4000;input.placeholder='Optional direction for this exploration…';direction.append(input);actions?.before(direction);
        const button=document.createElement('button');button.id='asyncDreamEngage';button.textContent='Have Callie start without me';button.disabled=dreamEngaging.has(card.item_id);button.addEventListener('click',()=>engageDream(card,input.value));actions?.append(button);
      }
      for(const [label,action] of [['Snooze a week','sleep'],['Dismiss Dream','dismiss']]){
        const button=document.createElement('button');button.className='dream-feedback';button.textContent=label;button.addEventListener('click',()=>updateFloorDream(card,action));actions?.append(button);
      }
    }
  }catch(error){if(host.isConnected)host.textContent=error.message||'Could not load this Dream'}
}
async function loadPieceContext(card){
  const host=document.getElementById('pieceContext');if(!host)return;
  try{
    const data=await api(`/api/calliope/floor/piece-context?session=${encodeURIComponent(card.session_id)}`);
    const turns=(data.turns||[]).filter(t=>String(t.user_message||'').trim()||String(t.assistant_message||'').trim()).slice(-2);
    const bubbles=turns.map(t=>{
      const user=String(t.user_message||'').trim(),reply=String(t.assistant_message||'').trim();
      return `${user&&!user.startsWith('[')?`<div class="drawer-bubble is-human"><span>${esc(user)}</span></div>`:''}${reply?`<div class="drawer-bubble is-callie"><span>${esc(reply)}</span></div>`:''}`;
    }).join('');
    const made=(data.surfaces||[]).slice(0,6).map(s=>`<a class="drawer-made ${s.open_url?'':'is-static'}" ${s.open_url?`href="${esc(s.open_url)}" target="_blank" rel="noopener"`:''}>${s.thumbnail_url?`<img data-floor-starter-thumb src="${esc(s.thumbnail_url)}" alt="" loading="lazy" decoding="async">`:''}<span class="drawer-made-kind">${esc(String(s.kind||''))}</span><span class="drawer-made-title">${esc(s.title)}</span></a>`).join('');
    host.innerHTML=`${bubbles?`<div class="duty-section-title">Latest exchange</div><div class="drawer-exchange">${bubbles}</div>`:''}${made?`<div class="duty-section-title">Made here</div><div class="drawer-made-strip">${made}</div>`:''}`;
  }catch(err){console.error(err)}
}
// Decisions get the structured path: the options Callie already enumerated,
// recommendation pre-highlighted, one tap to answer with an optional reason.
// The chat launch stays available as "Discuss" for the ones that need talk.
async function loadDecisionPanel(card){
  const host=document.getElementById('decisionPanel');if(!host)return;
  const decisionId=String(card.item_id||'').split(':')[1];if(!decisionId)return;
  if(card.column_state!=='awaiting_you'){return}
  host.innerHTML='<div class="duty-loading">Loading choices…</div>';
  try{
    const data=await api('/api/calliope/decisions');
    const rows=data.decisions||data.open||data.items||[];
    const decision=rows.find(d=>String(d.id)===decisionId);
    if(!decision||String(decision.status||'open')!=='open'){host.innerHTML='<div class="duty-loading">This decision is no longer open.</div>';return}
    const options=Array.isArray(decision.options)?decision.options:[];
    const rec=String(decision.recommended_option||'');
    if(!options.length){host.innerHTML='';return}
    host.innerHTML=`<div class="duty-section-title">Decide now</div><div class="decision-options">${options.map(o=>{const key=String(o.key||'');return `<button type="button" class="decision-option ${key===rec?'is-recommended':''}" data-decision-option="${esc(key)}"><strong>${esc(o.label||key)}</strong>${key===rec?'<span class="decision-rec">recommended</span>':''}${o.summary?`<span class="decision-summary">${esc(o.summary)}</span>`:''}</button>`}).join('')}</div><div class="duty-note-compose"><input type="text" data-decision-note maxlength="2000" placeholder="Add a reason (optional)…"></div>`;
    host.querySelectorAll('[data-decision-option]').forEach(button=>button.addEventListener('click',async()=>{
      const note=host.querySelector('[data-decision-note]')?.value?.trim()||'';
      host.querySelectorAll('[data-decision-option]').forEach(b=>{b.disabled=true});
      try{
        await api(`/api/calliope/decisions/${encodeURIComponent(decisionId)}/answer`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({selected_option:button.dataset.decisionOption,note,revision:decision.revision})});
        card.column_state='done';card.detail_state='answered';
        const live=state.liveCards.find(x=>x.item_id===card.item_id);if(live&&live!==card)Object.assign(live,card);
        closeDrawer();render();showToast(`Decided: ${button.querySelector('strong')?.textContent||button.dataset.decisionOption}`);
      }catch(err){console.error(err);host.querySelectorAll('[data-decision-option]').forEach(b=>{b.disabled=false});showToast(err?.message||'That choice could not be recorded. It may have changed — reopen to see the latest.')}
    }));
  }catch(err){console.error(err);host.innerHTML='<div class="duty-loading">Choices unavailable right now.</div>'}
}
// Ordinary questions share the server-owned lifecycle with the chat dock.
async function loadInputRequestPanel(card){
  const host=document.getElementById('decisionPanel');if(!host)return;
  const id=String(card.item_id||'').split(':')[1];if(!id)return;
  if(state.timeMode==='history'){host.textContent='Historical question · return to Live to answer.';return}
  host.textContent='Loading question…';
  try{
    const question=await api(`/api/calliope/input-requests/${encodeURIComponent(id)}`);
    if(!host.isConnected)return;
    const fields=question.request.fields||[];
    if(!question.can_answer){
      host.innerHTML=`<p>${question.status==='open'?'Continue in the original conversation to answer.':esc(question.status==='answered'?'Answered':'Cancelled')}</p>${fields.filter(f=>Object.hasOwn(question.answers||{},f.key)).map(f=>`<p><strong>${esc(f.label)}</strong>: ${esc(String(question.answers[f.key]))}</p>`).join('')}`;
      return;
    }
    const fieldMarkup=f=>{
      const key=esc(f.key),label=esc(f.label),required=f.required?'required':'';
      const options=f.options||[];
      if(f.type==='choice')return `<fieldset><legend>${label}</legend><div class="input-question-choices">${options.map(o=>`<label><input type="radio" name="${key}" value="${esc(o.value)}" ${required}><span>${esc(o.label)}</span></label>`).join('')}</div></fieldset>`;
      if(f.type==='boolean')return `<label class="input-question-boolean"><input type="checkbox" name="${key}"><span>${label}</span></label>`;
      if(f.type==='select')return `<label><span>${label}</span><select name="${key}" ${required}><option value="">Choose…</option>${options.map(o=>`<option value="${esc(o.value)}">${esc(o.label)}</option>`).join('')}</select></label>`;
      return `<label><span>${label}</span>${f.type==='textarea'?`<textarea name="${key}" maxlength="4000" ${required}></textarea>`:`<input type="text" name="${key}" maxlength="4000" ${required}>`}</label>`;
    };
    host.innerHTML=`<form class="floor-input-question"><div class="duty-section-title">Answer here</div><p>${esc(question.request.prompt||'')}</p>${fields.map(fieldMarkup).join('')}<div class="drawer-actions"><button type="submit" class="primary">${esc(question.request.submit_label||'Continue')} →</button><button type="submit" data-input-cancel formnovalidate>Cancel question</button></div><p role="status" data-input-status></p></form>`;
    const form=host.querySelector('form');
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(form.dataset.pending)return;
      const cancel=event.submitter?.hasAttribute('data-input-cancel');
      const answers={};
      for(const field of form.elements){if(!field.name||(field.type==='radio'&&!field.checked))continue;answers[field.name]=field.type==='checkbox'?field.checked:String(field.value||'').trim()}
      form.dataset.pending='true';form.querySelectorAll('button').forEach(b=>b.disabled=true);
      try{
        const receipt=await api(`/api/calliope/input-requests/${encodeURIComponent(id)}/${cancel?'cancel':'answer'}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({answers,revision:question.revision})});
        card.column_state='done';card.detail_state=receipt.status;
        const live=state.liveCards.find(x=>x.item_id===card.item_id);if(live&&live!==card)Object.assign(live,card);
        closeDrawer();render();showToast(cancel?'Question cancelled':'Answer saved · the conversation will continue when ready');
      }catch(error){form.querySelector('[data-input-status]').textContent=error?.message||'Could not save your answer. Please try again.'}
      finally{delete form.dataset.pending;form.querySelectorAll('button').forEach(b=>b.disabled=false)}
    });
  }catch(error){if(host.isConnected)host.textContent=error?.message||'Question unavailable right now.'}
}
// Standing pieces: run history (rung 1 — the transcripts always existed in
// the hermes mirror; this is the edge pointing at them) and operator notes
// (rung 2 — "leave a note for the night shift").
function dutyRoute(card){const parts=String(card.route||card.item_id||'').split(':');return parts.length>=3&&parts[0]==='duty_cycle'?{profile:parts[1],job:parts.slice(2).join(':')}:null}
async function loadDutyPanel(card,override){
  const route=override||dutyRoute(card);if(!route)return;
  const host=document.createElement('div');host.className='duty-panel';host.innerHTML='<div class="duty-loading">Loading run history…</div>';
  $('drawerBody').appendChild(host);
  try{
    const data=await api(`/api/calliope/floor/duty-runs?profile=${encodeURIComponent(route.profile)}&job=${encodeURIComponent(route.job)}`);
    if(override?.steerViaThread)data.steerViaThread=true;
    host.innerHTML=dutyPanelMarkup(card,data);
    host.querySelectorAll('[data-duty-transcript]').forEach(b=>b.addEventListener('click',()=>toggleDutyTranscript(host,b.dataset.dutyTranscript)));
    host.querySelector('[data-duty-note-send]')?.addEventListener('click',async()=>{
      const input=host.querySelector('[data-duty-note-input]'),note=(input?.value||'').trim();
      if(!note)return;
      try{
        await api('/api/calliope/floor/duty-notes',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({profile_key:route.profile,job_id:route.job,job_name:presentationTitle(card),note})});
        input.value='';showToast('Noted — the next run reads it before starting');
        const fresh=await api(`/api/calliope/floor/duty-runs?profile=${encodeURIComponent(route.profile)}&job=${encodeURIComponent(route.job)}`);
        host.innerHTML=dutyPanelMarkup(card,fresh);
        host.querySelectorAll('[data-duty-transcript]').forEach(b=>b.addEventListener('click',()=>toggleDutyTranscript(host,b.dataset.dutyTranscript)));
      }catch(err){console.error(err);showToast('The note could not be saved. Please try again.')}
    });
  }catch(err){console.error(err);host.innerHTML='<div class="duty-loading">Run history is unavailable right now.</div>'}
}
function dutyRunLine(run){
  const when=run.started_at?new Date(run.started_at).toLocaleString([],{dateStyle:'medium',timeStyle:'short'}):'—';
  const calls=run.api_calls?`${run.api_calls} calls`:'';
  const status=String(run.status||'unknown');
  return `<div class="duty-run"><span class="duty-status is-${esc(status)}">${esc(status)}</span><span class="duty-when">${esc(when)}</span><span class="duty-meta">${esc(calls)}</span>${run.hermes_session_id?`<button type="button" class="duty-open" data-duty-transcript="${esc(run.hermes_session_id)}">transcript</button>`:''}</div>`;
}
function dutyPanelMarkup(card,data){
  const runs=(data.runs||[]).map(dutyRunLine).join('')||'<div class="duty-loading">No recorded runs yet.</div>';
  if(data.steerViaThread){
    return `<div class="duty-section-title">Recent runs</div>${runs}<div id="dutyTranscript"></div><div class="duty-loading">Steer this heartbeat by replying in the thread — each run reads the recent messages before working.</div>`;
  }
  const notes=(data.notes||[]).map(n=>`<div class="duty-note ${n.consumed_at?'is-consumed':''}"><span>${esc(n.note)}</span><small>${esc((n.author_email||'').split('@')[0])} · ${n.consumed_at?'read by the run':'waiting for next run'}</small></div>`).join('');
  return `<div class="duty-section-title">Recent runs</div>${runs}<div id="dutyTranscript"></div><div class="duty-section-title">Notes for the next run</div>${notes}<div class="duty-note-compose"><input type="text" data-duty-note-input maxlength="2000" placeholder="Tell tonight's run something…"><button type="button" data-duty-note-send>Leave note</button></div>`;
}
async function toggleDutyTranscript(host,sessionId){
  const box=host.querySelector('#dutyTranscript');
  if(box.dataset.open===sessionId){box.innerHTML='';box.dataset.open='';return}
  box.dataset.open=sessionId;box.innerHTML='<div class="duty-loading">Opening transcript…</div>';
  try{
    const data=await api(`/api/calliope/hermes-transcript/${encodeURIComponent(sessionId)}`);
    const rows=(data.messages||[]).filter(m=>['user','assistant_text','tool_call','mcp_result','tool_result'].includes(String(m.kind)))
      .map(m=>{
        const who=m.kind==='user'?'run prompt':m.kind==='assistant_text'?'assistant':`${m.kind==='tool_call'?'call':'result'}${m.tool_name?` · ${m.tool_name}`:(Array.isArray(m.tool_call_names)&&m.tool_call_names.length?` · ${m.tool_call_names.join(', ')}`:'')}`;
        const body=String(m.content||'').trim();
        return `<div class="duty-msg kind-${esc(String(m.kind))}"><div class="duty-msg-who">${esc(who)}${m.content_chars>2000?` <em>(${m.content_chars} chars, truncated)</em>`:''}</div>${body?`<pre>${esc(body)}</pre>`:''}</div>`;
      }).join('');
    box.innerHTML=`<div class="duty-transcript">${rows||'<div class="duty-loading">Empty transcript.</div>'}${data.truncated?'<div class="duty-loading">Transcript truncated at 500 rows.</div>':''}</div>`;
  }catch(err){console.error(err);box.innerHTML='<div class="duty-loading">The transcript could not be opened.</div>'}
}
function closeDrawer(){$('drawerBackdrop').classList.remove('open')}
function showToast(msg){const t=$('toast');t.textContent=msg;t.classList.add('show');clearTimeout(showToast.timer);showToast.timer=setTimeout(()=>t.classList.remove('show'),2600)}
function floorChatAgents(){const agents=state.agentRoster.filter(agent=>agent?.key&&agent?.name);return agents.length?agents:[{key:'calliope',name:'Calliope',role_label:'Warehouse muse',description:'Warehouse muse',primary:true,avatar_day_url:'/calliope/callie-avatar-day.jpg',avatar_night_url:'/calliope/callie-avatar-night.jpg'}]}
function floorChatAgent(key=state.intakeAgentKey){return floorChatAgents().find(agent=>agent.key===key)||floorChatAgents()[0]}
function floorAgentRole(agent){return String(agent?.role_label||(agent?.key==='calliope'?'Warehouse muse':'Specialist'))}
function floorAgentImage(agent){if(!agent)return null;if(agent.key==='calliope'){const day=new Date().getHours()>=7&&new Date().getHours()<19;return safeImageUrl(day?agent.avatar_day_url:agent.avatar_night_url)||safeImageUrl(agent.avatar_day_url)||'/calliope/callie-avatar-day.jpg'}return safeImageUrl(agent.avatar_url)}
function syncFloorLaunchAvatar(){const frame=$('floorIntakeOpenAvatar'),image=frame?.querySelector('img[data-floor-launch-avatar]');if(!frame||!image)return;const hour=new Date().getHours(),period=hour>=7&&hour<19?'day':'night',source=safeImageUrl(frame.dataset[`${period}Src`]);frame.dataset.period=period;if(source)image.src=source}
function floorIntakePlaceholder(){return `Ask ${floorChatAgent()?.name||'Calliope'} to explore, compare, or make something…`}
let floorSpeechConfig = null;
const floorDictation = window.CalliopeDictation.create({
  config: () => floorSpeechConfig,
  onChange: speech => {
    const active = speech.phase !== 'idle', recording = speech.phase === 'recording';
    const button = $('floorIntakeDictate'), preview = $('floorDictationPreview');
    button.disabled = active && !recording;
    button.textContent = recording ? 'Stop dictation' : active ? 'Transcribing…' : 'Dictate';
    button.setAttribute('aria-pressed', String(recording));
    $('floorIntakeDictateCancel').hidden = !active;
    preview.hidden = !active;
    preview.textContent = speech.liveTranscript || (speech.realtimeFailed
      ? 'Recording · text will appear when you stop' : recording ? 'Listening…' : 'Connecting…');
    syncFloorIntake();
  },
  onMessage: message => { $('floorIntakeStatus').textContent = message; },
  onTranscript: (target, text) => {
    if (!document.body.classList.contains('floor-intake-open')) return;
    if (state.intakeEditor?.insertText) {
      if (!state.intakeEditor.insertText(text, target.selection)) throw new Error('The draft is too long.');
    } else {
      const input = $('floorIntakeInput'), value = input.value;
      const from = Math.min(target.selection.from, value.length), to = Math.min(target.selection.to, value.length);
      input.setRangeText(window.CalliopeDictation.insertion(value, from, to, text), from, to, 'end');
      input.dispatchEvent(new Event('input', {bubbles:true}));
    }
    syncFloorIntake(); scheduleFloorIntakeDiscovery(); focusFloorIntake();
  },
});
async function loadFloorSpeech() {
  try { floorSpeechConfig = await api('/api/calliope/speech'); }
  catch { floorSpeechConfig = null; }
  $('floorIntakeDictate').hidden = !floorDictation.supported();
}
$('floorIntakeDictate').addEventListener('click', () => {
  if (floorDictation.state.phase === 'recording') { floorDictation.stop(); return; }
  const input = $('floorIntakeInput');
  floorDictation.start({kind:'floor', selection:state.intakeEditor?.getSelection?.()
    || {from:input.selectionStart, to:input.selectionEnd}});
});
$('floorIntakeDictateCancel').addEventListener('click', () => floorDictation.cancel());
window.addEventListener('pagehide', () => floorDictation.cancel());
function floorIntakeValue(){return state.intakeEditor?.getValue?.()??$('floorIntakeInput').value}
function floorIntakePlainValue(){const raw=floorIntakeValue();return state.intakeEditor?.getPlainText?.()??window.CalliopeObjectEditor?.plainText?.(raw)??raw}
function focusFloorIntake(){if(state.intakeEditor?.focus)state.intakeEditor.focus();else $('floorIntakeInput').focus()}
function setFloorIntakePlaceholder(){const value=floorIntakePlaceholder();$('floorIntakeInput').placeholder=value;state.intakeEditor?.setPlaceholder?.(value)}
async function lookupFloorIntakeObjects(query,kind=''){const key=`${kind}:${String(query).trim().toLowerCase()}`;if(state.intakeObjectCache.has(key))return state.intakeObjectCache.get(key);const params=new URLSearchParams({q:query,limit:'16'});if(kind)params.set('kind',kind);const data=await api(`/api/calliope/objects?${params}`),objects=Array.isArray(data.objects)?data.objects:[];if(state.intakeObjectCache.size>=100)state.intakeObjectCache.delete(state.intakeObjectCache.keys().next().value);state.intakeObjectCache.set(key,objects);return objects}
async function lookupFloorIntakeObjectHints(candidates,signal){if(!Array.isArray(candidates)||!candidates.length)return[];const data=await api('/api/calliope/object-hints',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({candidates}),signal});return Array.isArray(data.hints)?data.hints:[]}
function lookupFloorIntakeAgents(){if(floorChatAgent()?.key!=='calliope')return[];const agents=floorChatAgents().filter(agent=>agent.key!=='calliope'&&!agent.primary);return agents.length?[{key:'all',name:'all',description:'Invite every active specialist except Calliope into this turn.',fanout:true},...agents]:[]}
function pastedFloorIntakeImages(event){const clipboard=event.clipboardData;if(!clipboard)return[];const direct=[...(clipboard.files||[])].filter(file=>file.type.startsWith('image/'));if(direct.length)return direct;return[...(clipboard.items||[])].filter(item=>item.kind==='file'&&item.type.startsWith('image/')).map(item=>item.getAsFile()).filter(Boolean)}
function pasteFloorIntakeImages(event){if(event.clipboardData?.getData('text/plain'))return false;const images=pastedFloorIntakeImages(event);if(!images.length)return false;event.preventDefault();readFloorIntakeImages(images);return true}
function initializeFloorIntakeEditor(){const host=$('floorIntakeEditor');if(!host||!window.CalliopeObjectEditor?.mount)return;state.intakeEditor=window.CalliopeObjectEditor.mount(host,{variant:'composer',value:$('floorIntakeInput').value,placeholder:floorIntakePlaceholder(),ariaLabel:'Message Calliope. Type at to reference a person or invite a specialist, or two opening brackets to reference a company object.',lookup:lookupFloorIntakeObjects,hints:lookupFloorIntakeObjectHints,agents:lookupFloorIntakeAgents,onChange:value=>{$('floorIntakeInput').value=value;syncFloorIntake();scheduleFloorGhosts();scheduleFloorIntakeDiscovery()},onSubmit:()=>$('floorIntakeForm').requestSubmit(),onPaste:pasteFloorIntakeImages});$('floorIntakeInput').hidden=true;host.hidden=false}
function floorAgentAvatarMarkup(agent,option=false){const image=floorAgentImage(agent),label=agent?.name||'Calliope',fallback=initials(label),className=option?'floor-intake-agent-option-avatar':'floor-intake-agent-avatar';return `<span class="${className}${agent?.key!=='calliope'?' is-specialist':''}" aria-hidden="true"><span>${esc(fallback)}</span>${image?`<img data-floor-intake-avatar src="${esc(image)}" alt="" loading="lazy" decoding="async">`:''}</span>`}
const floorSignatureFonts=new Set();
function installFloorSignatureFont(agent){const family=String(agent?.signature_font||(agent?.key==='calliope'?'Homemade Apple':'')).trim();if(family&&!/^[A-Za-z0-9][A-Za-z0-9 .&+\-]{0,79}$/.test(family))return;if(family&&family!=='Homemade Apple'&&!floorSignatureFonts.has(family)){floorSignatureFonts.add(family);const link=document.createElement('link');link.rel='stylesheet';link.href=`https://fonts.googleapis.com/css2?family=${family.split(/\s+/).map(encodeURIComponent).join('+')}${family === 'Molle' ? ':ital@1' : family === 'UnifrakturCook' ? ':wght@700' : ''}&display=swap`;link.dataset.floorAgentFont=family;document.head.append(link)}document.documentElement.style.setProperty('--floor-agent-font',family?`"${family.replaceAll('"','\\"')}",cursive`:'var(--serif)')}
function renderFloorIntakeAgent(){const selected=floorChatAgent(),avatar=$('floorIntakeAgentAvatar'),image=floorAgentImage(selected);state.intakeAgentKey=selected.key;$('floorIntakeAgentName').textContent=selected.name;$('floorIntakeAgentRole').textContent=floorAgentRole(selected);avatar.classList.toggle('is-specialist',selected.key!=='calliope');avatar.innerHTML=`<span>${esc(initials(selected.name))}</span>${image?`<img data-floor-intake-avatar src="${esc(image)}" alt="" loading="lazy" decoding="async">`:''}`;$('floorIntakeAgentMenu').innerHTML=floorChatAgents().map(agent=>`<button type="button" class="floor-intake-agent-option" role="option" data-floor-intake-agent="${esc(agent.key)}" aria-selected="${String(agent.key===selected.key)}">${floorAgentAvatarMarkup(agent,true)}<span class="floor-intake-agent-option-copy"><strong>${esc(agent.name)}</strong><small>${esc(agent.description||floorAgentRole(agent))}</small></span><span class="floor-intake-agent-option-state">${agent.key==='calliope'?'<em>✦ Primary</em>':''}${agent.key===selected.key?'<b aria-hidden="true">✓</b>':''}</span></button>`).join('');setFloorIntakePlaceholder();$('floorIntakeInputLabel').textContent=`Message ${selected.name}`;installFloorSignatureFont(selected)}
function closeFloorIntakeAgentMenu(){$('floorIntakeAgentMenu').hidden=true;$('floorIntakeAgentToggle').setAttribute('aria-expanded','false')}
function toggleFloorIntakeAgentMenu(){const menu=$('floorIntakeAgentMenu'),open=menu.hidden;closeFloorIntakeDesignMenu();menu.hidden=!open;$('floorIntakeAgentToggle').setAttribute('aria-expanded',String(open));if(open)requestAnimationFrame(()=>menu.querySelector('[aria-selected="true"]')?.focus())}
function floorDesignVersions(profile){if(Array.isArray(profile?.versions)&&profile.versions.length)return profile.versions;return profile?.version?[profile.version]:[]}
function floorDesignVersionById(id){for(const profile of state.designProfiles){const version=floorDesignVersions(profile).find(item=>item.id===id);if(version)return {profile,version}}return null}
function floorAdaptiveDesignProfile(){return state.designProfiles.find(profile=>profile.is_adaptive||profile.is_builtin)||null}
function floorIntakeDesignLabel(){if(state.intakeDesignVersionId==null)return 'no design';return floorDesignVersionById(state.intakeDesignVersionId)?.profile?.name||'pinned'}
function renderFloorIntakeDesign(){const label=floorIntakeDesignLabel(),none=state.intakeDesignVersionId==null,adaptive=floorAdaptiveDesignProfile(),activeProfileId=none?'':state.intakeDesignVersionId===undefined?adaptive?.id||'':floorDesignVersionById(state.intakeDesignVersionId)?.profile?.id||'';$('floorIntakeDesignToggle').querySelector('span').textContent=label;$('floorIntakeDesignToggle').classList.toggle('unpinned',none);$('floorIntakeDesignMenu').innerHTML=[`<button type="button" role="option" data-floor-intake-design="" aria-selected="${String(none)}" class="${none?'active':''}"><b>No Design Profile</b><small>unpinned — each artifact styles itself</small></button>`,...state.designProfiles.filter(profile=>!profile.archived).map(profile=>{const version=floorDesignVersions(profile)[0];if(!version)return '';const active=!none&&profile.id===activeProfileId;return `<button type="button" role="option" data-floor-intake-design="${esc(version.id)}" aria-selected="${String(active)}" class="${active?'active':''}"><b>${esc(profile.name||'Untitled profile')}</b><small>v${esc(String(version.version??'?'))}${profile.is_builtin?' · appliance default':''}</small></button>`})].join('')}
async function loadFloorIntakeDesignProfiles(){if(state.designProfiles.length)return state.designProfiles;if(state.designProfilesPromise)return state.designProfilesPromise;state.designProfilesPromise=api('/api/calliope/styles').then(data=>{state.designProfiles=Array.isArray(data.profiles)?data.profiles:[];renderFloorIntakeDesign();return state.designProfiles}).catch(err=>{console.warn('Design Profiles unavailable',err);return state.designProfiles}).finally(()=>{state.designProfilesPromise=null});return state.designProfilesPromise}
function closeFloorIntakeDesignMenu(){$('floorIntakeDesignMenu').hidden=true;$('floorIntakeDesignToggle').setAttribute('aria-expanded','false')}
function toggleFloorIntakeDesignMenu(){const menu=$('floorIntakeDesignMenu'),open=menu.hidden;closeFloorIntakeAgentMenu();menu.hidden=!open;$('floorIntakeDesignToggle').setAttribute('aria-expanded',String(open));if(open)requestAnimationFrame(()=>menu.querySelector('[aria-selected="true"]')?.focus())}
function renderFloorIntakeAttachments(){const tray=$('floorIntakeAttachments');tray.hidden=!state.intakeAttachments.length;tray.innerHTML=state.intakeAttachments.map((attachment,index)=>`<div class="floor-intake-attachment"><img src="${esc(attachment.data_url)}" alt=""><span>${esc(attachment.name)}</span><button type="button" data-floor-intake-remove="${index}" aria-label="Remove ${esc(attachment.name)}">×</button></div>`).join('');syncFloorIntake()}
async function readFloorIntakeImages(files){const accepted=[...files].slice(0,Math.max(0,4-state.intakeAttachments.length));let added=0;for(const file of accepted){if(!/^image\/(png|jpeg|webp|gif)$/.test(file.type)){showToast(`${file.name} is not a supported image`);continue}if(state.config?.max_image_bytes&&file.size>state.config.max_image_bytes){showToast(`${file.name} is too large`);continue}try{const dataUrl=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file)});state.intakeAttachments.push({name:file.name||`Image ${state.intakeAttachments.length+1}`,data_url:dataUrl});added++}catch(_error){showToast(`${file.name} could not be read`)}}renderFloorIntakeAttachments();if(files.length&&!added&&state.intakeAttachments.length>=4)showToast('A message can include at most four images');return added}
function floorDiscoveryGroupLabel(group){return ({knowledge:'Company memory',artifacts:'Dashboards & artifacts',data:'Source data',metrics:'Governed metrics',conversations:'Conversations'})[group]||'Company evidence'}
// Compact cousin of the Gallery's formatHomeValue: currency/percent/decimals,
// grouped thousands, dash for no observation.
const floorMetricValueText=window.CalliopeAnswerable.valueText;
const floorMetricGhost=window.CalliopeAnswerable.sparkline;
function floorDiscoveryQuery(){return String(floorIntakePlainValue()||'').replace(/\s+/g,' ').trim().slice(0,300)}
function floorDiscoveryCard(item){const thumb=safeImageUrl(item.thumbnail_url),openUrl=safeOpenUrl(item.url),conversation=item.group==='conversations'||item.kind==='conversation',group=floorDiscoveryGroupLabel(item.group),subtype=String(item.subtype||item.kind||'evidence').replace(/[-_]+/g,' '),verb=conversation?'Resume conversation':'Bring to Stage';
  if(item.kind==='metric'&&item.metric){
    const trend=item.metric.trend||{},delta=window.CalliopeAnswerable.trendDelta(trend).text;
    return `<article class="floor-intake-discovery-card metric"><button type="button" class="floor-intake-discovery-pick" data-floor-discovery-id="${esc(String(item.id))}"${state.intakeDiscovery.promoting?' disabled':''}>${floorMetricGhost(item.metric.series)}<span class="floor-intake-discovery-meta">${item.bound_term?`<span class="floor-bound-chip" title="Bound term: ${esc(item.bound_term)}">bound</span>`:''}<span>${esc(group)}</span><span>${esc(item.metric.category||subtype)}</span></span><span class="floor-metric-row"><span class="floor-metric-value">${esc(floorMetricValueText(item.metric))}</span>${delta?`<span class="floor-metric-delta ${esc(trend.meaning||'neutral')}">${esc(delta)}</span>`:''}</span><strong>${esc(item.title||'Metric')}</strong><p>${esc(item.summary||'A governed metric with replayable receipts.')}</p><span class="floor-intake-discovery-intent">Ask about this metric</span></button></article>`;
  }return `<article class="floor-intake-discovery-card${thumb?' has-thumbnail':''}"><button type="button" class="floor-intake-discovery-pick" data-floor-discovery-id="${esc(String(item.id))}"${state.intakeDiscovery.promoting?' disabled':''}>${thumb?`<span class="floor-intake-discovery-thumb"><img data-floor-discovery-thumb src="${esc(thumb)}" alt="" loading="lazy" decoding="async"></span>`:''}<span class="floor-intake-discovery-meta">${item.bound_term?`<span class="floor-bound-chip" title="Bound term: ${esc(item.bound_term)}">bound</span>`:''}<span>${esc(group)}</span><span>${esc(subtype)}</span></span><strong>${esc(item.title||'Evidence')}</strong><p>${esc(item.summary||item.source||'Open this result in a new notebook.')}</p><span class="floor-intake-discovery-intent">${esc(verb)}</span></button>${openUrl&&!conversation?`<a class="floor-intake-discovery-open" data-floor-discovery-open href="${esc(openUrl)}" aria-label="${esc('Open '+String(item.title||'result'))}">Open ↗</a>`:''}</article>`}
// Reconcile by result identity: typing and unchanged results never dismantle
// a card, restart its thumbnail, or invalidate its click target.
function reconcileFloorDiscoveryCards(results,emptyText=''){
  const host=$('floorIntakeDiscoveryItems'),markup=results.map(floorDiscoveryCard);
  const signature=JSON.stringify([markup,emptyText]);if(host.dataset.signature===signature)return;
  host.dataset.signature=signature;
  host.querySelectorAll('.is-leaving,.floor-intake-discovery-empty').forEach(node=>node.remove());
  const previous=new Map([...host.children].map(node=>[node.dataset.resultId,node]));
  const before=new Map([...previous].map(([key,node])=>[key,node.getBoundingClientRect()]));
  const oldHeight=host.getBoundingClientRect().height,origin=host.getBoundingClientRect();
  const focus=document.activeElement,motion=!floorIntakeMotionPreference.matches;
  host._discoveryMotion?.cancel();
  for(const node of previous.values())node._discoveryMotion?.cancel();
  const wanted=new Set(results.map(item=>String(item.id)));
  for(const [key,node] of previous){
    if(wanted.has(key))continue;
    if(!motion){node.remove();continue}
    const rect=before.get(key);node.classList.add('is-leaving');node.inert=true;node.setAttribute('aria-hidden','true');
    Object.assign(node.style,{position:'absolute',left:`${rect.left-origin.left}px`,top:`${rect.top-origin.top}px`,width:`${rect.width}px`,height:`${rect.height}px`});
    node._discoveryMotion=node.animate([{opacity:1,transform:'translateY(0)'},{opacity:0,transform:'translateY(-5px)'}],{duration:140,easing:'ease-out'});
    node._discoveryMotion.finished.catch(()=>{}).finally(()=>node.remove());
  }
  const nodes=results.map((item,index)=>{
    const key=String(item.id);let node=previous.get(key);
    if(!node){const template=document.createElement('template');template.innerHTML=markup[index];node=template.content.firstElementChild;node.dataset.resultId=key}
    else if(node._discoveryMarkup!==markup[index]){
      const template=document.createElement('template');template.innerHTML=markup[index];
      node.className=template.content.firstElementChild.className;node.replaceChildren(...template.content.firstElementChild.childNodes);
    }
    node._discoveryMarkup=markup[index];
    const current=[...host.children].filter(child=>!child.classList.contains('is-leaving'))[index];
    if(current!==node)host.insertBefore(node,current||null);
    return node;
  });
  if(emptyText){const empty=document.createElement('div');empty.className='floor-intake-discovery-empty';empty.textContent=emptyText;host.append(empty)}
  if(focus?.isConnected&&!focus.closest('.is-leaving')&&document.activeElement!==focus)focus.focus({preventScroll:true});
  if(!motion)return;
  for(const node of nodes){
    const old=before.get(node.dataset.resultId),now=node.getBoundingClientRect();
    if(!old)node._discoveryMotion=node.animate([{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:180,easing:'ease-out'});
    else if(Math.abs(old.left-now.left)>1||Math.abs(old.top-now.top)>1)node._discoveryMotion=node.animate([{transform:`translate(${old.left-now.left}px,${old.top-now.top}px)`},{transform:'translate(0,0)'}],{duration:220,easing:'cubic-bezier(.2,.8,.2,1)'});
  }
  const height=host.getBoundingClientRect().height;
  if(oldHeight&&Math.abs(oldHeight-height)>1)host._discoveryMotion=host.animate([{height:`${oldHeight}px`},{height:`${height}px`}],{duration:220,easing:'ease-out'});
}
function renderFloorIntakeDiscovery(){
  const discovery=state.intakeDiscovery,panel=$('floorIntakeDiscovery'),status=$('floorIntakeDiscoveryStatus'),hint=$('floorIntakeDiscoveryHint'),items=$('floorIntakeDiscoveryItems');
  panel.dataset.state=discovery.status;panel.setAttribute('aria-busy',String(discovery.status==='searching'));
  if(discovery.status==='idle'){panel.hidden=true;items._discoveryMotion?.cancel();items.replaceChildren();delete items.dataset.signature;return}
  panel.hidden=false;let empty='';
  if(discovery.status==='searching'){
    status.textContent=discovery.items.length?'Updating matches…':'Looking across company knowledge and past work…';
    hint.textContent=discovery.items.length?'Previous matches stay available while this search finishes.':'Keep typing or press Enter to ask normally.';
  }else if(discovery.status==='error'){
    status.textContent='Search update unavailable';
    hint.textContent=discovery.items.length?'Previous matches are still available. Keep typing to try again.':'You can still press Enter to ask normally.';
    if(!discovery.items.length)empty='The live lookup could not finish. Your draft is untouched.';
  }else if(discovery.status==='empty'){
    status.textContent='No clear matches yet';hint.textContent='Press Enter to ask normally.';
    empty='Nothing indexed closely enough to interrupt the question.';
  }else{
    const unavailable=(discovery.searched||[]).some(source=>source.status==='unavailable');
    status.textContent=`Related · ${discovery.items.length} found`;
    hint.textContent=unavailable?'Some sources are temporarily quiet · bring a result to the Stage, open it directly, or keep asking.':'Bring a result to the Stage, open it directly, or press Enter to ask normally.';
  }
  reconcileFloorDiscoveryCards(discovery.items,empty);
}
function stopFloorIntakeDiscovery(){const discovery=state.intakeDiscovery;clearTimeout(discovery.timer);discovery.timer=null;for(const controller of discovery.controllers)controller.abort();discovery.controllers=[];discovery.pending=0;discovery.sequence++}
function resetFloorIntakeDiscovery(){stopFloorIntakeDiscovery();Object.assign(state.intakeDiscovery,{query:'',items:[],searched:[],status:'idle',errors:0,promoting:false});renderFloorIntakeDiscovery();resetFloorGhosts()}
function mergeFloorDiscoverySources(current,incoming){const merged=[...current];for(const source of incoming){const index=merged.findIndex(candidate=>candidate.key===source.key);if(index>=0)merged[index]=source;else merged.push(source)}return merged}
async function runFloorDiscoveryStream(query,sequence,spec,batch){
  const discovery=state.intakeDiscovery,controller=new AbortController();discovery.controllers.push(controller);
  try{
    const data=await api(spec.path,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({query,limit:spec.limit,mode:'ambient'}),signal:controller.signal});
    if(sequence!==discovery.sequence||query!==floorDiscoveryQuery())return;
    const searched=Array.isArray(data.searched)?data.searched:[];
    if(searched.some(source=>source.status==='unavailable'))batch.errors++;
    batch.results.set(spec.key,{items:(Array.isArray(data.items)?data.items:[]).slice(0,spec.limit),searched});
  }catch(error){
    if(error?.name==='AbortError'||sequence!==discovery.sequence)return;
    batch.errors++;batch.results.set(spec.key,{items:[],searched:[{key:spec.key,label:spec.label,count:0,status:'unavailable'}]});
  }finally{
    if(sequence!==discovery.sequence||query!==floorDiscoveryQuery())return;
    discovery.controllers=discovery.controllers.filter(candidate=>candidate!==controller);discovery.pending=Math.max(0,discovery.pending-1);
    if(discovery.pending)return;
    // Publish one coherent snapshot, independent of which source was faster.
    const known=new Set(),next=[];let searched=[];
    for(const source of batch.specs){const result=batch.results.get(source.key);if(!result)continue;
      for(const item of result.items){const id=String(item.id);if(!known.has(id)){known.add(id);next.push(item)}}
      searched=mergeFloorDiscoverySources(searched,result.searched);
    }
    next.sort((a,b)=>(b.bound_term?1:0)-(a.bound_term?1:0));discovery.errors=batch.errors;
    if(batch.errors&&!next.length){discovery.status='error'}
    else{discovery.items=next;discovery.searched=searched;discovery.status=next.length?'ready':'empty'}
    // Failed or partial lookups must not poison a later retry's cache.
    if(!batch.errors){if(discovery.cache.size>=40)discovery.cache.delete(discovery.cache.keys().next().value);discovery.cache.set(query,{items:[...next],searched:[...searched]})}
    renderFloorIntakeDiscovery();
  }
}
function runFloorIntakeDiscovery(query,sequence){
  const discovery=state.intakeDiscovery,specs=[{key:'evidence',label:'Company evidence',path:'/api/calliope/desk/search',limit:8},{key:'conversations',label:'Conversations',path:'/api/calliope/desk/search/conversations',limit:4}];
  const batch={specs,results:new Map(),errors:0};discovery.pending=specs.length;
  for(const spec of specs)void runFloorDiscoveryStream(query,sequence,spec,batch);
}
function scheduleFloorIntakeDiscovery(){
  const discovery=state.intakeDiscovery,query=floorDiscoveryQuery();
  if(query===discovery.query&&(discovery.timer||discovery.pending))return;
  stopFloorIntakeDiscovery();discovery.query=query;
  if(query.length<3||!/[\p{L}\p{N}]/u.test(query)){Object.assign(discovery,{items:[],searched:[],status:'idle',errors:0});renderFloorIntakeDiscovery();return}
  const cached=discovery.cache.get(query);
  if(cached){Object.assign(discovery,{items:[...cached.items],searched:[...cached.searched],status:cached.items.length?'ready':'empty',errors:0});renderFloorIntakeDiscovery();return}
  // Keep the rendered snapshot and its actions throughout debounce + fetch.
  Object.assign(discovery,{status:'searching',errors:0});renderFloorIntakeDiscovery();const sequence=discovery.sequence;
  discovery.timer=setTimeout(()=>{discovery.timer=null;runFloorIntakeDiscovery(query,sequence)},700);
}
// ── Metric ghosts ─────────────────────────────────────────────────────────
// The answerable index (governed metrics + the words that reach them: title,
// aliases, Resolution Ledger terms) is fetched once per open and matched in
// the browser on every keystroke, so a metric the draft seems to be about
// appears beside the composer before the 700 ms evidence search even starts.
// Values fill in from one cached lookup. Nothing here interrupts the question.
// Scoring lives in /calliope/answerable-match.js so the Floor's ghost rails and
// the chat composer's suggestion rail rank the same draft identically.
const FLOOR_GHOST_STOPWORDS=window.CalliopeAnswerable.STOPWORDS;
const floorGhostNormalize=window.CalliopeAnswerable.normalize;
const floorGhostStem=window.CalliopeAnswerable.stem;
const floorGhostTokens=window.CalliopeAnswerable.tokens;
const floorGhostPrepare=window.CalliopeAnswerable.prepare;
async function loadFloorAnswerable(){const ghosts=state.ghosts;if(ghosts.index&&Date.now()-ghosts.indexAt<300000)return ghosts.index;if(ghosts.indexPromise)return ghosts.indexPromise;ghosts.indexPromise=api('/api/calliope/floor/answerable').then(data=>{ghosts.index={metrics:(Array.isArray(data.metrics)?data.metrics:[]).map(floorGhostPrepare)};ghosts.indexAt=Date.now();ghosts.query='';scheduleFloorGhosts();return ghosts.index}).catch(error=>{console.warn('Answerable index unavailable',error);return null}).finally(()=>{ghosts.indexPromise=null});return ghosts.indexPromise}
function floorGhostMatches(text){return window.CalliopeAnswerable.matches(state.ghosts.index,text,6)}
function floorGhostWhen(iso){const at=new Date(iso);if(Number.isNaN(at.getTime()))return '';const minutes=(Date.now()-at.getTime())/6e4;if(minutes<1)return 'now';if(minutes<60)return `${Math.round(minutes)}m ago`;if(minutes<48*60)return `${Math.round(minutes/60)}h ago`;if(minutes<60*24*14)return `${Math.round(minutes/1440)}d ago`;return at.toLocaleDateString(undefined,{month:'short',day:'numeric'})}
function positionFloorGhostRails(){const form=$('floorIntakeForm'),stack=form?.parentElement;if(stack)stack.style.setProperty('--floor-ghost-top',`${form.offsetTop+34}px`)}
function floorGhostSignature(match){const live=state.ghosts.values.get(match.metric.name);return `${match.via?.kind||''}:${match.via?.text||''}|${live?`${live.value}|${live.trend?.percent??''}|${live.observed_at||''}`:'pending'}`}
function floorGhostCardInner(match){const metric=match.metric,live=state.ghosts.values.get(metric.name),trend=live?.trend||{},delta=live?window.CalliopeAnswerable.trendDelta(trend).text:'',bound=match.via?.kind==='binding'?match.via:null,category=metric.family||metric.category||(match.via?.kind==='alias'?`also “${match.via.text}”`:'Governed metric');return `<button type="button" class="floor-ghost-pick" data-floor-ghost="${esc(metric.name)}" title="${esc(bound?.gloss||`Ask about ${metric.title} with its receipts`)}">${live?floorMetricGhost(live.series):''}<span class="floor-ghost-meta"><span>${esc(category)}</span></span><span class="floor-metric-row"><span class="floor-metric-value${live?'':' is-pending'}" title="${live?esc(floorMetricValueText(live)):''}">${live?esc(floorMetricValueText({...live,display:{...(live.display||{}),compact:true}})):'···'}</span>${delta?`<span class="floor-metric-delta ${esc(trend.meaning||'neutral')}" title="${esc(trend.basis||'vs previous observation')}">${esc(delta)}</span>`:''}</span><strong>${esc(metric.title)}</strong>${live?.observed_at?`<time class="floor-ghost-when" datetime="${esc(live.observed_at)}" title="Latest snapshot">${esc(floorGhostWhen(live.observed_at))}</time>`:live?'<small>no observation yet</small>':''}${bound?`<span class="floor-bound-chip" title="${esc(bound.gloss||'Bound in the Resolution Ledger')}">${esc(bound.text)}</span>`:''}</button>`}
function floorGhostElement(match,order){const el=document.createElement('article');el.className=`floor-ghost${state.ghosts.values.get(match.metric.name)?' is-live':' is-pending'}`;el.dataset.name=match.metric.name;el.dataset.sig=floorGhostSignature(match);el.style.setProperty('--ghost-i',String(order));el.innerHTML=floorGhostCardInner(match);return el}
// Keyed reconciliation: a tile that survives the keystroke keeps its node (no
// re-entry animation); only new tiles animate in and stale ones drop out.
function renderFloorGhosts(){const rails=[$('floorGhostLeft'),$('floorGhostRight')];if(!rails[0]||!rails[1])return;const matches=state.ghosts.matches.slice(0,6);positionFloorGhostRails();document.body.classList.toggle('floor-ghosts-on',matches.length>0);const perSide=[[],[]];matches.forEach((match,index)=>perSide[index%2].push(match));rails.forEach((rail,side)=>{const wanted=perSide[side],names=new Set(wanted.map(match=>match.metric.name));for(const el of [...rail.querySelectorAll(':scope>.floor-ghost')]){if(!names.has(el.dataset.name))el.remove()}let fresh=0;wanted.forEach((match,position)=>{const name=match.metric.name,signature=floorGhostSignature(match),live=Boolean(state.ghosts.values.get(name));let el=rail.querySelector(`:scope>.floor-ghost[data-name="${CSS.escape(name)}"]`);if(el){if(el.dataset.sig!==signature){el.innerHTML=floorGhostCardInner(match);el.dataset.sig=signature;el.classList.toggle('is-live',live);el.classList.toggle('is-pending',!live)}}else el=floorGhostElement(match,fresh++);const current=rail.children[position]||null;if(current!==el)rail.insertBefore(el,current)});rail.hidden=!wanted.length})}
function resetFloorGhosts(){const ghosts=state.ghosts;clearTimeout(ghosts.timer);clearTimeout(ghosts.matchTimer);ghosts.timer=null;ghosts.matchTimer=null;ghosts.applied='';ghosts.controller?.abort();ghosts.controller=null;ghosts.query='';ghosts.matches=[];renderFloorGhosts()}
async function fetchFloorGhostValues(names){const ghosts=state.ghosts;ghosts.controller?.abort();const controller=new AbortController();ghosts.controller=controller;try{const data=await api('/api/calliope/floor/ghosts',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({names}),signal:controller.signal});for(const item of (Array.isArray(data.metrics)?data.metrics:[])){if(item?.name)ghosts.values.set(item.name,{...item,at:Date.now()})}if(ghosts.controller===controller)ghosts.controller=null;renderFloorGhosts()}catch(error){if(error?.name!=='AbortError')console.warn('Metric ghost values unavailable',error)}}
function applyFloorGhostMatches(){const ghosts=state.ghosts,text=ghosts.query;ghosts.matches=text.length>=3?floorGhostMatches(text):[];renderFloorGhosts();const missing=ghosts.matches.map(match=>match.metric.name).filter(name=>{const cached=ghosts.values.get(name);return !cached||Date.now()-cached.at>60000});clearTimeout(ghosts.timer);ghosts.timer=null;if(!missing.length)return;ghosts.timer=setTimeout(()=>{ghosts.timer=null;void fetchFloorGhostValues(missing)},60)}
// Trailing debounce: the rails settle after a pause in typing instead of
// re-ranking on every keystroke.
const FLOOR_GHOST_MATCH_DELAY_MS=280;
function scheduleFloorGhosts(){const ghosts=state.ghosts,text=floorDiscoveryQuery();if(!ghosts.index){ghosts.query=text;void loadFloorAnswerable();return}if(text===ghosts.query&&(ghosts.matchTimer||ghosts.applied===text))return;ghosts.query=text;clearTimeout(ghosts.matchTimer);ghosts.matchTimer=setTimeout(()=>{ghosts.matchTimer=null;ghosts.applied=text;applyFloorGhostMatches()},FLOOR_GHOST_MATCH_DELAY_MS)}
function chooseFloorGhost(name){const match=state.ghosts.matches.find(candidate=>candidate.metric.name===name);if(!match)return;beginFloorMetric({id:`ghost:${name}`,title:match.metric.title,metric:{name,params:{}}})}
function openFloorIntakeDb(){return new Promise((resolve,reject)=>{if(!window.indexedDB){reject(new Error('Draft storage is unavailable'));return}const request=indexedDB.open(FLOOR_INTAKE_DB_NAME,1);request.onupgradeneeded=()=>{if(!request.result.objectStoreNames.contains(FLOOR_INTAKE_DB_STORE))request.result.createObjectStore(FLOOR_INTAKE_DB_STORE,{keyPath:'id'})};request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error||new Error('Draft storage is unavailable'))})}
async function writeFloorIntakeHandoff(payload){const db=await openFloorIntakeDb();try{await new Promise((resolve,reject)=>{const transaction=db.transaction(FLOOR_INTAKE_DB_STORE,'readwrite');transaction.objectStore(FLOOR_INTAKE_DB_STORE).put(payload);transaction.oncomplete=resolve;transaction.onerror=()=>reject(transaction.error||new Error('Draft storage failed'));transaction.onabort=()=>reject(transaction.error||new Error('Draft storage failed'))})}finally{db.close()}sessionStorage.setItem(FLOOR_INTAKE_STORAGE_KEY,payload.id)}
function floorIntakeIsEmpty(){return !floorIntakeValue().trim()&&!state.intakeAttachments.length&&!state.intakePlaybooks.length}
function syncFloorIntake(){
  const open=document.body.classList.contains('floor-intake-open'),send=$('floorIntakeSend');
  $('floorIntake').setAttribute('aria-hidden',String(!open));$('floorIntakeOpen').setAttribute('aria-expanded',String(open));
  const empty=floorIntakeIsEmpty(),action=empty?'data':'send',label=empty?'Explore data':'Send message';
  if(send.dataset.intakeAction!==action){
    send.dataset.intakeAction=action;send.textContent=empty?'→':'↑';
    send.setAttribute('aria-label',label);send.title=label;
  }
  send.disabled=floorDictation.state.phase!=='idle'||(!empty&&!floorIntakeValue().trim())||$('floorIntakeForm').classList.contains('submitting');
}
function renderFloorIntakePlaybooks(){
  window.CalliopePlaybookPicker.renderSelections($('floorIntakePlaybooks'),state.intakePlaybooks,ref=>{
    state.intakePlaybooks=state.intakePlaybooks.filter(item=>item.ref_id!==ref);renderFloorIntakePlaybooks();
  });
  syncFloorIntake();
}
function floorIntakeSubmissionText(text=floorIntakeValue().trim()){
  const prompt=window.CalliopePlaybookPicker.withReferences(text,state.intakePlaybooks);
  const limit=state.config?.max_message_chars||200_000;
  if([...prompt].length>limit){showToast(`Messages can contain up to ${limit.toLocaleString()} characters, including playbook references. Your draft is preserved; shorten it or attach a document in chat.`);return '';}
  return prompt;
}
function openFloorIntakePlaybook(){
  if($('floorIntakeForm').classList.contains('submitting'))return;
  window.CalliopePlaybookPicker.open({
    getValue:floorIntakeValue,
    select:item=>{
      if($('floorIntakeForm').classList.contains('submitting'))return false;
      if(!state.intakePlaybooks.some(ref=>ref.ref_id===item.ref_id)){
        if((window.CalliopeObjectEditor?.parseObjectMarkers(floorIntakeValue()).length||0)+state.intakePlaybooks.length>=24)return false;
        state.intakePlaybooks.push(item);
      }
      renderFloorIntakePlaybooks();
    },
    focus:focusFloorIntake,
  });
}
function renderFloorIntakeTeam(){const button=$('floorIntakeTeam');button.setAttribute('aria-pressed',String(state.intakeTeamMode));button.querySelector('span').textContent=state.intakeTeamMode?'Team on':'Team off'}
function setFloorIntakeHeadline(index){floorIntakeHeadlineIndex=index;const headline=FLOOR_INTAKE_HEADLINES[index],element=$('floorIntakeHeadline');element.textContent=headline;element.dataset.length=headline.length>64?'long':'short'}
function refillFloorIntakeHeadlineQueue(){floorIntakeHeadlineQueue=FLOOR_INTAKE_HEADLINES.map((_headline,index)=>index).filter(index=>index!==floorIntakeHeadlineIndex);for(let index=floorIntakeHeadlineQueue.length-1;index>0;index--){const swapIndex=Math.floor(Math.random()*(index+1));[floorIntakeHeadlineQueue[index],floorIntakeHeadlineQueue[swapIndex]]=[floorIntakeHeadlineQueue[swapIndex],floorIntakeHeadlineQueue[index]]}}
function nextFloorIntakeHeadlineIndex(){if(!floorIntakeHeadlineQueue.length)refillFloorIntakeHeadlineQueue();return floorIntakeHeadlineQueue.pop()}
function floorIntakeHeadlineCanRotate(){return document.body.classList.contains('floor-intake-open')&&!document.hidden&&!floorIntakeMotionPreference.matches}
function stopFloorIntakeHeadlineRotation(reset=false){clearTimeout(floorIntakeHeadlineTimer);clearTimeout(floorIntakeHeadlineSwapTimer);floorIntakeHeadlineTimer=null;floorIntakeHeadlineSwapTimer=null;$('floorIntakeHeadline').classList.remove('is-changing');if(reset){floorIntakeHeadlineQueue=[];setFloorIntakeHeadline(0)}}
function scheduleFloorIntakeHeadlineRotation(){if(!floorIntakeHeadlineCanRotate()||floorIntakeHeadlineTimer||floorIntakeHeadlineSwapTimer)return;floorIntakeHeadlineTimer=setTimeout(()=>{floorIntakeHeadlineTimer=null;if(!floorIntakeHeadlineCanRotate()){stopFloorIntakeHeadlineRotation();return}const element=$('floorIntakeHeadline');element.classList.add('is-changing');floorIntakeHeadlineSwapTimer=setTimeout(()=>{floorIntakeHeadlineSwapTimer=null;if(!floorIntakeHeadlineCanRotate()){stopFloorIntakeHeadlineRotation();return}setFloorIntakeHeadline(nextFloorIntakeHeadlineIndex());requestAnimationFrame(()=>{element.classList.remove('is-changing');scheduleFloorIntakeHeadlineRotation()})},FLOOR_INTAKE_HEADLINE_FADE_MS)},FLOOR_INTAKE_HEADLINE_ROTATION_MS)}
function syncFloorIntakeHeadlineRotation(){if(floorIntakeHeadlineCanRotate())scheduleFloorIntakeHeadlineRotation();else stopFloorIntakeHeadlineRotation(!document.body.classList.contains('floor-intake-open')||floorIntakeMotionPreference.matches)}
function openFloorIntake(){
  if(document.body.classList.contains('floor-intake-open'))return;
  loadFloorSpeech();state.intakeTeamMode=false;closeDrawer();renderFloorIntakeAgent();renderFloorIntakeDesign();renderFloorIntakeAttachments();renderFloorIntakeTeam();document.body.classList.add('floor-intake-open');document.querySelector('.app').inert=true;syncFloorIntake();syncFloorIntakeHeadlineRotation();Promise.allSettled([loadAgentRoster(),loadFloorIntakeDesignProfiles(),loadFloorAnswerable()]);
  scheduleFloorGhosts();scheduleFloorIntakeDiscovery();requestAnimationFrame(focusFloorIntake);
}
function closeFloorIntake(){
  if(!document.body.classList.contains('floor-intake-open'))return;
  floorDictation.cancel();resetFloorIntakeDiscovery();closeFloorIntakeAgentMenu();closeFloorIntakeDesignMenu();document.body.classList.remove('floor-intake-open');stopFloorIntakeHeadlineRotation(true);document.querySelector('.app').inert=false;$('floorIntakeForm').classList.remove('submitting');$('floorIntakeStatus').textContent='';syncFloorIntake();
  requestAnimationFrame(()=>$('floorIntakeOpen').focus());
}
async function beginFloorIntake({openData=false}={}){
  const form=$('floorIntakeForm');
  if(floorDictation.state.phase!=='idle'||form.classList.contains('submitting'))return;
  const empty=floorIntakeIsEmpty();
  // Only explicit button activation can enter Data; empty composer Enter is inert.
  if (openData ? !empty : empty) return;
  const prompt=openData?'':floorIntakeSubmissionText();if(!openData&&!prompt)return;
  stopFloorIntakeDiscovery();$('floorIntakeDiscovery').hidden=true;form.classList.add('submitting');$('floorIntakeStatus').textContent='Opening a new notebook…';syncFloorIntake();
  const payload={id:globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(16).slice(2)}`,prompt,created_at:Date.now(),agent_key:state.intakeAgentKey,team_mode:state.intakeTeamMode,authoring_mode:'html',attachments:state.intakeAttachments};if(state.intakeDesignVersionId!==undefined)payload.design_version_id=state.intakeDesignVersionId;
  if(openData)payload.launch_mode='data';
  try{await writeFloorIntakeHandoff(payload);window.location.assign('/calliope?view=zen&start=1')}
  catch(_error){form.classList.remove('submitting');$('floorIntakeStatus').textContent='This browser could not carry the draft into Calliope.';syncFloorIntake();scheduleFloorIntakeDiscovery()}
}

async function beginFloorEvidence(item){const discovery=state.intakeDiscovery,form=$('floorIntakeForm'),prompt=floorIntakeSubmissionText(),query=floorDiscoveryQuery();if(!item?.id||!prompt||query.length<3||discovery.promoting||form.classList.contains('submitting'))return;stopFloorIntakeDiscovery();discovery.promoting=true;discovery.status='ready';form.classList.add('submitting');$('floorIntakeStatus').textContent=`Bringing “${item.title||'this breadcrumb'}” into a new notebook…`;syncFloorIntake();renderFloorIntakeDiscovery();const requestBody={query,limit:24,evidence_id:item.id,origin:'floor',agent_key:state.intakeAgentKey,team_mode:state.intakeTeamMode};if(state.intakeDesignVersionId!==undefined)requestBody.design_profile_version_id=state.intakeDesignVersionId;try{const data=await api('/api/calliope/evidence-explorations',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(requestBody)}),sessionId=String(data.session?.id||''),surfaceId=String(data.surface?.id||''),persistedItems=Array.isArray(data.surface?.payload?.items)?data.surface.payload.items:[],persistedSelection=persistedItems.find(candidate=>String(candidate?.id||'')===String(item.id)),evidenceId=String(data.selected_evidence_id||persistedSelection?.id||'');if(!sessionId||!surfaceId||!evidenceId)throw new Error('The selected breadcrumb changed before it could be staged.');const target=new URL(data.url||'/calliope',window.location.origin);if(target.origin!==window.location.origin||target.pathname!=='/calliope')throw new Error('The new notebook returned an invalid destination.');const payload={id:globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(16).slice(2)}`,launch_mode:'evidence',prompt,created_at:Date.now(),authoring_mode:'html',session_id:sessionId,surface_id:surfaceId,evidence_id:evidenceId,attachments:state.intakeAttachments};await writeFloorIntakeHandoff(payload);target.searchParams.set('view','zen');window.location.assign(`${target.pathname}${target.search}${target.hash}`)}catch(error){discovery.cache.delete(query);discovery.promoting=false;form.classList.remove('submitting');$('floorIntakeStatus').textContent=error?.message||'This breadcrumb could not be staged. Your draft is still here.';syncFloorIntake();scheduleFloorIntakeDiscovery()}}
function chooseFloorDiscoveryItem(item){if(item?.group==='conversations'||item?.kind==='conversation'){const target=safeOpenUrl(item.url);if(target)window.location.assign(target);return}if(item?.kind==='metric'&&item?.metric?.name){beginFloorMetric(item);return}beginFloorEvidence(item)}
async function beginFloorMetric(item){
  const discovery=state.intakeDiscovery,form=$('floorIntakeForm'),prompt=floorIntakeValue().trim();
  if(!item?.metric?.name||discovery.promoting||form.classList.contains('submitting'))return;
  stopFloorIntakeDiscovery();discovery.promoting=true;discovery.status='ready';form.classList.add('submitting');
  $('floorIntakeStatus').textContent=`Opening “${item.title||item.metric.name}” with its receipts…`;syncFloorIntake();renderFloorIntakeDiscovery();
  try{
    const frozen=await api(`/api/calliope/gallery/metrics/${encodeURIComponent(item.metric.name)}/ask`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({params:item.metric.params||{},origin:'floor'})});
    if(!frozen?.session?.id||!frozen?.surface?.id||!frozen?.url)throw new Error('The metric notebook could not be prepared.');
    const destination=new URL(frozen.url,window.location.origin);
    if(destination.origin!==window.location.origin||destination.pathname!=='/calliope')throw new Error('The metric notebook returned an invalid destination.');
    const seeded=destination.searchParams.get('prompt')||'';
    const submittedPrompt=floorIntakeSubmissionText(prompt||seeded);
    if(!submittedPrompt)throw new Error('Shorten the message or remove a playbook before opening this metric.');
    await writeFloorIntakeHandoff({id:globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(16).slice(2)}`,launch_mode:'starter_surface',prompt:submittedPrompt,created_at:Date.now(),agent_key:state.intakeAgentKey,team_mode:state.intakeTeamMode,authoring_mode:'html',attachments:state.intakeAttachments,session_id:frozen.session.id,surface_id:frozen.surface.id,source_item_id:item.id,source_kind:'metric'});
    destination.searchParams.delete('prompt');destination.searchParams.set('view','zen');
    window.location.assign(destination.pathname+destination.search);
  }catch(error){
    discovery.cache.delete(floorDiscoveryQuery());discovery.promoting=false;form.classList.remove('submitting');
    $('floorIntakeStatus').textContent=error?.message||'This metric could not be staged. Your draft is still here.';
    syncFloorIntake();scheduleFloorIntakeDiscovery();
  }
}
function hydrateFilters(){
  const people=state.facets.people||[];
  const kinds=state.facets.kinds||[];
  const prevP=$('people').value,prevK=$('kinds').value;
  $('people').innerHTML='<option value="all">All people</option>'+people.map(p=>`<option value="${esc(p)}">${esc(displayAgent(p))}</option>`).join('');
  $('kinds').innerHTML='<option value="all">All kinds</option>'+Array.from(new Set([...kinds,'page'])).map(k=>`<option value="${esc(k)}">${esc(kindLabel(k))}</option>`).join('');
  if(people.includes(prevP))$('people').value=prevP;if(kinds.includes(prevK)||prevK==='page')$('kinds').value=prevK;
}
['search','people','kinds','freshness'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',()=>{syncMeToggle();scheduleFloorSearch()}));
// One-tap "just my pieces": flips the people filter to the viewer and back.
// The dropdown still works for everyone else; this is the everyday gesture.
// "Me" is the grid view: my threads as tiles, newest first. The board's
// people filter still narrows to the viewer underneath, so search, kinds
// and freshness keep working exactly as they do on the board.
function syncMeToggle(){
  const on=state.gridMode&&Boolean(state.viewer?.email);
  $('meToggle').setAttribute('aria-pressed',String(on));$('meToggle').title=on?'Back to the Floor':'My threads as a grid — newest first, click a tile to open it';
  const notices=$('noticesToggle');notices.hidden=!on;notices.setAttribute('aria-pressed',String(state.hideNotices));
  notices.title=state.hideNotices?'Notices hidden · show low-priority notices':'Notices shown · hide low-priority notices';
}
function syncViewButtons(){$('boardBtn').classList.toggle('on',state.view==='board');$('listBtn').classList.toggle('on',state.view==='list')}
function enterGridMode(){
  const me=String(state.viewer?.email||'');if(!me||state.gridMode)return;
  const select=$('people');
  if(![...select.options].some(o=>o.value===me))select.insertAdjacentHTML('beforeend',`<option value="${esc(me)}">${esc(displayAgent(me))}</option>`);
  select.value=me;state.gridMode=true;state.viewBeforeGrid=state.view==='grid'?'board':state.view;state.view='grid';state.gridLoadStartedAt=performance.now();state.gridLoadedIn=0;
  syncViewButtons();syncMeToggle();scheduleFloorSearch();
}
function leaveGridMode(view){
  if(!state.gridMode)return;
  state.gridMode=false;$('people').value='all';state.view=view||state.viewBeforeGrid||'board';
  syncViewButtons();syncMeToggle();scheduleFloorSearch();
}
$('noticesToggle').addEventListener('click',()=>{if(!state.gridMode)return;state.hideNotices=!state.hideNotices;syncMeToggle();scheduleFloorSearch()});
$('meToggle').addEventListener('click',()=>{if(state.gridMode)leaveGridMode();else enterGridMode()});
$('boardBtn').addEventListener('click',()=>{if(state.gridMode){leaveGridMode('board');return}state.view='board';syncViewButtons();render()});
$('listBtn').addEventListener('click',()=>{if(state.gridMode){leaveGridMode('list');return}state.view='list';syncViewButtons();render()});
$('liveBtn').addEventListener('click',showLive);
const timeViz=$('timeViz');
timeViz.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();timeViz.focus({preventScroll:true});state.scrubPointer=e.pointerId;timeViz.setPointerCapture(e.pointerId);timeViz.classList.add('dragging');previewScrubAt(timeAtClientX(e.clientX),220)});
timeViz.addEventListener('pointermove',e=>{if(state.scrubPointer!==e.pointerId)return;e.preventDefault();previewScrubAt(timeAtClientX(e.clientX),220)});
function finishScrub(e){if(state.scrubPointer!==e.pointerId)return;e.preventDefault();previewScrubAt(timeAtClientX(e.clientX),24);state.scrubPointer=null;timeViz.classList.remove('dragging');try{timeViz.releasePointerCapture(e.pointerId)}catch(_e){}}
timeViz.addEventListener('pointerup',finishScrub);timeViz.addEventListener('pointercancel',e=>{if(state.scrubPointer!==e.pointerId)return;state.scrubPointer=null;timeViz.classList.remove('dragging');clearTimeout(state.scrubTimer);state.scrubPreviewAt=null;renderTimeline();reloadFloorPieces()});
timeViz.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End','PageUp','PageDown'].includes(e.key))return;e.preventDefault();const {min,max}=timeBounds(),base=state.scrubPreviewAt??(state.timeMode==='history'?Date.parse(state.selectedSnapshotAt):max),step=(max-min)/(e.shiftKey?20:100),target=e.key==='Home'?min:e.key==='End'?max:Math.max(min,Math.min(max,base+(e.key==='ArrowLeft'||e.key==='PageDown'?-step:step)));previewScrubAt(target,40)});
new ResizeObserver(()=>{if(state.snapshots.length)requestAnimationFrame(renderTimeline)}).observe(timeViz);
$('reload').addEventListener('click',()=>state.timeMode==='history'?reloadFloorPieces():load());$('drawerClose').addEventListener('click',closeDrawer);$('drawerBackdrop').addEventListener('click',e=>{if(e.target===$('drawerBackdrop'))closeDrawer()});document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer()});
// The launcher hint remains hoverable and can be dismissed without moving focus.
$('floorPages').addEventListener('click',()=>{
  const kinds=$('kinds');if(!Array.from(kinds.options).some(o=>o.value==='page'))kinds.add(new Option('Page','page'));
  kinds.value=kinds.value==='page'?'all':'page';kinds.dispatchEvent(new Event('change'));
});
$('floorNewPage').addEventListener('click',()=>{
  openFloorIntake();
  if(!floorIntakeValue().trim()){const draft='Help me create a living Page about ';$('floorIntakeInput').value=draft;state.intakeEditor?.setValue?.(draft)}
  syncFloorIntake();focusFloorIntake();
});
for(const event of ['pointerenter','focus'])$('floorIntakeOpen').addEventListener(event,()=>delete $('floorIntakeOpen').dataset.tooltipDismissed);
document.addEventListener('keydown',event=>{if(event.key==='Escape')$('floorIntakeOpen').dataset.tooltipDismissed='true'});
$('floorIntakeOpen').addEventListener('click',()=>{document.body.classList.contains('floor-intake-open')?closeFloorIntake():openFloorIntake()});$('floorIntake').addEventListener('click',e=>{if(e.target===$('floorIntake'))closeFloorIntake()});$('floorIntakeInput').addEventListener('input',()=>{syncFloorIntake();scheduleFloorGhosts();scheduleFloorIntakeDiscovery()});$('floorIntake').addEventListener('click',e=>{const button=e.target.closest('[data-floor-ghost]');if(button)chooseFloorGhost(button.dataset.floorGhost)});window.addEventListener('resize',()=>{if(document.body.classList.contains('floor-intake-open'))positionFloorGhostRails()});$('floorIntakeInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('floorIntakeForm').requestSubmit()}});$('floorIntakeInput').addEventListener('paste',e=>{if(!pasteFloorIntakeImages(e))return;e.preventDefault()});$('floorIntakeForm').addEventListener('submit',e=>{e.preventDefault();beginFloorIntake({openData:floorIntakeIsEmpty()&&e.submitter===$('floorIntakeSend')})});$('floorIntakeDiscoveryItems').addEventListener('click',e=>{if(e.target.closest('[data-floor-discovery-open]'))return;const button=e.target.closest('[data-floor-discovery-id]');if(!button)return;const item=state.intakeDiscovery.items.find(item=>String(item.id)===button.dataset.floorDiscoveryId);if(item)chooseFloorDiscoveryItem(item)});$('floorIntakeAgentToggle').addEventListener('click',e=>{e.stopPropagation();toggleFloorIntakeAgentMenu()});$('floorIntakeAgentMenu').addEventListener('click',e=>{const button=e.target.closest('[data-floor-intake-agent]');if(!button)return;state.intakeAgentKey=button.dataset.floorIntakeAgent;renderFloorIntakeAgent();closeFloorIntakeAgentMenu();requestAnimationFrame(focusFloorIntake)});$('floorIntakeDesignToggle').addEventListener('click',e=>{e.stopPropagation();toggleFloorIntakeDesignMenu()});$('floorIntakeDesignMenu').addEventListener('click',e=>{const button=e.target.closest('[data-floor-intake-design]');if(!button)return;state.intakeDesignVersionId=button.dataset.floorIntakeDesign||null;renderFloorIntakeDesign();closeFloorIntakeDesignMenu();requestAnimationFrame(focusFloorIntake)});$('floorIntakePlaybook').addEventListener('click',openFloorIntakePlaybook);$('floorIntakeTeam').addEventListener('click',()=>{state.intakeTeamMode=!state.intakeTeamMode;renderFloorIntakeTeam()});$('floorIntakeImages').addEventListener('change',async e=>{await readFloorIntakeImages(e.target.files||[]);e.target.value=''});$('floorIntakeAttachments').addEventListener('click',e=>{const button=e.target.closest('[data-floor-intake-remove]');if(!button)return;state.intakeAttachments.splice(Number(button.dataset.floorIntakeRemove),1);renderFloorIntakeAttachments()});document.addEventListener('click',e=>{if(!e.target.closest('#floorIntakeAgent'))closeFloorIntakeAgentMenu();if(!e.target.closest('#floorIntakeDesign'))closeFloorIntakeDesignMenu()});document.addEventListener('keydown',e=>{if(e.key!=='Escape'||!document.body.classList.contains('floor-intake-open')||document.querySelector('#playbook-picker[open]'))return;e.preventDefault();if(!$('floorIntakeAgentMenu').hidden){closeFloorIntakeAgentMenu();$('floorIntakeAgentToggle').focus();return}if(!$('floorIntakeDesignMenu').hidden){closeFloorIntakeDesignMenu();$('floorIntakeDesignToggle').focus();return}closeFloorIntake()});
window.addEventListener('resize',()=>{clearTimeout(state.fitTimer);state.fitTimer=setTimeout(()=>{fitLanes();if(state.snapshots.length)renderTimeline()},90)});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')safeRefresh();syncFloorIntakeHeadlineRotation()});
document.addEventListener('error',event=>{const img=event.target?.closest?.('img[data-floor-avatar],img[data-floor-intake-avatar],img[data-floor-launch-avatar],img[data-floor-discovery-thumb],img[data-floor-starter-thumb]');if(!img)return;if(img.matches('[data-floor-avatar]')){const person=state.people.get(String(img.dataset.floorAvatar||''));if(person)person.avatar_url=null}if(img.matches('[data-floor-discovery-thumb]')){const card=img.closest('.floor-intake-discovery-card');img.closest('.floor-intake-discovery-thumb')?.remove();card?.classList.remove('has-thumbnail');return}img.remove()},true);
setInterval(safeRefresh,30000);
initializeFloorIntakeEditor();
const overview=window.CalliopeFloorMetrics.create({
  api, isHistorical:()=>state.timeMode==='history'||state.scrubPreviewAt!==null,
  returnLive:async()=>{
    const previous={timeMode:state.timeMode,selectedGeneration:state.selectedGeneration,selectedSnapshotAt:state.selectedSnapshotAt,selectedExpectedTotal:state.selectedExpectedTotal,scrubPreviewAt:state.scrubPreviewAt};
    await showLive();
    if($('workspace').classList.contains('view-stale')||$('workspace').classList.contains('view-loading')){
      Object.assign(state,previous);$('liveDot').className='live-dot past';renderTimeline();throw new Error('Live Floor unavailable');
    }
  }, redrawHistory:renderTimeline,
  openMetric:metric=>beginFloorStarter({title:metric.title||metric.name,sourceKind:'metric',action:{kind:'metric',name:metric.name,params:metric.params||{}}}),
  openArea:area=>beginFloorStarter({title:area.name,sourceKind:'subject',action:{kind:'subject',key:area.key}}),
});
syncFloorLaunchAvatar();
setFloorIntakeHeadline(0);floorIntakeMotionPreference.addEventListener?.('change',syncFloorIntakeHeadlineRotation);
load();
})();

window.addEventListener("storage",e=>{if(e.key==="calliope-favorites-changed"&&state.timeMode==="live")reloadFloorPieces({quiet:true})});
