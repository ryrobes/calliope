// Meaning map: one diagram per scope (all areas, a business area, or one
// object) at three levels of detail, after EventCatalog's L1/L2/L3.
//   L1 · areas and the table groups inside them, plus neighbouring areas
//   L2 · groups open into their source tables and cubes, with probed joins
//   L3 · adds measures, suggestions and the apps and reports built on them
// Structure only: the graph comes from /api/calliope/meaning/graph and nothing
// here runs SQL. ELK (vendored, loaded on first use) does the layout.
import {escapeHtml as esc} from './meaning-present.mjs';

export const LEVELS=[
 {n:1,label:'Areas & groups',hint:'Business areas, their table groups and the areas they touch'},
 {n:2,label:'Sources & cubes',hint:'Each group opens into its source tables and cubes, with probed joins'},
 {n:3,label:'Measures & work',hint:'Adds measures, suggestions and the apps and reports built on them'}];
export const TAGS={area:'Area',group:'Group',relation:'Source',cube:'Cube',metric:'Measure',idea:'Suggested measure',breakdown:'Suggested breakdown',artifact:'App',suggestions:'Suggestions'};
export const LEGEND=[['relation','Sources',2],['cube','Cubes',2],['metric','Measures',3],['suggestion','Suggestions',3],['artifact','Apps & reports',3]];
export const LEVEL_OF={area:1,group:1,relation:2,cube:2,metric:3,idea:3,breakdown:3,artifact:3};
export const legendKey=kind=>kind==='idea'||kind==='breakdown'?'suggestion':kind;
const array=v=>Array.isArray(v)?v:[];
// Card height follows its content: tag, a title of one or two lines, an
// optional reading (measures) and a one-line subtitle.
const WIDTH={relation:224,cube:224,metric:216,artifact:216,idea:216,breakdown:216,group:236,area:236};
export const HEADER=n=>n.subtitle?78:56;
function sized(n){
 const w=WIDTH[n.kind]||220;
 if(n.container)return {...n,w,h:96};
 const lines=(text,font,max)=>text?Math.min(max,Math.max(1,Math.ceil(textWidth(text,font)/(w-34)))):0;
 const prose=n.kind==='relation'||n.kind==='group';
 return {...n,w,h:20+13+lines(n.title,'600 13px sans-serif',2)*17+(n.value?25:0)
  +(n.subtitle?3+lines(n.subtitle,'400 11.5px sans-serif',prose?2:1)*15:0)+(n.meta?17:0)};
}

// What Meaning inferred a table to be. Tags, glyphs and join verbs come from
// this, so the map reads as business structure rather than table names.
export const ROLES={event:'Event',entity:'Entity',junction:'Link table',lookup:'Lookup',summary:'Summary'};
export function relationRole(n){return n?.relation_kind==='aggregate_or_cube'?'summary':ROLES[n?.object_role]?n.object_role:'';}
export function grainText(n){const g=String(n?.grain||'').trim().replace(/\.$/,'');return g?g.replace(/^one row represents\s+/i,'Each row: '):'';}
function joinVerb(from,to){
 const a=relationRole(from),b=relationRole(to);
 return b==='lookup'?'coded by':a==='junction'?'part of':b==='entity'?'belongs to':a==='summary'||b==='summary'?'summarized in':'linked to';
}
export function compact(value){const x=Number(value);return value==null||value===''||!Number.isFinite(x)?String(value??''):new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1}).format(x);}
function columns(e){const v=e?.evidence||{};return v.from_column?(v.from_column===v.to_column||!v.to_column?v.from_column:`${v.from_column} → ${v.to_column}`):'';}
export function joinLabel(e,byId){return edgeLabel(e,byId);}
function edgeLabel(e,byId){
 if(e.label!=='joins to')return e.label;
 const verb=joinVerb(byId?.get(e.from),byId?.get(e.to)),col=columns(e);
 return col?`${verb} · ${col}`:verb;
}

// Fold parallel links into one edge per pair, keeping every piece of evidence.
function fold(list){
 const by=new Map();
 for(const e of list){const key=e.from+'\n'+e.to,old=by.get(key);if(!old){by.set(key,{...e,items:[e]});continue;}old.items.push(e);}
 return [...by.values()].map(e=>{const labels=[...new Set(e.items.map(x=>x.label))];return {...e,id:e.items.length>1?'fold:'+e.from+'>'+e.to:e.id,
  label:labels.length===1?labels[0]:`${e.items.length} links`,bridged:e.items.every(x=>x.bridged),
  state:e.items.some(x=>x.state==='verified'||x.state==='declared'||x.state==='published')?e.items.find(x=>['verified','declared','published'].includes(x.state)).state:e.state};});
}

// Remove legend-hidden nodes but keep what they connected linked through them.
function contract(edges,hiddenIds,title){
 let out=edges;
 for(const h of hiddenIds){
  const ins=out.filter(e=>e.to===h&&e.from!==h),outs=out.filter(e=>e.from===h&&e.to!==h);
  out=out.filter(e=>e.from!==h&&e.to!==h);
  for(const a of ins)for(const b of outs)if(a.from!==b.to)out.push({id:`via:${a.id}:${b.id}`,from:a.from,to:b.to,label:'via '+title(h),state:'bridged',bridged:true,evidence:{basis:`Linked through ${title(h)}, hidden by the legend`}});
 }
 return out;
}

