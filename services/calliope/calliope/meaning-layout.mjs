// Web map geometry. Reading and tracing use normal document layout.
export const W=300,H=190;
export const types=[['relation','Sources'],['cube','Cubes'],['metric','Metrics'],['artifact','Apps'],['idea','Metric ideas'],['breakdown','Breakdowns']];
export const clamp=(n,lo,hi)=>Math.max(lo,Math.min(hi,n));
// Collapse whole categories, never a handful sampled from each. All identities
// and edges remain in the complete catalog and can be expanded independently.
export function initialVisible(nodes,overview=false){
 if(overview)return new Set(nodes.map(n=>n.id));
 const counts={};for(const n of nodes)counts[n.kind]=(counts[n.kind]||0)+1;
 return new Set(nodes.filter(n=>n.kind==='area'||(!['idea','breakdown'].includes(n.kind)&&counts[n.kind]<=6)).map(n=>n.id));
}
export function groupedGraph(nodes,edges,visible){
 const groups=new Map(),byId=new Map(nodes.map(n=>[n.id,n])),mapped=new Map();
 for(const n of nodes){if(visible.has(n.id))continue;const kind=n.kind;if(!groups.has(kind))groups.set(kind,{id:'group:'+kind,kind:'group',group_kind:kind,title:types.find(t=>t[0]===kind)?.[1]||kind,members:[],status:'collapsed'});groups.get(kind).members.push(n.id);mapped.set(n.id,'group:'+kind);}
 const shown=nodes.filter(n=>visible.has(n.id)),groupEdges=new Map();
 for(const g of groups.values()){g.description=`${g.members.length} objects · open the complete searchable collection`;g.subtitle=`${g.members.length} of ${nodes.filter(n=>n.kind===g.group_kind).length} collapsed`;shown.push(g);}
 for(const e of edges){const from=mapped.get(e.from)||e.from,to=mapped.get(e.to)||e.to;if(!byId.has(e.from)||!byId.has(e.to))continue;if(from===to)continue;
  if(from===e.from&&to===e.to){groupEdges.set(e.id,e);continue;}
  const key=[from,to,e.label,e.state].join('|');if(!groupEdges.has(key))groupEdges.set(key,{...e,id:'group-edge:'+key,from,to,grouped:true,count:0,evidence:{basis:'Collapsed recorded relationships'}});groupEdges.get(key).count++;
 }
 return {nodes:shown,edges:[...groupEdges.values()].map(e=>e.grouped?{...e,label:`${e.label} · ${e.count}`} :e)};
}
export function positions(nodes,overview=false){
 const result={};
 if(overview){const columns=Math.max(1,Math.ceil(Math.sqrt(nodes.length)));nodes.forEach((n,i)=>result[n.id]=[i%columns*350,Math.floor(i/columns)*230]);return result;}
 let x=0;
 for(const kinds of [['area'],['relation'],['cube'],['metric','idea'],['artifact','breakdown']]){
  const entries=nodes.filter(n=>kinds.includes(n.group_kind||n.kind));
  if(!entries.length)continue;
  const columns=Math.max(1,Math.ceil(Math.sqrt(entries.length/1.5)));
  entries.forEach((n,i)=>result[n.id]=[x+(i%columns)*350,Math.floor(i/columns)*230]);
  x+=columns*350+70;
 }
 return result;
}
export function cleanLayout(raw={}){
 if(!raw||typeof raw!=='object'||Array.isArray(raw))raw={};
 const ps={};for(const [id,p] of Object.entries(raw.positions||{}).slice(0,400)){if(Array.isArray(p)&&p.length===2&&p.every(v=>Number.isFinite(v)&&Math.abs(v)<1e6))ps[id]=p;}
 return {positions:ps,pan:Array.isArray(raw.pan)&&raw.pan.length===2&&raw.pan.every(v=>Number.isFinite(v)&&Math.abs(v)<1e6)?raw.pan:[30,30],zoom:Number.isFinite(raw.zoom)?clamp(raw.zoom,.8,1.6):1,shown:Array.isArray(raw.shown)?raw.shown.filter(s=>typeof s==='string').slice(0,200):null,limit:Number.isInteger(raw.limit)?clamp(raw.limit,12,40):12};
}
export function fit(layout,visible,width,height){
 const ps=Object.entries(layout.positions).filter(([id])=>visible.has(id)).map(([,p])=>p);if(!ps.length||!width||!height)return layout;
 const x=Math.min(...ps.map(p=>p[0])),y=Math.min(...ps.map(p=>p[1])),r=Math.max(...ps.map(p=>p[0]+W)),b=Math.max(...ps.map(p=>p[1]+H));
 const zoom=clamp(Math.min(Math.max(width-70,100)/(r-x),Math.max(height-60,80)/(b-y)),.8,1.05);
 return {...layout,zoom,pan:[Math.max(30,(width-(r-x)*zoom)/2)-x*zoom,Math.max(25,(height-(b-y)*zoom)/2)-y*zoom]};
}
export function zoomAt(layout,next,x,y){const z=clamp(next,.8,1.6);return {...layout,zoom:z,pan:[x-(x-layout.pan[0])*z/layout.zoom,y-(y-layout.pan[1])*z/layout.zoom]};}
export function curve(a,b){if(Math.abs(a[0]-b[0])<10){const s=[a[0]+W,a[1]+H/2],e=[b[0]+W,b[1]+H/2];return [s,e,[s[0]+60,s[1]],[e[0]+60,e[1]]]};const forward=a[0]<b[0],s=[a[0]+(forward?W:0),a[1]+H/2],e=[b[0]+(forward?0:W),b[1]+H/2],m=(s[0]+e[0])/2;return [s,e,[m,s[1]],[m,e[1]]];}
export function overlaps(a,b,pad=0){return a[0]<b[0]+b[2]+pad&&a[0]+a[2]+pad>b[0]&&a[1]<b[1]+b[3]+pad&&a[1]+a[3]+pad>b[1];}
export function safeLabel([a,b,c,d],width,occupied,viewport){for(const t of [.5,.4,.6,.3,.7,.2,.8,.12,.88,.06,.94]){const u=1-t,p=[0,1].map(i=>u*u*u*a[i]+3*u*u*t*c[i]+3*u*t*t*d[i]+t*t*t*b[i]),rect=[p[0]-width/2,p[1]-9,width,18];if(rect[0]>=6&&rect[1]>=6&&rect[0]+width<=viewport[0]-6&&rect[1]+18<=viewport[1]-26&&!occupied.some(r=>overlaps(rect,r,6)))return rect;}return null;}
export function edgeLabels(edges,layout,visible,selected,edge,width,height){
 if(layout.zoom<.6)return [];const trans=p=>[layout.pan[0]+p[0]*layout.zoom,layout.pan[1]+p[1]*layout.zoom];
 const occupied=Object.entries(layout.positions).filter(([id])=>visible.has(id)).map(([,p])=>[...trans(p),W*layout.zoom,H*layout.zoom]),out=[];
 for(const e of [...edges].sort((a,b)=>Number(b.id===edge)-Number(a.id===edge))){if(![e.from,e.to].includes(selected)||!visible.has(e.from)||!visible.has(e.to)||!layout.positions[e.from]||!layout.positions[e.to])continue;const rect=safeLabel(curve(layout.positions[e.from],layout.positions[e.to]).map(trans),[...e.label].length*6.5+14,occupied,[width,height]);if(rect){occupied.push(rect);out.push({...e,rect})}}return out;
}
export function hiddenNeighbors(edges,selected,visible){const found=new Set(visible),out=[];for(const e of edges){const other=e.from===selected?e.to:e.to===selected?e.from:null;if(other&&!found.has(other)){found.add(other);out.push(other)}}return out;}
