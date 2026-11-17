/* Deterministic radial placement from observed cross-source relationships.
   Pure geometry: graph samples, not corpus size, determine centrality. */
(function(root){
'use strict';
function hash(s){let h=2166136261;for(const c of s)h=Math.imul(h^c.charCodeAt(0),16777619);return h>>>0;}
function layout(roots,nodes,edges,spacing=240){
 const byId=new Map(nodes.map(n=>[n.id,n])),ids=new Set(roots.map(r=>r.id)),links=new Map(roots.map(r=>[r.id,new Map()]));
 const owners=n=>[...new Set((n?.sources||[]).filter(s=>ids.has(s)))].sort();
 function connect(a,b){if(a===b)return;links.get(a).set(b,(links.get(a).get(b)||0)+1);links.get(b).set(a,(links.get(b).get(a)||0)+1);}
 for(const n of nodes){const group=owners(n);for(let i=0;i<group.length;i++)for(let j=i+1;j<group.length;j++)connect(group[i],group[j]);}
 for(const e of edges){if(e.kind==='contains')continue;const a=owners(byId.get(e.source)),b=owners(byId.get(e.target));if(a.some(id=>b.includes(id)))continue;for(const x of a)for(const y of b)connect(x,y);}
 const ranked=roots.map(r=>({...r,crossLinks:[...links.get(r.id).values()].reduce((a,b)=>a+b,0),peers:links.get(r.id).size})).sort((a,b)=>b.crossLinks-a.crossLinks||b.peers-a.peers||a.id.localeCompare(b.id));
 const placed=[],connected=ranked.filter(r=>r.crossLinks>0).length;
 let innerRadius=0,priorRadius=0;
 for(let index=0;index<ranked.length;index++){
  const r=ranked[index];
  if(ranked.length===1||(index===0&&connected)){placed.push({...r,x:0,y:0,orbit:0});continue;}
  const isolated=!r.crossLinks;
  let radius=Math.max(priorRadius,isolated?Math.max(spacing,innerRadius+spacing*1.1):spacing*Math.sqrt(index)*.78);
  const phase=hash(r.id)/4294967296*Math.PI*2;
  let best=null;
  // Grow an orbit only when the current one cannot maintain cluster clearance.
  for(let attempt=0;attempt<ranked.length*8+8&&!best;attempt++,radius+=spacing*.12){
   for(let j=0;j<96;j++){
    const angle=phase+j/96*Math.PI*2,x=Math.cos(angle)*radius,y=Math.sin(angle)*radius;
    if(placed.some(p=>Math.hypot(p.x-x,p.y-y)<spacing))continue;
    let score=0,weight=0;
    for(const p of placed){const d=Math.hypot(p.x-x,p.y-y),w=links.get(r.id).get(p.id)||0;score+=d*w;weight+=w;}
    score=weight?score/weight:0;
    // Related roots stay close; equally good positions spread around the orbit.
    score+=placed.reduce((s,p)=>s+spacing*spacing/Math.max(1,Math.hypot(p.x-x,p.y-y)),0)*.12;
    if(!best||score<best.score)best={x,y,score,orbit:radius};
   }
  }
  const p={...r,...best};placed.push(p);priorRadius=p.orbit;
  if(!isolated)innerRadius=p.orbit;
 }
 return placed;
}
const api={layout};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.CalliopeBrainLayout=api;
})(typeof globalThis!=='undefined'?globalThis:this);