export function areaGroups(graph){
 const root=array(graph.nodes).find(n=>n.kind==='area');if(!root)return [];
 const members=new Set(array(graph.edges).filter(e=>e.from===root.id&&e.label==='includes').map(e=>e.to)),ids=new Set(array(graph.nodes).map(n=>n.id));
 const groups=array(graph.groups).map(g=>({...g,members:array(g.members).filter(m=>ids.has(m))})).filter(g=>g.members.length),placed=new Set(groups.flatMap(g=>g.members));
 const loose=[...members].filter(m=>!placed.has(m));
 if(loose.length&&!groups.length){
  // No published grouping: group by database schema, and say so.
  const by=new Map();for(const m of loose){const s=m.replace(/^relation:/,'').split('.').slice(0,-1).join('.')||'default';by.has(s)||by.set(s,[]);by.get(s).push(m);}
  return [...by].map(([s,list])=>({id:'group:schema:'+s,title:s,description:'Grouped by database schema; this area has no published grouping.',members:list,basis:'Database schema'}));
 }
 if(loose.length)groups.push({id:'group:other:'+root.id,title:'Other sources',description:'Members of this area outside its published groupings.',members:loose,basis:'Published area membership'});
 return groups;
}

// Area scope (also the base of an object's map).
export function areaModel(graph,{level=1,hidden=new Set(),target=''}={}){
 const nodes=array(graph.nodes),edges=array(graph.edges),root=nodes.find(n=>n.kind==='area');
 if(!root)return {nodes:[],edges:[]};
 const byId=new Map(nodes.map(n=>[n.id,n])),fromRoot=new Map(edges.filter(e=>e.from===root.id).map(e=>[e.to,e]));
 const groups=areaGroups(graph),groupOf=new Map();for(const g of groups)for(const m of g.members)groupOf.set(m,g.id);
 const inside=n=>{const tie=fromRoot.get(n.id);return n.kind==='relation'?tie?.label==='includes':n.kind==='cube'?tie?.label==='analyzed through':n.kind==='metric'?tie?.label==='measured by':!!tie;};
 const atLevel=n=>n.id===target||(LEVEL_OF[n.kind]||9)<=level;
 const legendHidden=n=>n.id!==target&&hidden.has(legendKey(n.kind));
 const levelNodes=nodes.filter(n=>n.kind!=='area'&&atLevel(n)),present=new Set(levelNodes.map(n=>n.id));
 // Graph edges at this level; at L1 an object's links rise to its group.
 const lift=id=>present.has(id)?id:level===1&&groupOf.has(id)?groupOf.get(id):null;
 let links=[];
 for(const e of edges){if(e.from===root.id)continue;const a=lift(e.from),b=lift(e.to);if(a&&b&&a!==b)links.push({...e,from:a,to:b,label:edgeLabel(e,byId),detail:e});}
 const hiddenIds=levelNodes.filter(legendHidden).map(n=>n.id);
 links=contract(links,hiddenIds,id=>byId.get(id)?.title||id);
 const visible=levelNodes.filter(n=>!legendHidden(n));
 // Containment: the area box, its groups, a suggestions box; outside work sits beside it.
 const upstream=id=>links.filter(e=>e.to===id).map(e=>e.from);
 const home=n=>{
  if(!inside(n)&&n.kind!=='idea'&&n.kind!=='breakdown'&&n.kind!=='artifact')return 'root';
  if(n.kind==='idea'||n.kind==='breakdown')return 'suggestions:'+root.id;
  if(level>=2&&groupOf.has(n.id))return groupOf.get(n.id);
  if(level>=2&&(n.kind==='cube'||n.kind==='metric')){const gs=new Set(upstream(n.id).map(id=>groupOf.get(id)));if(gs.size===1&&!gs.has(undefined))return [...gs][0];}
  return root.id;};
 const out=[sized({id:root.id,kind:'area',title:root.title,subtitle:root.description||root.subtitle||'',tagNote:root.subtitle||'',container:true,parent:'root',viewing:!target,source:root.id})];
 const kids=new Set();
 for(const n of visible){const parent=home(n);kids.add(parent);out.push(sized({id:n.id,kind:n.kind,title:n.title,subtitle:subtitleOf(n),value:valueOf(n),meta:metaOf(n),
  role:n.kind==='relation'?relationRole(n):'',support:n.kind==='relation'&&!!n.subject_role&&n.subject_role!=='core',status:n.status,parent,viewing:n.id===target,source:n.id,external:parent==='root'}));}
 for(const g of groups){
  const list=g.members.map(id=>byId.get(id)).filter(Boolean),rows=list.reduce((s,n)=>s+(Number(n.preview?.value)||0),0),cubes=list.filter(n=>n.kind==='cube').length,sources=list.length-cubes;
  const counts=[sources&&`${sources} source${sources===1?'':'s'}`,cubes&&`${cubes} cube${cubes===1?'':'s'}`,rows&&compact(rows)+' rows'].filter(Boolean).join(' · ');
  // The group's inferred purpose leads; its size is the footnote.
  out.push(sized({id:g.id,kind:'group',title:g.title,description:g.description,subtitle:g.description||counts,meta:g.description?counts:'',tagNote:counts,parent:root.id,container:level>=2&&kids.has(g.id),members:g.members}));
 }
 if(kids.has('suggestions:'+root.id))out.push({id:'suggestions:'+root.id,kind:'suggestions',title:'Suggested measures & breakdowns',subtitle:'Proposals from the published dossier, not definitions',parent:root.id,container:true,w:240,h:64});
 // Neighbouring areas and their ties into this one.
 const ids=new Set(out.map(n=>n.id));
 for(const n of array(graph.neighbors)){
  out.push(sized({id:n.id,kind:'area',title:n.title,subtitle:n.subtitle||'',parent:'root',external:true,source:n.id,area:n.area,ties:n.ties}));
  for(const t of array(n.ties)){const local=t.local&&ids.has(t.local)?t.local:t.local&&level===1&&groupOf.has(t.local)?groupOf.get(t.local):root.id;
   links.push({id:`tie:${n.id}:${local}:${t.label}`,from:local,to:n.id,label:t.label,state:t.state,evidence:t});}
 }
 const parent=new Map(out.map(n=>[n.id,n.parent]));
 const within=id=>{for(let p=id;p&&p!=='root';p=parent.get(p))if(p===root.id)return true;return false;};
 const final=fold(links.filter(e=>parent.has(e.from)&&parent.has(e.to))).map(e=>({...e,crossing:within(e.from)!==within(e.to)}));
 return {nodes:out,edges:final,root:root.id};
}

