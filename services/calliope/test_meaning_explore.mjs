import test from 'node:test';
import assert from 'node:assert/strict';
import {neighborhood, collection, connectionsCount} from './calliope/meaning-explore.mjs';
import {positions, fit, cleanLayout} from './calliope/meaning-layout.mjs';

const nodes=[{id:'area',kind:'area',title:'Inventory'}, {id:'source',kind:'relation',title:'Stock',description:'Physical inventory'},
 {id:'cube',kind:'cube',title:'Stock snapshot'}, {id:'metric',kind:'metric',title:'Stock cost',review:['Scope needs review']},
 {id:'app',kind:'artifact',title:'Inventory desk'}, {id:'idea',kind:'idea',title:'Stock trend'}];
const edges=[{id:'a',from:'area',to:'cube',label:'shares sources with',state:'association'},
 {id:'b',from:'source',to:'cube',label:'feeds',state:'declared'},
 {id:'c',from:'cube',to:'metric',label:'measured by',state:'declared'},
 {id:'d',from:'cube',to:'app',label:'used by',state:'declared'},
 {id:'e',from:'cube',to:'idea',label:'suggests',state:'unverified'}];
test('trace preserves every incident edge and separates associations from lineage',()=>{
 const trace=neighborhood({nodes,edges},'cube');
 assert.deepEqual(trace.inputs.map(e=>e.node.id),['source']);
 assert.deepEqual(trace.consumers.map(e=>e.node.id),['app','metric']);
 assert.deepEqual(trace.context.map(e=>e.edge.id),['a','e']);
 assert.equal(Object.values(trace).flat().length,edges.length);
 assert.equal(connectionsCount([...trace.inputs,...trace.inputs]),1);
});
test('collection search and review filters cover the complete catalog without sampling',()=>{
 const graph={nodes,edges};
 assert.equal(collection(graph).length,5);
 assert.deepEqual(collection(graph,{query:'physical'}).map(n=>n.id),['source']);
 assert.deepEqual(collection(graph,{lens:'review'}).map(n=>n.id),['metric','idea']);
 assert.equal(collection(graph,{lens:'connected',selected:'cube'}).length,5);
 assert.equal(collection(graph,{kind:'metric',lens:'connected',selected:'source'}).length,0);
});
test('large map categories use multiple columns and fitting never destroys reading scale',()=>{
 const large=Array.from({length:50},(_,i)=>({id:'m'+i,kind:'metric'})),ps=positions(large);
 assert.ok(new Set(Object.values(ps).map(p=>p[0])).size>1);
 const layout=fit(cleanLayout({positions:ps}),new Set(large.map(n=>n.id)),900,600);
 assert.equal(layout.zoom,.8);
 assert.ok(layout.pan[0]>=0&&layout.pan[1]>=0);
 assert.equal(cleanLayout({zoom:.3}).zoom,.8);
});

test('possible joins and implied links are context, not data-flow dependencies',()=>{
 const g=neighborhood({nodes,edges:[{id:'join',from:'source',to:'cube',label:'joins to',state:'declared'},
   {id:'implied',from:'cube',to:'metric',label:'measured by',state:'implied'}]},'cube');
 assert.equal(g.inputs.length,0);assert.equal(g.consumers.length,0);assert.equal(g.context.length,2);
});
