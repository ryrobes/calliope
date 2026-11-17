import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {areaModel,objectModel,landscapeModel,levelsFor,elkGraph,areaGroups} from './calliope/meaning-diagram.mjs';

const area='subject:orders',root='area:'+area;
const node=(kind,key,extra={})=>({id:`${kind}:${key}`,kind,title:key,status:'provisional',...extra});
const graph={
 nodes:[{id:root,kind:'area',title:'Orders',subtitle:'4 sources',area},
  node('relation','s.orders',{source:'s.orders',preview:{value:2000},object_role:'event',grain:'One row represents an order placed at a point in time.'}),node('relation','s.items',{source:'s.items',object_role:'junction'}),
  node('relation','s.stores',{source:'s.stores',object_role:'entity',subject_role:'support'}),node('relation','x.loose',{source:'x.loose'}),
  node('cube','cubes.daily',{source:'cubes.daily'}),node('metric','revenue',{preview:{value:1234567,label:'Saved daily snapshot'}}),
  node('idea','idea1',{title:'Orders per day'}),node('artifact','desk',{slug:'desk'})],
 edges:[...['s.orders','s.items','s.stores','x.loose'].map(r=>({id:'inc'+r,from:root,to:'relation:'+r,label:'includes',state:'published'})),
  {id:'j1',from:'relation:s.items',to:'relation:s.orders',label:'joins to',state:'verified',evidence:{from_column:'order_id',to_column:'order_id'}},
  {id:'j2',from:'relation:s.orders',to:'relation:s.stores',label:'joins to',state:'unverified',evidence:{from_column:'store_id',to_column:'id'}},
  {id:'c1',from:root,to:'cube:cubes.daily',label:'analyzed through',state:'declared'},
  {id:'f1',from:'relation:s.orders',to:'cube:cubes.daily',label:'feeds',state:'declared'},
  {id:'m0',from:root,to:'metric:revenue',label:'measured by',state:'declared'},
  {id:'m1',from:'cube:cubes.daily',to:'metric:revenue',label:'measured by',state:'declared'},
  {id:'i1',from:root,to:'idea:idea1',label:'suggests',state:'unverified'},
  {id:'a0',from:root,to:'artifact:desk',label:'explored in',state:'declared'},
  {id:'a1',from:'metric:revenue',to:'artifact:desk',label:'used by',state:'declared'}],
 groups:[{id:'group:capture',title:'Order capture',members:['relation:s.orders','relation:s.items']},{id:'group:stores',title:'Stores',members:['relation:s.stores']}],
 neighbors:[{id:'area:subject:sales',kind:'area',area:'subject:sales',title:'Sales',subtitle:'5 sources',ties:[{local:'relation:s.stores',remote:'t.stores',label:'joins on store_id',state:'verified'},{local:null,label:'same schema s',state:'association'}]}]};
const ids=m=>new Set(m.nodes.map(n=>n.id));

test('L1 shows the area, its groups and neighbours; objects lift into their group',()=>{
 const m=areaModel(graph,{level:1});
 assert.deepEqual([...ids(m)].sort(),[root,'area:subject:sales','group:capture','group:other:'+root,'group:stores'].sort());
 assert.ok(m.nodes.every(n=>n.id===root||!n.container),'groups are cards at L1');
 const lifted=m.edges.find(e=>e.from==='group:capture'&&e.to==='group:stores');
 assert.equal(lifted.label,'belongs to · store_id → id','join verbs come from the inferred roles');
 assert.ok(m.edges.some(e=>e.from==='group:stores'&&e.to==='area:subject:sales'&&e.crossing),'a member tie lifts to its group and crosses the boundary');
 assert.ok(m.edges.some(e=>e.from===root&&e.to==='area:subject:sales'&&e.state==='association'),'a schema tie starts at the area itself');
 assert.ok(!m.edges.some(e=>e.from==='group:capture'&&e.to==='group:capture'),'joins inside a group vanish at L1');
 assert.match(m.nodes.find(n=>n.id==='group:capture').subtitle,/2 sources · 2K rows/);
});

test('L2 opens groups into sources and places a single-group cube inside it',()=>{
 const m=areaModel(graph,{level:2}),by=new Map(m.nodes.map(n=>[n.id,n]));
 assert.ok(by.get('group:capture').container);
 assert.equal(by.get('relation:s.items').parent,'group:capture');
 assert.equal(by.get('cube:cubes.daily').parent,'group:capture');
 assert.equal(by.get('relation:x.loose').parent,'group:other:'+root,'ungrouped members get an honest group');
 assert.ok(!by.has('metric:revenue'),'measures wait for L3');
 assert.equal(m.edges.find(e=>e.from==='relation:s.items').label,'part of · order_id');
 const orders=by.get('relation:s.orders');
 assert.equal(orders.role,'event');assert.equal(orders.subtitle,'Each row: an order placed at a point in time');assert.equal(orders.meta,'s.orders · 2K rows');
 assert.ok(by.get('relation:s.stores').support,'supporting tables are marked');
});

