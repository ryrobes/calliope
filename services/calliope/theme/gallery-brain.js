/* Structural overview inspired by DataRabbit's source inventory and KG clusters.
   No document or memory body is requested; no background polling or endless physics. */
(() => {
'use strict';
const $=s=>document.querySelector(s),esc=window.CalliopeMetricCards.esc;
const shell=document.createElement('section');shell.id='gallery-brain';shell.hidden=true;
shell.innerHTML=`<div class="brain-head"><p>Documents, memories & business knowledge · recorded connections</p><button data-brain-refresh title="Re-read stored observations, cached for up to one minute">Refresh</button></div><div class="brain-workspace"><aside class="brain-sources" aria-label="Knowledge sources"></aside><div class="brain-map"><canvas tabindex="0" role="img" aria-label="Knowledge graph. Select a source from the list to filter. Use plus and minus to zoom, arrow keys to pan, and zero to fit."></canvas><div class="brain-map-heading"><strong>All knowledge sources</strong><small></small></div><div class="brain-map-tools"><button data-brain-zoom="1.25" aria-label="Zoom in">+</button><button data-brain-zoom="0.8" aria-label="Zoom out">−</button><button data-brain-fit>Fit</button></div><div class="brain-empty" role="status">Loading knowledge sources…</div><div class="brain-tooltip" hidden></div><div class="brain-legend"><span>Documents</span><span>Memories</span><span>Database</span><span>Business context</span><span>Lines: recorded links · dashed: same table</span></div></div></div><div class="brain-feedback" role="status"></div>`;
$('#gallery-tables').after(shell);
const list=shell.querySelector('.brain-sources'),map=shell.querySelector('.brain-map'),canvas=map.querySelector('canvas'),ctx=canvas.getContext('2d'),hint=map.querySelector('.brain-tooltip'),feedback=shell.querySelector('.brain-feedback'),empty=map.querySelector('.brain-empty');
let active=false,data=null,selected=new URL(location.href).searchParams.get('brain_source')||'',ticket=0,controller=null,loading=false;
let communities=[],nodes=[],edges=[],byId=new Map(),frame=0,iterations=0,hover=null,camera={x:0,y:0,z:1},width=900,height=650,drag=null;
const number=n=>n==null?'—':Number(n).toLocaleString(),age=t=>t?window.CalliopeMetricCards.relative(t):'Not recorded';
const fullTime=t=>t?new Date(t).toLocaleString():'Not recorded';
const displayName=i=>i.agent_name|| (i.family==='memory'&&i.name===i.ref.slice(5)?i.name.replace(/^calliope-/,'').replace(/-[a-f0-9]{20,}$/,'').replaceAll('-',' ').replace(/\b\w/g,c=>c.toUpperCase()):i.name);
const families={catalog:'Database Structure',context:'Business Context',documents:'Document Brain',memory:'Hindsight'};
const query=()=>($('#q')?.value||'').trim().toLowerCase();
const matches=i=>!query()||[i.name,i.kind,i.family,i.ref,i.agent_name||'',i.agent_profile||''].some(v=>String(v).toLowerCase().includes(query()));
function hero(){if(!active)return;$('#gallery-kicker').textContent='Knowledge inventory';$('#gallery-title').innerHTML='Inside the <em>Brain</em>.';$('#gallery-tally').textContent=data?`${data.items.filter(i=>i.family==='documents').length} document sources · ${data.items.filter(i=>i.family==='memory').length} memory banks · ${data.items.filter(i=>i.family==='catalog'||i.family==='context').length} knowledge graphs`:'';}
function paintList(){
 if(!data)return;const items=data.items.filter(matches);
 const compact=n=>n==null?'—':new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1}).format(n);
 const countText=(i,format=compact)=>i.family==='documents'?format(i.documents)+' docs · '+format(i.chunks)+' chunks':i.family==='memory'?format(i.memories)+' memories · '+format(i.links)+' links':i.family==='catalog'?format(i.counts?.db_table)+' tables · '+format(i.counts?.db_schema)+' schemas':format(i.counts?.business_domain)+' domains · '+format(i.counts?.business_concept)+' concepts';
 const row=i=>{const memory=i.family==='memory',documents=i.family==='documents',stamp=memory?i.latest_memory_at:i.updated_at;
 const status=i.errors?number(i.errors)+' errors':i.enabled===false?'Paused':i.pending?number(i.pending)+' pending':'';
 const details=[i.name,i.kind,i.agent_profile,countText(i,number),(documents?'Synced':memory?'Latest memory':'Updated')+' · '+fullTime(stamp),status].filter(Boolean).join(' · ');
 return `<button class="brain-source" data-family="${i.family}" data-brain-source="${esc(i.ref)}" aria-pressed="${selected===i.ref}" title="${esc(details)}"><span class="brain-source-top"><strong>${esc(displayName(i))}</strong><span class="brain-age" aria-label="${esc((documents?'Synced':memory?'Latest memory':'Updated')+' '+fullTime(stamp))}">${stamp?esc(age(stamp).replace(/ ago$/,'')):'—'}</span></span><span class="brain-source-meta"><span class="brain-count">${esc(countText(i))}</span>${status?`<span class="brain-badge${i.errors?' brain-issue':''}">${esc(status)}</span>`:''}</span>${selected===i.ref?`<small class="brain-more">${documents?esc(i.kind)+' · '+number(i.ready)+' enriched<br>Enriched '+esc(fullTime(i.enriched_at)):memory?esc(i.agent_profile||i.kind)+'<br>'+number(i.documents)+' documents · '+number(i.entities)+' entities':number(i.nodes)+' nodes<br>'+Object.entries(i.counts||{}).map(([k,v])=>esc(k.replaceAll('_',' '))+' · '+number(v)).join('<br>')}</small>`:''}</button>`;};
 const familyItems=f=>{const group=items.filter(i=>i.family===f);return f==='documents'?group.sort((a,b)=>(Number(b.documents)||0)-(Number(a.documents)||0)||displayName(a).localeCompare(displayName(b))):group;};
 const folder='<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M2 5V4h6l2 2h8v10H2Z"/></svg>';
 list.innerHTML=`<button class="brain-source brain-source-all" data-brain-source="" aria-pressed="${!selected}"><strong>All sources</strong><span class="brain-age">${data.items.length}</span></button>`+Object.entries(families).map(([f,label])=>`<h2 class="brain-family" data-family="${f}">${folder}<span>${label}</span><span class="brain-family-count">${items.filter(i=>i.family===f).length}</span></h2>${familyItems(f).map(row).join('')}`).join('');
 if(!items.length)list.insertAdjacentHTML('beforeend','<p class="brain-family">No matching sources</p>');
}
function color(token,fallback){const probe=document.createElement('i');probe.style.color=`var(${token},${fallback})`;map.append(probe);const result=getComputedStyle(probe).color;probe.remove();return result;}
let palette={};
function colors(){palette={documents:color('--jade','#75cbbb'),memory:color('--amber','#dfb477'),catalog:color('--chart-3','#91b6d8'),context:color('--chart-4','#ce9eae'),entity:color('--fog','#a4afb6'),text:color('--bone','#ddd'),dim:color('--dim','#87908c'),background:color('--void','#0b0d0c')};}
function hash(s){let h=2166136261;for(const c of s)h=Math.imul(h^c.charCodeAt(0),16777619);return h>>>0;}
const portraits=new Map();
function loadAvatar(url){
 if(!url||!url.startsWith('/calliope/specialists/')||portraits.has(url))return;
 const image=new Image(),record={image,ready:false};portraits.set(url,record);
 image.onload=()=>{record.ready=true;draw();};image.onerror=()=>{record.ready=false;draw();};image.src=url+'?thumbnail=1';
}
function buildGraph(){
 cancelAnimationFrame(frame);frame=0;iterations=0;hover=null;hint.hidden=true;
 if(!data)return;
 const allowed=new Set(data.items.filter(matches).map(i=>i.ref));
 nodes=data.graph.nodes.filter(n=>n.sources.some(s=>allowed.has(s))).map(n=>({...n,label:n.root?displayName(data.items.find(i=>i.ref===n.id)):n.label,vx:0,vy:0}));byId=new Map(nodes.map(n=>[n.id,n]));
 edges=data.graph.edges.filter(e=>byId.has(e.source)&&byId.has(e.target)).map(e=>({...e,a:byId.get(e.source),b:byId.get(e.target)}));
 const roots=nodes.filter(n=>n.root),positions=window.CalliopeBrainLayout.layout(roots,nodes,edges);
 for(const position of positions){const n=byId.get(position.id),item=data.items.find(i=>i.ref===n.id);Object.assign(n,position);n.radius=n.kind==='memory'?32:9;n.color=palette[n.kind];n.avatarUrl=item.avatar_url||null;loadAvatar(n.avatarUrl);}
 const groups=new Map();
 for(const n of nodes.filter(n=>!n.root)){
  n.anchors=n.sources.map(s=>byId.get(s)).filter(r=>r?.root);n.anchor=n.anchors[0];
  const group=n.anchors.map(r=>r.id).sort().join('|');if(!groups.has(group))groups.set(group,[]);groups.get(group).push(n);
 }
 for(const [group,members] of groups){
  members.sort((a,b)=>hash(a.id)-hash(b.id));
  members.forEach((n,i)=>{const x=n.anchors.reduce((sum,r)=>sum+r.x,0)/n.anchors.length,y=n.anchors.reduce((sum,r)=>sum+r.y,0)/n.anchors.length;
   const a=i*2.3999632297+hash(group)%100,shared=n.anchors.length>1,rad=shared?12+Math.sqrt((i+1)/members.length)*40:55+Math.sqrt((i+1)/members.length)*50;
   n.x=x+Math.cos(a)*rad;n.y=y+Math.sin(a)*rad;n.tx=n.x;n.ty=n.y;n.radius=n.kind==='entity'?2.3:3.2;n.color=palette[n.family|| (n.kind==='document'?'documents':n.kind)]||palette.entity;
  });
 }
 communities=roots.map(root=>({root,members:nodes.filter(n=>!n.root&&n.sources.length===1&&n.sources[0]===root.id)}));
 nodes.forEach(n=>n.degree=0);edges.forEach(e=>{e.a.degree++;e.b.degree++;});
 map.querySelector('.brain-map-heading strong').textContent=selected?displayName(data.items.find(i=>i.ref===selected)||{name:'Source'}):'All knowledge sources';
 map.querySelector('.brain-map-heading small').textContent=`${number(nodes.length)} nodes · ${number(edges.length)} connections · bounded sample`;
 canvas.setAttribute('aria-label',`${nodes.length} nodes and ${edges.length} stored connections. Source membership and sampled relationships. Use the source list to filter, plus and minus to zoom, arrow keys to pan, zero to fit.`);
 empty.hidden=nodes.length>0;empty.textContent=data.items.length?'No graph nodes match this view.':data.unavailable.length?'Source metadata is unavailable. See the status below.':'No knowledge sources are configured.';
 fit();schedule();
}
function fit(){if(!nodes.length){camera={x:width/2,y:height/2,z:1};draw();return;}const xs=nodes.map(n=>n.x),ys=nodes.map(n=>n.y),x0=Math.min(...xs)-65,x1=Math.max(...xs)+65,y0=Math.min(...ys)-60,y1=Math.max(...ys)+60;camera.z=Math.max(.12,Math.min(2,(width-60)/(x1-x0),(height-160)/(y1-y0)));camera.x=width/2-(x0+x1)/2*camera.z;camera.y=(height+35)/2-(y0+y1)/2*camera.z;draw();}
function step(){
 // Bounded layout relaxation: indexed local collisions, springs, fixed cluster anchors.
 const grid=new Map(),cell=18;
 for(const n of nodes){if(n.root)continue;n.vx+=(n.tx-n.x)*.015;n.vy+=(n.ty-n.y)*.015;for(const r of nodes.filter(n=>n.root)){const dx=n.x-r.x,dy=n.y-r.y,d=Math.hypot(dx,dy)||.1,clearance=r.radius+12;if(d<clearance){n.vx+=dx/d*(clearance-d)*.15;n.vy+=dy/d*(clearance-d)*.15;}}const gx=Math.floor(n.x/cell),gy=Math.floor(n.y/cell);for(let dx=-1;dx<=1;dx++)for(let dy=-1;dy<=1;dy++){for(const m of grid.get((gx+dx)+','+(gy+dy))||[]){const x=n.x-m.x,y=n.y-m.y,d=Math.hypot(x,y)||.1;if(d<13){const f=(13-d)*.08;n.vx+=x/d*f;n.vy+=y/d*f;m.vx-=x/d*f;m.vy-=y/d*f;}}}const key=gx+','+gy;if(!grid.has(key))grid.set(key,[]);grid.get(key).push(n);}
 for(const e of edges){if(e.a.root||e.b.root||e.a.anchor!==e.b.anchor)continue;const x=e.b.x-e.a.x,y=e.b.y-e.a.y,d=Math.hypot(x,y)||1,f=(d-26)*.0015;e.a.vx+=x/d*f;e.a.vy+=y/d*f;e.b.vx-=x/d*f;e.b.vy-=y/d*f;}
 for(const n of nodes){if(n.root)continue;n.vx*=.7;n.vy*=.7;n.x+=n.vx;n.y+=n.vy;}
}
function nodeRadius(n){return n.root&&n.kind==='memory'?Math.max(n.radius,14/camera.z):n.radius;}
function drawCommunities(){
 for(const {root,members} of communities){
  const points=[root,...members].flatMap(n=>[0,1,2,3,4,5,6,7].map(i=>{const a=i*Math.PI/4,r=n.root?nodeRadius(n)+19:18;return {x:n.x+Math.cos(a)*r,y:n.y+Math.sin(a)*r};})).sort((a,b)=>a.x-b.x||a.y-b.y);
  const cross=(o,a,b)=>(a.x-o.x)*(b.y-o.y)-(a.y-o.y)*(b.x-o.x),half=ps=>{const h=[];for(const p of ps){while(h.length>1&&cross(h[h.length-2],h[h.length-1],p)<=0)h.pop();h.push(p);}return h;};
  const hull=[...half(points).slice(0,-1),...half([...points].reverse()).slice(0,-1)];if(hull.length<3)continue;
  ctx.beginPath();const last=hull[hull.length-1],first=hull[0];ctx.moveTo((last.x+first.x)/2,(last.y+first.y)/2);
  hull.forEach((p,i)=>{const next=hull[(i+1)%hull.length];ctx.quadraticCurveTo(p.x,p.y,(p.x+next.x)/2,(p.y+next.y)/2);});ctx.closePath();
  ctx.fillStyle=root.color;ctx.globalAlpha=hover&&!hover.sources.includes(root.id)?.018:.065;ctx.fill();
 }
 ctx.globalAlpha=1;
}
function draw(){
 if(!active)return;ctx.clearRect(0,0,width,height);ctx.save();ctx.translate(camera.x,camera.y);ctx.scale(camera.z,camera.z);
 drawCommunities();
 const connected=hover?new Set([hover.id,...edges.filter(e=>e.source===hover.id||e.target===hover.id).flatMap(e=>[e.source,e.target])]):null;
 for(const e of edges){const highlighted=hover&&(e.source===hover.id||e.target===hover.id);ctx.globalAlpha=highlighted ? .8 : connected ? .055 : e.kind==='contains' ? .12 : .28;ctx.strokeStyle=e.a.color;ctx.lineWidth=(highlighted?1.3:.65)/camera.z;ctx.setLineDash(e.kind==='same_table'?[4/camera.z,4/camera.z]:[]);ctx.beginPath();ctx.moveTo(e.a.x,e.a.y);ctx.lineTo(e.b.x,e.b.y);ctx.stroke();ctx.setLineDash([]);}
 for(const n of nodes){ctx.globalAlpha=connected&&!connected.has(n.id) ? .15 : 1;ctx.fillStyle=n.color;if(n.root){ctx.globalAlpha*=.12;ctx.beginPath();ctx.arc(n.x,n.y,nodeRadius(n)+9,0,Math.PI*2);ctx.fill();ctx.globalAlpha=connected&&!connected.has(n.id) ? .25 : 1;}ctx.beginPath();ctx.arc(n.x,n.y,nodeRadius(n)+(hover===n?1.5:0),0,Math.PI*2);ctx.fill();
  if(n.root&&n.kind==='memory'){
   const radius=nodeRadius(n)-2,portrait=portraits.get(n.avatarUrl);
   ctx.save();ctx.beginPath();ctx.arc(n.x,n.y,radius,0,Math.PI*2);ctx.clip();
   if(portrait?.ready){const img=portrait.image,side=Math.min(img.naturalWidth,img.naturalHeight);ctx.drawImage(img,(img.naturalWidth-side)/2,(img.naturalHeight-side)/2,side,side,n.x-radius,n.y-radius,radius*2,radius*2);}
   else{ctx.fillStyle=palette.background;ctx.fillRect(n.x-radius,n.y-radius,radius*2,radius*2);ctx.fillStyle=palette.text;ctx.font='600 14px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(n.label.split(/\s+/).slice(0,2).map(s=>s[0]).join(''),n.x,n.y);}
   ctx.restore();
  }
 }
 ctx.restore();ctx.globalAlpha=1;
 const labels=[];
 for(const n of nodes.filter(n=>n.root).sort((a,b)=>b.crossLinks-a.crossLinks||a.id.localeCompare(b.id))){
  if(width<600&&camera.z<.6&&nodes.filter(n=>n.root).length>6&&hover!==n)continue;
  if(connected&&!connected.has(n.id))continue;
  const x=n.x*camera.z+camera.x,y=n.y*camera.z+camera.y-nodeRadius(n)*camera.z-9;
  ctx.font='600 11px sans-serif';ctx.textAlign='center';ctx.fillStyle=palette.text;
  const label=n.label.length>28?n.label.slice(0,26)+'…':n.label,w=ctx.measureText(label).width;
  const rect={x:x-w/2-4,y:y-12,w:w+8,h:17};
  if(hover!==n&&labels.some(r=>r.x<rect.x+rect.w&&r.x+r.w>rect.x&&r.y<rect.y+rect.h&&r.y+r.h>rect.y))continue;
  labels.push(rect);ctx.fillText(label,x,y);
 }

}
function schedule(){if(frame||!active||document.hidden)return;frame=requestAnimationFrame(()=>{frame=0;if(!active||document.hidden)return;if(iterations<100&&!matchMedia('(prefers-reduced-motion: reduce)').matches){step();iterations++;schedule();}draw();});}
function resize(){if(!active)return;const r=map.getBoundingClientRect();width=r.width;height=r.height;const dpr=Math.min(2,devicePixelRatio||1);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);fit();}
new ResizeObserver(resize).observe(map);
function zoom(factor,x=width/2,y=height/2){const old=camera.z;camera.z=Math.max(.08,Math.min(8,old*factor));camera.x=x-(x-camera.x)*camera.z/old;camera.y=y-(y-camera.y)*camera.z/old;draw();}
function at(x,y){const wx=(x-camera.x)/camera.z,wy=(y-camera.y)/camera.z;let best=null,distance=Infinity;for(const n of nodes){const d=Math.hypot(n.x-wx,n.y-wy);if(d<Math.max(nodeRadius(n),7/camera.z)&&d<distance){best=n;distance=d;}}return best;}
canvas.addEventListener('wheel',e=>{e.preventDefault();const r=canvas.getBoundingClientRect();zoom(Math.exp(-e.deltaY*.0015),e.clientX-r.left,e.clientY-r.top);},{passive:false});
canvas.addEventListener('pointerdown',e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY};canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener('pointermove',e=>{const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;if(drag){camera.x+=e.clientX-drag.x;camera.y+=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;hint.hidden=true;draw();return;}hover=at(x,y);hint.hidden=!hover;if(hover){const names=hover.sources.map(s=>data.items.find(i=>i.ref===s)?.name).filter(Boolean);hint.textContent=(hover.root?hover.label:hover.label+' · '+names.join(', '))+(hover.root?(hover.crossLinks?' · '+hover.crossLinks+' cross-source links to '+hover.peers+' other sources in this sample':' · No recorded cross-source connection in this sample'):' · '+hover.degree+' displayed connections');hint.style.left=Math.max(6,Math.min(x+14,width-275))+'px';hint.style.top=Math.max(6,Math.min(y+12,height-100))+'px';}draw();});
canvas.addEventListener('pointerup',e=>{if(!drag)return;const moved=Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY);drag=null;if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);if(moved<5){const r=canvas.getBoundingClientRect(),n=at(e.clientX-r.left,e.clientY-r.top);if(n?.root)select(n.id);}});
canvas.addEventListener('pointercancel',()=>{drag=null;});canvas.addEventListener('pointerleave',()=>{hover=null;hint.hidden=true;draw();});
canvas.addEventListener('keydown',e=>{if(['+','=','-','0','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();if(e.key==='0')fit();else if(e.key==='+'||e.key==='=')zoom(1.25);else if(e.key==='-')zoom(.8);else{camera.x+=e.key==='ArrowLeft'?30:e.key==='ArrowRight'?-30:0;camera.y+=e.key==='ArrowUp'?30:e.key==='ArrowDown'?-30:0;draw();}}});
function status(){if(!data)return;const unavailable=[...new Set([...data.unavailable,...data.graph.unavailable])];feedback.textContent=(unavailable.length?'Some metadata is unavailable: '+unavailable.join(', ')+'. ':'')+`Observed ${fullTime(data.observed_at)}. This is a bounded sample; table columns are omitted. ${data.graph.total_sources>data.graph.shown_sources?'Showing '+data.graph.shown_sources+' of '+data.graph.total_sources+' sources. ':''}Shading shows source communities; dashed links identify the same table across graphs. Placement reflects displayed connections. An unlinked bank does not imply its agent has never used these documents.`;}
async function load(){
 const id=++ticket;controller?.abort();controller=new AbortController();loading=true;shell.querySelector('[data-brain-refresh]').disabled=true;shell.setAttribute('aria-busy','true');feedback.textContent='Reading knowledge inventory…';
 try{const r=await fetch('/api/calliope/gallery/brain'+(selected?'?ref='+encodeURIComponent(selected):''),{signal:controller.signal});const result=await r.json();if(!r.ok)throw Error(result.error?.message||'Knowledge inventory is unavailable');if(id!==ticket)return;data=result;selected=result.selected||'';paintList();colors();buildGraph();hero();status();}
 catch(e){if(id!==ticket||e.name==='AbortError')return;feedback.textContent=e.message||'Knowledge inventory is unavailable';if(!data){empty.hidden=false;empty.textContent='Could not load knowledge sources. Use Refresh to retry.';}}
 finally{if(id===ticket){loading=false;shell.querySelector('[data-brain-refresh]').disabled=false;shell.removeAttribute('aria-busy');}}
}
function select(ref){selected=ref;const url=new URL(location.href);if(ref)url.searchParams.set('brain_source',ref);else url.searchParams.delete('brain_source');history.replaceState({},'',url);load();}
list.addEventListener('click',e=>{const b=e.target.closest('[data-brain-source]');if(b)select(b.dataset.brainSource);});
shell.addEventListener('click',e=>{const z=e.target.closest('[data-brain-zoom]');if(z)zoom(Number(z.dataset.brainZoom));if(e.target.closest('[data-brain-fit]'))fit();if(e.target.closest('[data-brain-refresh]'))load();});
$('#q')?.addEventListener('input',()=>{if(active){paintList();buildGraph();queueMicrotask(hero);}});
window.addEventListener('gallery-view',e=>{active=e.detail==='brain';shell.hidden=!active;if(active){colors();resize();if(!data&&!loading)load();else{draw();schedule();}queueMicrotask(hero);}else{cancelAnimationFrame(frame);frame=0;}});
document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelAnimationFrame(frame);frame=0;}else schedule();});
new MutationObserver(()=>{if(active){colors();nodes.forEach(n=>n.color=palette[n.family|| (n.kind==='document'?'documents':n.kind)]||palette.entity);draw();}}).observe(document.documentElement,{attributes:true,attributeFilter:['style','class','data-theme']});
})();
