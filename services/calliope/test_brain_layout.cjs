const assert=require('node:assert/strict');
const {layout}=require('./theme/brain-layout.js');
const roots=['hub','a','b','c','isolated','lonely'].map(id=>({id,root:true,sources:[id]}));
const nodes=[...roots],edges=[];
function shared(id,sources){nodes.push({id,sources});}
shared('ab',['hub','a']);shared('ac',['hub','b']);shared('ad',['hub','c']);shared('bc',['a','b']);
// Membership/internal links must not turn a busy isolated corpus into a hub.
for(let i=0;i<100;i++){nodes.push({id:'i'+i,sources:['isolated']});edges.push({source:'isolated',target:'i'+i,kind:'contains'});}
const result=layout(roots,nodes,edges),byId=new Map(result.map(r=>[r.id,r]));
assert.equal(byId.get('hub').orbit,0);assert.equal(byId.get('hub').crossLinks,3);
assert.equal(byId.get('isolated').crossLinks,0);
const connected=result.filter(r=>r.crossLinks>0),outer=result.filter(r=>!r.crossLinks);
assert.ok(Math.min(...outer.map(r=>r.orbit))>Math.max(...connected.map(r=>r.orbit)));
for(let i=0;i<result.length;i++)for(let j=i+1;j<result.length;j++)assert.ok(Math.hypot(result[i].x-result[j].x,result[i].y-result[j].y)>=239.99);
assert.deepEqual(layout([...roots].reverse(),[...nodes].reverse(),[...edges].reverse()),result);
assert.deepEqual(layout([],[],[]),[]);
assert.equal(layout([roots[0]],[roots[0]],[])[0].orbit,0);
const large=Array.from({length:80},(_,i)=>({id:'r'+i,root:true,sources:['r'+i]}));
const many=layout(large,large,[]);assert.equal(many.length,80);assert.ok(many.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
console.log('Radial layout: centrality, isolation, spacing, determinism and bounds passed');