function subtitleOf(n){
 if(n.kind==='relation')return grainText(n)||[n.source||n.subtitle,n.preview&&compact(n.preview.value)+' rows'].filter(Boolean).join(' · ');
 if(n.kind==='cube')return n.grain?'Grain · '+n.grain:n.source||'';
 if(n.kind==='metric')return n.preview?.label||n.subtitle||'';
 return n.subtitle||'';
}
function metaOf(n){
 return n.kind==='relation'&&grainText(n)?[n.source,n.preview&&compact(n.preview.value)+' rows'].filter(Boolean).join(' · '):'';
}
function valueOf(n){
 if(n.kind==='metric')return n.preview?compact(n.preview.value):'';
 return '';
}

// One object's neighbourhood, nested in the groups it belongs to.
export function objectModel(graph,{target,level=2,hidden=new Set()}){
 const full=areaModel(graph,{level:Math.max(2,level),hidden,target}),kind=new Map(full.nodes.map(n=>[n.id,n.kind]));
 if(!kind.has(target))return {nodes:[],edges:[]};
 const keep=new Set([target]);
 for(const e of full.edges){if(e.from===target)keep.add(e.to);if(e.to===target)keep.add(e.from);}
 if(level>=3)for(const id of [...keep])if(id!==target&&['cube','metric'].includes(kind.get(id)))for(const e of full.edges)if(e.from===id)keep.add(e.to);
 const parent=new Map(full.nodes.map(n=>[n.id,n.parent]));
 for(const id of [...keep])for(let p=parent.get(id);p&&p!=='root';p=parent.get(p))keep.add(p);
 const nodes=full.nodes.filter(n=>keep.has(n.id)).map(n=>({...n,viewing:n.id===target,container:n.container&&full.nodes.some(c=>c.parent===n.id&&keep.has(c.id))})).map(n=>n.container||!['group','suggestions'].includes(n.kind)?n:sized({...n,container:false}));
 return {nodes,edges:full.edges.filter(e=>keep.has(e.from)&&keep.has(e.to)),root:full.root};
}

// Every published area, with its groups inside, and the ties between areas.
export function landscapeModel(graph){
 const out=[],areas=array(graph.nodes).filter(n=>n.kind==='area');
 for(const a of areas){
  const groups=array(a.groups);
  out.push(sized({id:a.id,kind:'area',title:a.title,subtitle:a.subtitle||'',parent:'root',container:groups.length>0,source:a.id,area:a.area}));
  groups.forEach((g,i)=>out.push(sized({id:g.id||`${a.id}#${i}`,kind:'group',title:g.title,subtitle:`${g.size} source${g.size===1?'':'s'}`,parent:a.id,area:a.area,group:g.id})));
 }
 const ids=new Set(out.map(n=>n.id));
 return {nodes:out,edges:array(graph.area_links).filter(e=>ids.has(e.from)&&ids.has(e.to)).map(e=>({...e,crossing:true})),root:''};
}

export function levelsFor(scope){
 return LEVELS.map(l=>({...l,available:scope==='landscape'?l.n===1:scope==='object'?l.n>1:true,
  reason:scope==='landscape'&&l.n>1?'Open a business area to see its sources and measures':scope==='object'&&l.n===1?'An object’s map starts at its sources':''}));
}

// ELK graph. Layered layout stacks unconnected siblings in one tall column,
// so areas, the suggestions box and the canvas pack their unconnected parts
// into rows (aiming for a screen-shaped block) held by invisible ordering
// edges. Sources inside a group keep their natural stack.
const ROOT={'elk.algorithm':'layered','elk.direction':'RIGHT','elk.hierarchyHandling':'INCLUDE_CHILDREN','elk.edgeRouting':'ORTHOGONAL',
 'elk.json.shapeCoords':'ROOT','elk.json.edgeCoords':'ROOT','elk.layered.spacing.nodeNodeBetweenLayers':'56','elk.spacing.nodeNode':'22',
 'elk.layered.spacing.edgeNodeBetweenLayers':'16','elk.spacing.edgeNode':'14','elk.spacing.edgeEdge':'10','elk.spacing.edgeLabel':'4',
 'elk.edgeLabels.placement':'CENTER','elk.layered.nodePlacement.strategy':'NETWORK_SIMPLEX','elk.layered.considerModelOrder.strategy':'NODES_AND_EDGES'};