test('L3 adds measures, suggestions in their own box and the work built on them',()=>{
 const m=areaModel(graph,{level:3}),by=new Map(m.nodes.map(n=>[n.id,n]));
 assert.equal(by.get('idea:idea1').parent,'suggestions:'+root);
 assert.equal(by.get('metric:revenue').value,'1.2M');
 assert.ok(m.edges.some(e=>e.from==='metric:revenue'&&e.to==='artifact:desk'));
 assert.ok(!m.edges.some(e=>e.from===root&&!e.to.startsWith('area:')),'containment replaces edges from the area root');
});

test('hiding a legend kind keeps what it linked connected through it',()=>{
 const m=areaModel(graph,{level:3,hidden:new Set(['cube'])});
 assert.ok(!ids(m).has('cube:cubes.daily'));
 const bridge=m.edges.find(e=>e.from==='relation:s.orders'&&e.to==='metric:revenue');
 assert.ok(bridge.bridged&&bridge.label==='via cubes.daily');
});

test('an object map is its neighbourhood, nested in its group, starting at L2',()=>{
 const m=objectModel(graph,{target:'cube:cubes.daily',level:2});
 assert.deepEqual([...ids(m)].sort(),[root,'cube:cubes.daily','group:capture','relation:s.orders'].sort());
 assert.ok(m.nodes.find(n=>n.id==='cube:cubes.daily').viewing);
 const deeper=objectModel(graph,{target:'cube:cubes.daily',level:3});
 assert.ok(ids(deeper).has('metric:revenue')&&ids(deeper).has('artifact:desk'),'L3 follows the flow downstream');
 assert.deepEqual(levelsFor('object').map(l=>l.available),[false,true,true]);
 assert.deepEqual(levelsFor('landscape').map(l=>l.available),[true,false,false]);
});

test('landscape nests each area’s groups and keeps only ties between shown areas',()=>{
 const m=landscapeModel({nodes:[{id:root,kind:'area',title:'Orders',area,groups:[{id:'group:capture',title:'Order capture',size:2}]},{id:'area:b',kind:'area',title:'B',area:'b',groups:[]}],
  area_links:[{id:'e',from:root,to:'area:b',label:'shares this source',state:'shared'},{id:'gone',from:root,to:'area:missing',label:'x'}]});
 assert.equal(m.nodes.find(n=>n.id==='group:capture').parent,root);
 assert.ok(m.nodes.find(n=>n.id===root).container&&!m.nodes.find(n=>n.id==='area:b').container);
 assert.deepEqual(m.edges.map(e=>e.id),['e']);
});

test('no published grouping falls back to schemas and says so',()=>{
 const groups=areaGroups({...graph,groups:[]});
 assert.deepEqual(groups.map(g=>g.title).sort(),['s','x']);
 assert.match(groups[0].description,/no published grouping/);
});

test('ELK lays the area out without overlapping siblings and packs loose groups into rows',async()=>{
 const ELK=createRequire(import.meta.url)('./calliope/meaning-elk.js'),elk=new ELK();
 const loose={...graph,groups:[1,2,3,4,5,6].map(i=>({id:'group:g'+i,title:'Group '+i,members:[]})),nodes:[...graph.nodes,...[1,2,3,4,5,6].map(i=>node('relation','g'+i))],
  edges:[...graph.edges,...[1,2,3,4,5,6].map(i=>({id:'incg'+i,from:root,to:'relation:g'+i,label:'includes'}))]};
 loose.groups.forEach((g,i)=>g.members.push('relation:g'+(i+1)));
 const model=areaModel(loose,{level:2}),input=elkGraph(model,{aspect:1.8});
 assert.ok(input.edges.some(e=>e.id.startsWith('grid:')),'unconnected groups are ordered into rows');
 const out=await elk.layout(input),box=new Map();
 const walk=n=>{for(const c of n.children||[]){box.set(c.id,c);walk(c);}};walk(out);
 const areaBox=box.get(root);
 const kids=model.nodes.filter(n=>n.parent===root).map(n=>box.get(n.id));
 for(const a of kids)for(const b of kids)if(a!==b)assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y,`${a.id} overlaps ${b.id}`);
 assert.ok(areaBox.width/areaBox.height>0.8,'packed rows instead of one tall column');
 assert.ok(out.edges.filter(e=>!e.id.startsWith('grid:')).every(e=>e.sections?.length),'every real link is routed');
});
