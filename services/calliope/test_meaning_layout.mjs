import test from 'node:test';
import assert from 'node:assert/strict';
import {initialVisible,groupedGraph,positions,cleanLayout,zoomAt,fit,edgeLabels,overlaps,hiddenNeighbors,W,H} from './calliope/meaning-layout.mjs';
test('initial map collapses whole large categories without selecting arbitrary formulas',()=>{
 const nodes=[{id:'area',kind:'area'},...Array.from({length:8},(_,i)=>({id:'m'+i,kind:'metric',display_group:i<2?'alias':i})),...Array.from({length:6},(_,i)=>({id:'s'+i,kind:'relation'})),{id:'idea',kind:'idea'}];
 assert.deepEqual([...initialVisible(nodes)],['area','s0','s1','s2','s3','s4','s5']);
 assert.equal(initialVisible(nodes,true).size,nodes.length);
});
test('pointer anchored zoom preserves world position including clamp',()=>{
 const layout={zoom:.8,pan:[-80,90],positions:{}};
 for(const z of [.1,.4,1,3]){const next=zoomAt(layout,z,300,180);assert.ok(Math.abs((300-next.pan[0])/next.zoom-(300-layout.pan[0])/layout.zoom)<1e-8);assert.ok(Math.abs((180-next.pan[1])/next.zoom-(180-layout.pan[1])/layout.zoom)<1e-8);}
});
test('geometry rejects malformed saved values and bounds catalog depth',()=>{
 assert.equal(cleanLayout(null).zoom,1);
 const x=cleanLayout({positions:{bad:[NaN,3],ok:[30,40]},pan:[Infinity,0],zoom:5,limit:900});
 assert.deepEqual(x.positions,{ok:[30,40]});assert.deepEqual(x.pan,[30,30]);assert.equal(x.zoom,1.6);assert.equal(x.limit,40);
});
test('fit uses only shown nodes and keeps cards in viewport',()=>{
 const layout=cleanLayout({positions:{a:[0,0],b:[780,215],hidden:[10000,0]}}),visible=new Set(['a','b']),next=fit(layout,visible,1400,750);
 for(const id of visible){const [x,y]=next.positions[id];assert.ok(next.pan[0]+x*next.zoom>=0);assert.ok(next.pan[1]+y*next.zoom>=0);assert.ok(next.pan[0]+(x+W)*next.zoom<=1400);assert.ok(next.pan[1]+(y+H)*next.zoom<=750);}
});
test('labels avoid cards and each other; hidden neighbors are unique',()=>{
 const nodes=[{id:'a',kind:'area'},{id:'b',kind:'relation'},{id:'c',kind:'relation'}],layout={zoom:1,pan:[40,40],positions:positions(nodes)},visible=new Set(['a','b','c']);
 const edges=[{id:'ab',from:'a',to:'b',label:'has source'},{id:'ac',from:'a',to:'c',label:'has source'}];
 const labels=edgeLabels(edges,layout,visible,'a',null,1500,800),cards=Object.values(layout.positions).map(([x,y])=>[x+40,y+40,W,H]);
 for(const l of labels)assert.ok(cards.every(c=>!overlaps(l.rect,c,6)));
 if(labels.length>1)assert.ok(!overlaps(labels[0].rect,labels[1].rect,6));
 assert.deepEqual(edgeLabels(edges,{...layout,zoom:.5},visible,'a',null,1500,800),[]);
 assert.deepEqual(hiddenNeighbors([...edges,edges[0]],'a',new Set(['a'])),['b','c']);
});

test('collapsed nodes preserve every identity and aggregate only recorded edges',()=>{
 const nodes=[{id:'area',kind:'area'},...Array.from({length:12},(_,i)=>({id:'m'+i,kind:'metric'}))];
 const edges=nodes.slice(1).map(n=>({id:'e'+n.id,from:'area',to:n.id,label:'measured by',state:'declared'}));
 const shown=initialVisible(nodes),g=groupedGraph(nodes,edges,shown);
 assert.equal(g.nodes.length,2);assert.equal(g.nodes[1].members.length,12);assert.equal(g.edges[0].count,12);
 shown.add('m11');const partial=groupedGraph(nodes,edges,shown);assert.equal(partial.nodes.find(n=>n.kind==='group').members.length,11);assert.ok(partial.edges.some(e=>e.id==='em11'));
 const expanded=groupedGraph(nodes,edges,new Set(nodes.map(n=>n.id)));assert.deepEqual(expanded.nodes,nodes);assert.deepEqual(expanded.edges,edges);
});