const GAP_X=56,GAP_Y=22,PAD_X=32,PAD_Y=74;
let measureContext;
export function textWidth(text,font='600 11px sans-serif'){
 try{measureContext??=document.createElement('canvas').getContext('2d');measureContext.font=font;return Math.ceil(measureContext.measureText(text).width);}catch{return Math.ceil(String(text).length*6.4);}
}
export function elkGraph(model,{font='sans-serif',aspect=1.8}={}){
 const kids=new Map(),node=new Map(model.nodes.map(n=>[n.id,n])),parent=new Map(model.nodes.map(n=>[n.id,n.parent||'root']));
 for(const n of model.nodes){const p=n.parent||'root';kids.has(p)||kids.set(p,[]);kids.get(p).push(n);}
 const edges=model.edges,grid=[],packs=new Map(),sizes=new Map();
 const arranged=id=>id==='root'||['area','suggestions'].includes(node.get(id)?.kind);
 // Rows of connected components among a container's children.
 function pack(id){
  if(packs.has(id))return packs.get(id);
  const list=kids.get(id)||[],link=new Map(list.map(c=>[c.id,new Set()])),into=new Map(list.map(c=>[c.id,0])),from=new Map(list.map(c=>[c.id,0]));
  const up=nid=>{for(let p=nid;p&&p!=='root';p=parent.get(p))if(link.has(p))return p;return null;};
  for(const e of edges){const a=up(e.from),b=up(e.to);if(a&&b&&a!==b){link.get(a).add(b);link.get(b).add(a);from.set(a,from.get(a)+1);into.set(b,into.get(b)+1);}}
  const seen=new Set(),comps=[];
  for(const c of list){if(seen.has(c.id))continue;const comp=[],stack=[c.id];seen.add(c.id);while(stack.length){const x=stack.pop();comp.push(x);for(const y of link.get(x))if(!seen.has(y)){seen.add(y);stack.push(y);}}
   comp.sort((a,b)=>list.findIndex(c=>c.id===a)-list.findIndex(c=>c.id===b));
   const dims=comp.map(size);comps.push({ids:comp,heads:comp.filter(x=>!into.get(x)),tail:[...comp].reverse().find(x=>!from.get(x))||comp.at(-1),
    w:dims.reduce((s,d)=>s+d.w,0)+GAP_X*(comp.length-1),h:Math.max(...dims.map(d=>d.h))});}
  let rows=[comps];
  if(arranged(id)&&comps.length>1){
   // Try every row width the components allow; keep the block closest to
   // the wanted shape (the viewport's, a little taller inside boxes).
   const want=id==='root'?aspect:aspect*.8,fill=limit=>{const out=[[]];let width=0;for(const c of comps){if(out.at(-1).length&&width+GAP_X+c.w>limit){out.push([]);width=0;}out.at(-1).push(c);width+=(width?GAP_X:0)+c.w;}return out;};
   const shape=r=>({w:Math.max(...r.map(x=>x.reduce((s,c)=>s+c.w,0)+GAP_X*(x.length-1))),h:r.reduce((s,x)=>s+Math.max(...x.map(c=>c.h)),0)+GAP_Y*(r.length-1)});
   const limits=new Set();for(let i=0;i<comps.length;i++){let w=-GAP_X;for(let j=i;j<comps.length;j++){w+=GAP_X+comps[j].w;limits.add(w);}}
   let best=null;
   for(const limit of limits){const r=fill(limit),{w,h}=shape(r),score=Math.abs(Math.log(w/h/want))+.02*r.length;if(!best||score<best.score)best={rows:r,score};}
   rows=best.rows;
  }else rows=comps.map(c=>[c]);
  packs.set(id,rows);return rows;
 }
 function size(id){
  if(sizes.has(id))return sizes.get(id);
  const n=node.get(id),list=kids.get(id)||[];let result;
  if(!list.length)result={w:n.w,h:n.h};
  else{const rows=pack(id);result={w:Math.max(...rows.map(r=>r.reduce((s,c)=>s+c.w,0)+GAP_X*(r.length-1)))+PAD_X,h:rows.reduce((s,r)=>s+Math.max(...r.map(c=>c.h)),0)+GAP_Y*(rows.length-1)+PAD_Y};}
  sizes.set(id,result);return result;
 }
 function arrange(id){
  if(!arranged(id))return;
  // Sibling-to-sibling ordering moves a whole component (an edge between
  // two boxes keeps every child of the second to the right of the first).
  for(const row of pack(id))row.forEach((c,i)=>{if(i)for(const head of c.heads.length?c.heads:[c.ids[0]])grid.push({id:`grid:${id}:${head}`,sources:[row[i-1].tail],targets:[head]});});
 }
 const make=n=>{
  const children=(kids.get(n.id)||[]).map(make);arrange(n.id);
  if(!children.length)return {id:n.id,width:n.w,height:n.h};
  const header=Math.max(textWidth(n.title||'',`650 14px ${font}`)+(n.viewing?96:40),220);
  return {id:n.id,children,layoutOptions:{'elk.padding':`[top=${HEADER(n)},left=16,bottom=16,right=16]`,'elk.nodeSize.constraints':'[MINIMUM_SIZE]','elk.nodeSize.minimum':`(${header},96)`}};
 };
 const children=(kids.get('root')||[]).map(make);arrange('root');
 return {id:'root',layoutOptions:ROOT,children,
  edges:[...edges.map(e=>({id:e.id,sources:[e.from],targets:[e.to],labels:e.label?[{text:e.label,width:textWidth(e.label,`500 11px ${font}`)+12,height:18}]:[]})),...grid]};
}

