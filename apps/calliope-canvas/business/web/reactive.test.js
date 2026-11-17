import test from 'node:test';
import assert from 'node:assert/strict';
import {Reactive,compose} from './reactive.js';
const definition={view:'studio',selected:'a',reactive:[{id:'x',watch:{notes:'/draft/notes'},initial:'a',target:'slot',debounceMs:100,tree:{kind:'leaf',label:'b'},variants:{a:{value:{label:'A'},root:'x.a',components:[{id:'x.a',component:'CText',text:'A'}]},b:{value:{label:'B'},root:'x.b',components:[{id:'x.b',component:'CText',text:'B'}]}}}]};
const result=label=>({decision:{status:'complete',label,steps:1},elapsed_ms:25});
const data=notes=>({draft:{notes,handoff:'Keep this'},resources:{x:{noise:Math.random()}}});
test('watch only declared data; debounce burst, keep last value, reject a late response',()=>{
 const r=new Reactive();r.install(definition);r.observe(data('one'),0);assert.equal(r.poll(99).length,0);
 const first=r.poll(100)[0];assert.equal(r.states().x.value.label,'A');
 r.observe(data('two'),150);assert.equal(r.poll(250).length,0); // at most one inference in flight
 assert.equal(r.receive('x',first.ticket,result('b')),false);
 const second=r.poll(251)[0];assert.equal(second.body.state.notes,'two');assert.equal(r.receive('x',second.ticket,result('b')),true);
 assert.equal(r.states().x.value.label,'B');assert.equal(r.observe(data('two'),300),false);assert.equal(r.poll(500).length,0);
});
test('same record updates retain resource; navigation and hold invalidate previous requests',()=>{
 const r=new Reactive();r.install(definition);r.observe(data('one'),0);const first=r.poll(100)[0];
 r.install(definition);r.action('reactive_hold','x');assert.equal(r.receive('x',first.ticket,result('b')),false);assert.equal(r.poll(200).length,0);
 r.action('reactive_hold','x');const second=r.poll(201)[0];r.install({...definition,selected:'different'});r.observe(data('one'),300);const third=r.poll(400)[0];
 assert.equal(r.receive('x',second.ticket,result('b')),false);assert.equal(r.receive('x',third.ticket,result('b')),true);
});
test('unavailable and unknown leaves retain content; retry is explicit; pending indicator delayed',()=>{
 const r=new Reactive();r.install(definition);r.observe(data('one'),0);const job=r.poll(100)[0];assert.equal(r.pending(349),false);assert.equal(r.pending(350),true);
 r.receive('x',job.ticket,result('missing'));assert.equal(r.states().x.status,'error');assert.equal(r.states().x.label,'a');assert.equal(r.poll(5000).length,0);
 r.action('reactive_retry','x');const retry=r.poll(5001)[0];r.receive('x',retry.ticket,null,Error('offline'));assert.equal(r.states().x.value.label,'A');
});
test('composition replaces only owned children and leaves caller data intact',()=>{
 const base=[{id:'root',component:'CStack',children:['editor','slot']},{id:'editor',component:'TextField',value:{path:'/draft/notes'}},{id:'slot',component:'CStack',children:[]}];
 const next=compose(base,definition.reactive,{x:{label:'b'}});assert.deepEqual(next[1],base[1]);assert.deepEqual(next[2].children,['x.b']);assert.equal(next.find(n=>n.id==='x.a'),undefined);assert.deepEqual(base[2].children,[]);
});
