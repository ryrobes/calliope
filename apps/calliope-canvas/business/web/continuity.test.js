import test from 'node:test';import assert from 'node:assert/strict';
import {reconcile,resolve,equal,incrementalMessages} from './continuity.js';import {fieldError} from './fields.js';import {pasteMatrix} from './grid.js';
test('merge accepts independent updates and retains conflicts until explicit resolution',()=>{
 const base={id:'Q1',version:1,notes:'original',lines:{a:{quantity:'1',unit_price:'2.00'}}};
 const local=structuredClone(base);local.notes='my notes';local.lines.a.quantity='2';const remote=structuredClone(base);remote.version=2;remote.lines.a.unit_price='3.00';
 let next=reconcile(base,local,remote);assert.equal(next.local.notes,'my notes');assert.equal(next.local.lines.a.quantity,'2');assert.equal(next.local.lines.a.unit_price,'3.00');assert.equal(next.local.version,2);assert.deepEqual(next.conflicts,{});
 remote.lines.a.quantity='4';next=reconcile(base,local,remote);next=reconcile(next.base,next.local,remote,next.conflicts);assert.ok(next.conflicts['/lines/a/quantity']);
 assert.equal(resolve(next,true).local.lines.a.quantity,'4');assert.equal(resolve(next,true).local.notes,'my notes');assert.equal(resolve(next,false).local.lines.a.quantity,'2');assert.deepEqual(resolve(next,false).conflicts,{});assert.deepEqual(JSON.parse(JSON.stringify(next)),next);
});
test('receiving requires review when another receipt changes the order',()=>{
 const base={id:'PO-1',version:1,received_date:'2026-10-08',reference:'',lines:{a:{quantity:'0'}}};const local=structuredClone(base);local.lines.a.quantity='2';const remote={...base,version:2};let e=reconcile(base,local,remote);e=reconcile(e.base,e.local,remote,e.conflicts);assert.ok(e.conflicts['/version'].recordChanged);assert.equal(resolve(e,true).local.lines.a.quantity,'0');assert.equal(resolve(e,false).local.lines.a.quantity,'2');
});
test('deletion versus editing is a conflict; equality ignores object key order',()=>{
 const e=reconcile({lines:{a:{quantity:'1'}}},{lines:{a:{quantity:'3'}}},{lines:{}});assert.ok(e.conflicts['/lines/a']);assert.deepEqual(resolve(e,true).local.lines,{});assert.ok(equal({a:1,b:2},{b:2,a:1}));assert.ok(!equal({a:1},{a:1,b:2}));
});
test('incremental updates omit unchanged controls and local drafts',()=>{
 const components=[{id:'root',component:'CField',value:{path:'/draft/notes'}}];const snapshot=data=>({messages:[{updateComponents:{components}},{updateDataModel:{value:data}}]});const current={draft:{notes:'typing'},total:1};const next=incrementalMessages(snapshot(current),snapshot({...current,total:2}),current);assert.equal(next.length,1);assert.equal(next[0].updateDataModel.path,'/total');
});
test('typed validation and atomic rectangular paste',()=>{
 for(const v of ['','1.5','1e2','NaN','1,000'])assert.ok(fieldError(v,{kind:'quantity',precision:0,required:true}));assert.ok(fieldError('2026-02-30',{kind:'date'}));assert.equal(fieldError('2024-02-29',{kind:'date'}),'');assert.ok(fieldError('24:00',{kind:'time'}));assert.equal(fieldError('',{required:true,visible:false}),'');
 const rows=[['a','b'],['c','d']],fields=new Map(rows.flat().map(id=>[id,{props:{kind:'quantity',precision:0,label:id}}]));assert.deepEqual(pasteMatrix('1\t2\n3\t4',0,0,rows,fields),[['a','1'],['b','2'],['c','3'],['d','4']]);assert.throws(()=>pasteMatrix('1\t2\n3\t4.5',0,0,rows,fields));assert.throws(()=>pasteMatrix('1\t2',0,1,rows,fields));
});


test('case delivery acknowledges only its editor and retains a scheduling draft', async()=>{
  const {acknowledge}=await import('./continuity.js');
  const base={version:1,notes:'old',handoff:'',receipt:{version:1,quantity:'0'},appointment:{version:1,start:'08:00',reason:''}};
  const local=structuredClone(base);local.handoff='Keep access notes';local.receipt.quantity='1';local.appointment.start='14:00';
  const remote=structuredClone(base);remote.receipt.version=2;
  const e=acknowledge({base,local,conflicts:{'/receipt/quantity':{local:'1',remote:'0'}}},remote,['/receipt']);
  const result=reconcile(e.base,e.local,remote,e.conflicts);
  assert.equal(result.local.receipt.quantity,'0');assert.equal(result.local.receipt.version,2);
  assert.equal(result.local.appointment.start,'14:00');assert.equal(result.local.handoff,'Keep access notes');assert.deepEqual(result.conflicts,{});
});
