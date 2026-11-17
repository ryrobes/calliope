import test from 'node:test';
import assert from 'node:assert/strict';
import {example,actionExample,layout,nodeAt,replaceNode,leaf,binary,choice,validateTree,tracedPaths,stepPackage} from './calliope/business-logic-model.mjs';
test('terminal actions stay attached through graph edits and fit inside graph bounds',()=>{
 const doc=actionExample();validateTree(doc.tree);
 const graph=layout(doc.tree),terminal=graph.nodes.filter(n=>n.node.action);
 assert.equal(terminal.length,2);
 assert.deepEqual(terminal.map(n=>n.node.action.kind).sort(),['callie','sql']);
 assert.equal(doc.inputs.request_id.type,'string');assert.equal(doc.examples[0].context.request_id,'1');
 for(const n of terminal)assert.ok(n.x+n.width<=graph.width);
 const original=terminal[0],split={...binary(),yes:original.node};
 const edited=replaceNode(doc.tree,original.path,split);
 assert.equal(nodeAt(edited,original.path+'/yes').action.sql,original.node.action.sql);
 assert.throws(()=>validateTree({...leaf(),action:{kind:'shell',command:'anything'}}));
});
test('branch layout gives every child its own space and preserves pointer identity',()=>{
 const tree=choice();tree.routes.first=binary();const g=layout(tree);
 assert.equal(g.nodes.length,6);assert.equal(g.edges.length,5);
 for(const edge of g.edges)assert.ok(edge.x2>edge.x1,'Every branch proceeds left to right');
 for(const a of g.nodes)for(const b of g.nodes)if(a!==b)assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y);
 assert.equal(nodeAt(tree,'root/first/yes').label,'Yes');
 const next=replaceNode(tree,'root/first/yes',leaf('Revisit'));
 assert.equal(nodeAt(next,'root/first/yes').label,'Revisit');assert.equal(nodeAt(tree,'root/first/yes').label,'Yes');
});
test('trace distinguishes binary/choice paths and escaped keys',()=>{
 const paths=tracedPaths({status:'complete',path:[{node:'root',route:'a/b~',choices:{}},{node:'root/a~1b~0',route:'no'}]});
 assert.deepEqual([...paths],['root','root/a~1b~0','root/a~1b~0/no']);
 assert.deepEqual([...tracedPaths({status:'complete',path:[]})],['root']);
 assert.equal(tracedPaths({status:'unavailable',path:[]}).size,0);
});
test('reject malformed or oversized trees before replacing the current tree',()=>{
 for(const bad of [null,[],{kind:'sql',question:'q'}, {kind:'leaf',label:'x',sql:'select'}, {...binary(),threshold:NaN}, {...choice(),routes:{}}])assert.throws(()=>validateTree(bad));
 let deep=leaf();for(let i=0;i<5;i++)deep={...binary(),yes:deep};assert.throws(()=>validateTree(deep));
 assert.equal(validateTree(example().tree),9);
});

test('example has distinct questions, real field bindings and six outcomes',()=>{
 const doc=example(),nodes=layout(doc.tree).nodes;
 assert.equal(nodes.filter(n=>n.node.kind!=='leaf').length,3);
 assert.equal(new Set(doc.examples.map(e=>e.expected)).size,6);
 assert.deepEqual(nodeAt(doc.tree,'root/repair/repeat').inputs,['parts_status']);
 assert.deepEqual(Object.keys(doc.examples[4].context),['customer_message']);
 for(const inputs of [[],['x','x'],[''],['x'.repeat(81)],null,[42]])assert.throws(()=>validateTree({...binary(),inputs}));
 assert.equal(validateTree({...binary(),inputs:['a.b/key']}),3);
});

test('source SQL is portable, quotes values, and keeps every source column',async()=>{
 const {sourceSql,replaceRoute}=await import('./calliope/business-logic-model.mjs');
 const sql=sourceSql([{name:"O'Brien",context:{'a"b':"O'Brien",empty:null}}]);
 assert.ok(sql.includes("'O''Brien'"));assert.ok(sql.includes('"a""b"'));assert.ok(sql.includes('NULL::text'));
 const n=choice(),next=replaceRoute(n,'first','a/b~');assert.equal(next.routes['a/b~'].label,'First outcome');assert.equal(n.routes.first.label,'First outcome');
 assert.throws(()=>replaceRoute(n,'first','second'));assert.throws(()=>replaceRoute(n,'first',''));
});

test('measured multiline nodes retain spacing and connect at the actual route ports',()=>{
 const tree=example().tree,measured={'root':{height:820,ports:{repair:315,installation:650,unclear:760}},'root/repair':{height:1200,ports:{repeat:420,new:740,unclear:1150}}};
 const g=layout(tree,measured);
 for(const a of g.nodes)for(const b of g.nodes)if(a!==b)assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y,'Expanded text never overlaps another node');
 for(const [path,sizes] of Object.entries(measured)){
  const n=g.nodes.find(n=>n.path===path);assert.equal(n.height,sizes.height);
  for(const [key,y] of Object.entries(sizes.ports))assert.equal(g.edges.find(e=>e.to===path+'/'+key).y1,n.y+y);
 }
 assert.ok(g.height>1200);
});

test('step packages keep evidence separate from earlier answers and never invent an unvisited request',()=>{
 const tree=example().tree,context={customer_message:'repair',previous_visit_notes:null,parts_status:'ready',unused:7};
 const first={node:'root',route:'repair',question:tree.question,answer:'repair',score:.9,choices:tree.choices,model:'ignored',inputs:['customer_message']};
 const second={node:'root/repair',route:'repeat',question:tree.routes.repair.question,answer:'repeat',score:.8,choices:tree.routes.repair.choices};
 const last={node:'root/repair/repeat',route:'yes',question:tree.routes.repair.routes.repeat.question,answer:true,score:.9,threshold:.5};
 const d={status:'complete',path:[first,second,last]},packet=stepPackage(tree,last.node,context,d);
 assert.deepEqual(packet.evidence,{parts_status:'ready'});assert.deepEqual(packet.prior.map(s=>s.answer),['repair','repeat']);
 assert.equal(packet.request.state.prior_judgments.length,2);assert.ok(!('node' in packet.prior[0]));assert.ok(!('model' in packet.prior[0]));assert.ok(!('inputs' in packet.prior[0]));
 assert.equal(stepPackage(tree,'root/repair/new',context,d).phase,'unvisited');assert.equal(stepPackage(tree,'root/repair/new',context,d).request,null);
 assert.equal(stepPackage(tree,last.node,context,null).request,null);
 const stopped=stepPackage(tree,last.node,{customer_message:'repair'}, {status:'missing_input',path:[first,second],stopped_at:last.node});
 assert.deepEqual(stopped.missing,['parts_status']);assert.equal(stopped.request,null);assert.equal(stopped.prior.length,2);
 const leafPacket=stepPackage(tree,last.node+'/yes',context,d);assert.equal(leafPacket.phase,'result');assert.equal(leafPacket.request,null);assert.equal(leafPacket.prior[2].answer,true);
});