let engine;
function elk(){
 if(!engine)engine=new Promise((resolve,reject)=>{
  if(window.ELK)return resolve(new window.ELK());
  const script=document.createElement('script');
  script.src=new URL('./meaning-elk.js'+(document.body.dataset.meaningVersion?'?v='+document.body.dataset.meaningVersion:''),import.meta.url).href;
  script.onload=()=>window.ELK?resolve(new window.ELK()):reject(new Error('The map layout engine did not start'));
  script.onerror=()=>{engine=null;reject(new Error('The map layout engine could not load'));};
  document.head.append(script);
 });
 return engine;
}

function structure(model){return JSON.stringify([model.nodes.map(n=>[n.id,n.parent,n.container,n.w,n.h]),model.edges.map(e=>[e.id,e.from,e.to,e.label])]);}
function roundedPath(points,r=7){
 if(points.length<2)return '';
 let d=`M${points[0].x},${points[0].y}`;
 for(let i=1;i<points.length-1;i++){
  const p=points[i-1],c=points[i],n=points[i+1],d1=Math.hypot(c.x-p.x,c.y-p.y),d2=Math.hypot(n.x-c.x,n.y-c.y),k=Math.min(r,d1/2,d2/2);
  const a={x:c.x+(p.x-c.x)*k/(d1||1),y:c.y+(p.y-c.y)*k/(d1||1)},b={x:c.x+(n.x-c.x)*k/(d2||1),y:c.y+(n.y-c.y)*k/(d2||1)};
  d+=` L${a.x},${a.y} Q${c.x},${c.y} ${b.x},${b.y}`;
 }
 const last=points[points.length-1];return d+` L${last.x},${last.y}`;
}
function arrow(points){
 const end=points[points.length-1],prev=points[points.length-2],dx=end.x-prev.x,dy=end.y-prev.y,len=Math.hypot(dx,dy)||1,ux=dx/len,uy=dy/len,s=6;
 return `M${end.x},${end.y} L${end.x-ux*s*1.5-uy*s*0.7},${end.y-uy*s*1.5+ux*s*0.7} L${end.x-ux*s*1.5+uy*s*0.7},${end.y-uy*s*1.5-ux*s*0.7} Z`;
}

const ROLE_ICONS={event:'<circle cx="5" cy="5" r="3.6"/><path d="M5 3v2.2l1.4.9"/>',entity:'<circle cx="5" cy="3.4" r="1.8"/><path d="M1.8 8.7a3.2 3.2 0 0 1 6.4 0"/>',
 junction:'<circle cx="3.6" cy="5" r="2.2"/><circle cx="6.4" cy="5" r="2.2"/>',lookup:'<path d="M2 2.5h6M2 5h6M2 7.5h4"/>',summary:'<path d="M7.6 2H2.6l2.6 3-2.6 3h5"/>'};
function nodeMarkup(n){
 const label=n.role?ROLES[n.role]:TAGS[n.kind]||n.kind,icon=n.role?`<svg viewBox="0 0 10 10" aria-hidden="true">${ROLE_ICONS[n.role]}</svg>`:'';
 const tag=`<span class="dg-tag">${icon}${esc(n.external&&n.kind!=='area'?label+' · outside':label)}${n.container&&n.tagNote?`<i> · ${esc(n.tagNote)}</i>`:''}</span>`,viewing=n.viewing?'<em class="dg-viewing">Viewing</em>':'';
 if(n.container)return `<button type="button" class="dg-box-head" data-dg-node="${esc(n.id)}" aria-label="${esc(label+' '+n.title)}">${tag}${viewing}<strong>${esc(n.title)}</strong>${n.subtitle?`<small>${esc(n.subtitle)}</small>`:''}</button>`;
 return `${tag}${viewing}<strong>${esc(n.title)}</strong>${n.value?`<b class="dg-value">${esc(n.value)}</b>`:''}${n.subtitle?`<small>${esc(n.subtitle)}</small>`:''}${n.meta?`<span class="dg-meta">${esc(n.meta)}</span>`:''}`;
}

export function mountDiagram(host,callbacks={}){
 host.classList.add('dg-canvas');
 host.innerHTML=`<div class="dg-viewport" tabindex="0" role="group" aria-roledescription="diagram"><div class="dg-world"><svg class="dg-edges" aria-hidden="true"></svg><div class="dg-layer"></div></div></div>
<div class="dg-controls" role="group" aria-label="Map view"><button type="button" data-map="in" aria-label="Zoom in" title="Zoom in">+</button><button type="button" data-map="out" aria-label="Zoom out" title="Zoom out">−</button><button type="button" data-map="fit" aria-label="Fit map to view" title="Fit to view (0)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg></button></div>
<p class="dg-status" role="status" hidden></p>`;
 const viewport=host.querySelector('.dg-viewport'),world=host.querySelector('.dg-world'),svg=host.querySelector('.dg-edges'),layer=host.querySelector('.dg-layer'),status=host.querySelector('.dg-status');
 let cam={x:0,y:0,k:1},props={},model={nodes:[],edges:[]},key='',seq=0,boxes=new Map(),els=new Map(),moved=false,lastScope='',hover='',destroyed=false;
 const font=getComputedStyle(host).fontFamily||'sans-serif';
 const say=text=>{status.hidden=!text;status.textContent=text||'';};
 const apply=glide=>{world.classList.toggle('dg-glide',!!glide);world.style.transform=`translate(${cam.x}px,${cam.y}px) scale(${cam.k})`;};
 function bounds(ids){
  const list=ids?ids.map(id=>boxes.get(id)).filter(Boolean):[...boxes.values()];if(!list.length)return null;
  const x=Math.min(...list.map(b=>b.x)),y=Math.min(...list.map(b=>b.y));return {x,y,w:Math.max(...list.map(b=>b.x+b.w))-x,h:Math.max(...list.map(b=>b.y+b.h))-y};
 }
 function fit(glide=true,ids=null,max=1){
  // Keep what we frame clear of an open inspector beside the canvas.
  const b=bounds(ids),inset=props.picked&&viewport.clientWidth>760?Math.min(372,viewport.clientWidth*.4):0,vw=viewport.clientWidth-inset,vh=viewport.clientHeight;if(!b||vw<=0||!vh)return;
  const k=Math.max(.15,Math.min(max,(vw-56)/b.w,(vh-56)/b.h));cam={k,x:(vw-b.w*k)/2-b.x*k,y:(vh-b.h*k)/2-b.y*k};apply(glide);
 }
 function zoom(factor,cx=viewport.clientWidth/2,cy=viewport.clientHeight/2){
  const k=Math.max(.15,Math.min(2.5,cam.k*factor));cam={k,x:cx-(cx-cam.x)*k/cam.k,y:cy-(cy-cam.y)*k/cam.k};moved=true;apply(false);
 }
 // A box's contents count as near it, so picking a group keeps its sources lit.
 function adjacency(id){
  const near=new Set([id]),lit=new Set(),parent=new Map(model.nodes.map(n=>[n.id,n.parent]));
  for(const n of model.nodes)for(let p=n.parent;p&&p!=='root';p=parent.get(p))if(p===id){near.add(n.id);break;}
  const inner=new Set(near);
  for(const e of model.edges)if(inner.has(e.from)||inner.has(e.to)){near.add(e.from);near.add(e.to);lit.add(e.id);}
  return {near,lit};
 }
 function decorate(){
  const focus=hover||props.picked||'',q=(props.query||'').trim().toLowerCase(),{near,lit}=focus?adjacency(focus):{near:new Set(),lit:new Set()};
  host.classList.toggle('dg-focused',!!focus&&els.has(focus));host.classList.toggle('dg-searching',!!q);host.classList.toggle('dg-crossing',!!props.crossing);
  for(const [id,el] of els){const n=el._node;el.classList.toggle('is-picked',id===props.picked);el.classList.toggle('is-near',near.has(id));
   el.classList.toggle('is-match',!!q&&`${n.title} ${n.subtitle||''}`.toLowerCase().includes(q));}
  for(const g of svg.querySelectorAll('.dg-edge'))g.classList.toggle('is-lit',lit.has(g.dataset.id));
 }
 function element(n){
  const el=document.createElement(n.container?'div':'button');
  if(!n.container){el.type='button';el.dataset.dgNode=n.id;el.setAttribute('aria-label',`${TAGS[n.kind]||n.kind} ${n.title}${n.value?' · '+n.value:''}`);}
  el.className=n.container?'dg-box':'dg-node';el.dataset.kind=n.kind;el.dataset.shape=n.container?'box':'leaf';el.innerHTML=nodeMarkup(n);el._node=n;
  if(n.external)el.dataset.external='';if(n.status)el.dataset.status=n.status;
  if(n.role)el.dataset.role=n.role;if(n.support)el.dataset.support='';
  return el;
 }
 // Object constancy, after the diagrams this map is patterned on: what you
 // acted on keeps its place on screen while the layout changes around it, new
 // parts grow out of the container they belong to, departing parts fold back
 // into theirs, and lines draw only once the shapes have settled.
 const MORPH_MS=550,DASHED=new Set(['association','unverified','proposed','implied','bridged']);
 const reduced=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
 const place=(el,b)=>Object.assign(el.style,{left:b.x+'px',top:b.y+'px',width:b.w+'px',height:b.h+'px'});
 function nearest(id,parentOf,has){for(let p=parentOf.get(id);p&&p!=='root';p=parentOf.get(p))if(has(p))return p;return null;}
 // A small box near the top of a container: where its new contents start
 // growing from, and where departing contents shrink to.
 function seed(box,within){
  const w=Math.min(box.w,Math.max(28,within.w*.42)),h=Math.min(box.h,Math.max(18,within.h*.3));
  return {x:within.x+(within.w-w)/2,y:within.y+Math.max(0,Math.min(within.h-h,HEADER({subtitle:1}))),w,h};
 }
 function edgesMarkup(result){
  const byId=new Map(model.edges.map(e=>[e.id,e]));
  return (result.edges||[]).filter(e=>byId.has(e.id)).map(e=>{
   const m=byId.get(e.id),paths=(e.sections||[]).map(s=>[s.startPoint,...(s.bendPoints||[]),s.endPoint]).filter(p=>p.length>1);if(!paths.length)return '';
   const label=(e.labels||[])[0];
   return `<g class="dg-edge" data-id="${esc(e.id)}" data-state="${esc(m.state||'')}"${m.crossing?' data-crossing=""':''}${m.bridged?' data-bridged=""':''}>${paths.map(p=>`<path class="dg-line" d="${roundedPath(p)}"/>`).join('')}<path class="dg-arrow" d="${arrow(paths[paths.length-1])}"/>${label?`<g class="dg-label"><rect x="${label.x}" y="${label.y}" width="${label.width}" height="${label.height}" rx="2"/><text x="${label.x+label.width/2}" y="${label.y+label.height/2}">${esc(m.label)}</text></g>`:''}<title>${esc(m.label+(m.evidence?.basis?' — '+m.evidence.basis:''))}</title></g>`;
  }).join('');
 }
 function revealEdges(animate){
  svg.classList.remove('dg-edges-hidden','dg-edges-in');void svg.getBoundingClientRect();svg.classList.add('dg-edges-in');
  if(!animate||!svg.animate)return;
  for(const g of svg.querySelectorAll('.dg-edge')){
   if(DASHED.has(g.dataset.state))continue;
   for(const path of g.querySelectorAll('.dg-line')){
    const length=path.getTotalLength?.()||0;if(!length)continue;
    path.animate([{strokeDasharray:`${length}`,strokeDashoffset:length},{strokeDasharray:`${length}`,strokeDashoffset:0}],{duration:520,easing:'cubic-bezier(.2,.75,.2,1)'});
   }
  }
 }
 // Lay the new geometry out; onStart runs in the frame the morph begins, so
 // a camera move started there shares the cards' timing exactly.
 function draw(result,before,onStart){
  const prev=boxes,animate=prev.size>0&&!reduced();
  boxes=new Map();
  const walk=(n,depth)=>{if(n.id!=='root')boxes.set(n.id,{x:n.x,y:n.y,w:n.width,h:n.height,depth});for(const c of n.children||[])walk(c,depth+1);};walk(result,-1);
  const oldParent=new Map((before?.nodes||[]).map(n=>[n.id,n.parent])),newParent=new Map(model.nodes.map(n=>[n.id,n.parent]));
  layer.classList.toggle('dg-instant',animate);
  const seen=new Set(),ends=[],leaving=[];
  for(const n of model.nodes){
   const b=boxes.get(n.id);if(!b)continue;seen.add(n.id);
   let el=els.get(n.id),start=prev.get(n.id);const shape=n.container?'box':'leaf';
   if(el&&el.dataset.shape!==shape){const old=el;el=element(n);old.replaceWith(el);}
   else if(el){el.innerHTML=nodeMarkup(n);el._node=n;el.dataset.kind=n.kind;}
   else{
    el=element(n);layer.append(el);el.classList.add('dg-entering');
    const from=nearest(n.id,newParent,id=>prev.has(id));
    start=from?seed(b,prev.get(from)):null;
   }
   els.set(n.id,el);el.style.zIndex=String(b.depth+1);
   place(el,animate&&start?start:b);
   ends.push([el,b]);
  }
  for(const [id,el] of els)if(!seen.has(id)){
   els.delete(id);
   const into=nearest(id,oldParent,i=>boxes.has(i));
   leaving.push([el,into&&prev.get(id)?seed(prev.get(id),boxes.get(into)):null]);
  }
  // Containers first so cards and edges sit above their boxes.
  [...els.values()].sort((a,b)=>(+a.style.zIndex)-(+b.style.zIndex)).forEach(el=>layer.append(el));
  const width=Math.max(0,...[...boxes.values()].map(b=>b.x+b.w))+40,height=Math.max(0,...[...boxes.values()].map(b=>b.y+b.h))+40;
  svg.setAttribute('width',width);svg.setAttribute('height',height);svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
  svg.innerHTML=edgesMarkup(result);
  if(!animate){
   for(const [el] of ends)el.classList.remove('dg-entering');
   for(const [el] of leaving)el.remove();
   onStart?.(false);revealEdges(false);return;
  }
  svg.classList.add('dg-edges-hidden');
  void layer.offsetWidth;
  requestAnimationFrame(()=>{
   layer.classList.remove('dg-instant');
   for(const [el,b] of ends){place(el,b);el.classList.remove('dg-entering');}
   for(const [el,to] of leaving){el.classList.add('dg-leaving');if(to)place(el,to);setTimeout(()=>el.remove(),MORPH_MS+60);}
   onStart?.(true);
   setTimeout(()=>{if(!destroyed)revealEdges(true);},MORPH_MS-80);
  });
 }
 async function update(next){
  const before=model;
  props=next;model=next.model||{nodes:[],edges:[]};
  viewport.setAttribute('aria-label',next.label||'Meaning map');
  const k=structure(model);
  if(k!==key){
   key=k;const mine=++seq;
   if(!model.nodes.length){draw({children:[],edges:[]},before);say(next.empty||'Nothing to show at this level.');decorate();return;}
   const slow=setTimeout(()=>say('Laying out the map…'),160);
   let result;try{result=await (await elk()).layout(elkGraph(model,{font,aspect:Math.max(1,Math.min(3.2,next.aspect||1.8))}));}catch(e){clearTimeout(slow);if(mine===seq)say(e.message||'The map could not be laid out.');return;}
   clearTimeout(slow);if(mine!==seq||destroyed)return;say('');
   // Anchor: the object being drilled into, else the picked one, else the
   // area itself. Its top-left corner stays where it was while things move.
   const ids=new Set(model.nodes.map(n=>n.id));
   const anchor=[next.focus,props.picked,model.root].find(id=>id&&ids.has(id)&&boxes.has(id));
   const was=anchor?{...boxes.get(anchor)}:null;
   const scope=next.scope+'|'+(next.level||''),scopeChanged=next.scope!==lastScope.split('|')[0];
   const settle=()=>{
    if(mine!==seq||destroyed)return;
    // A framed object counts as positioned: the canvas's first resize must not re-fit everything.
    if(next.focus&&boxes.has(next.focus)){fit(true,[next.focus],1);moved=true;}
    else if(scopeChanged||!moved){fit(lastScope!=='');moved=false;}
   };
   draw(result,before,morphing=>{
    const now=anchor&&boxes.get(anchor);
    if(morphing&&was&&now){cam={...cam,x:cam.x+cam.k*(was.x-now.x),y:cam.y+cam.k*(was.y-now.y)};apply(true);setTimeout(settle,MORPH_MS+120);}
    else settle();
   });
   lastScope=scope;
  }
  decorate();
 }
 // Pan with drag or wheel, zoom with pinch or ctrl/⌘ + wheel.
 const pointers=new Map();let drag=null,pinch=null,clicked=false;
 viewport.addEventListener('pointerdown',e=>{if(e.button!==0&&e.pointerType==='mouse')return;pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});clicked=true;
  if(pointers.size===2){const [a,b]=[...pointers.values()];pinch={d:Math.hypot(a.x-b.x,a.y-b.y),k:cam.k};drag=null;}
  else if(!e.target.closest('[data-dg-node]')){drag={x:e.clientX,y:e.clientY,cx:cam.x,cy:cam.y};viewport.setPointerCapture(e.pointerId);}});
 viewport.addEventListener('pointermove',e=>{if(!pointers.has(e.pointerId))return;pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
  if(pinch&&pointers.size===2){const [a,b]=[...pointers.values()],r=viewport.getBoundingClientRect();zoom(pinch.k*Math.hypot(a.x-b.x,a.y-b.y)/(pinch.d||1)/cam.k,(a.x+b.x)/2-r.left,(a.y+b.y)/2-r.top);clicked=false;return;}
  if(drag){const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>3){clicked=false;moved=true;viewport.classList.add('dg-dragging');cam={...cam,x:drag.cx+dx,y:drag.cy+dy};apply(false);}}});
 const release=e=>{pointers.delete(e.pointerId);if(pointers.size<2)pinch=null;if(drag&&!pointers.size){drag=null;viewport.classList.remove('dg-dragging');}};
 viewport.addEventListener('pointerup',e=>{const wasDrag=!!drag;release(e);if(clicked&&wasDrag&&!e.target.closest('[data-dg-node]'))callbacks.onPick?.('');});
 viewport.addEventListener('pointercancel',release);
 viewport.addEventListener('wheel',e=>{
  if(e.ctrlKey||e.metaKey){e.preventDefault();const r=viewport.getBoundingClientRect();zoom(Math.exp(-e.deltaY*(e.deltaMode?0.05:0.0022)),e.clientX-r.left,e.clientY-r.top);return;}
  if(props.wheel==='page')return;
  e.preventDefault();moved=true;cam={...cam,x:cam.x-e.deltaX,y:cam.y-e.deltaY};apply(false);
 },{passive:false});
 host.addEventListener('click',e=>{const b=e.target.closest('[data-map]');if(b){const a=b.dataset.map;a==='fit'?(moved=false,fit()):zoom(a==='in'?1.25:.8);return;}
  const n=e.target.closest('[data-dg-node]');if(n)callbacks.onPick?.(n.dataset.dgNode);});
 host.addEventListener('dblclick',e=>{const n=e.target.closest('[data-dg-node]');if(n){e.preventDefault();callbacks.onDrill?.(n.dataset.dgNode);}});
 layer.addEventListener('pointerover',e=>{const n=e.target.closest('[data-dg-node]');const id=n?.dataset.dgNode||'';if(id!==hover){hover=id;decorate();}});
 layer.addEventListener('pointerleave',()=>{if(hover){hover='';decorate();}});
 viewport.addEventListener('keydown',e=>{
  if(e.target.closest('input,select,textarea'))return;
  const step=60,keys={ArrowLeft:[step,0],ArrowRight:[-step,0],ArrowUp:[0,step],ArrowDown:[0,-step]};
  if(e.key==='+'||e.key==='=')zoom(1.2);else if(e.key==='-')zoom(1/1.2);else if(e.key==='0'){moved=false;fit();}
  else if(e.key==='Escape')callbacks.onPick?.('');
  else if(keys[e.key]&&e.target===viewport){cam={...cam,x:cam.x+keys[e.key][0],y:cam.y+keys[e.key][1]};moved=true;apply(true);}
  else if(e.key==='Enter'&&e.target.dataset?.mapNode&&e.shiftKey)callbacks.onDrill?.(e.target.dataset.dgNode);
  else return;
  e.preventDefault();});
 const resize=new ResizeObserver(()=>{if(!moved&&boxes.size)fit(false);});resize.observe(viewport);
 return {update,fit:()=>{moved=false;fit();},focus:id=>{if(boxes.has(id)){fit(true,[id],1);moved=true;}},
  element:id=>els.get(id),destroy(){destroyed=true;resize.disconnect();host.replaceChildren();}};
}
