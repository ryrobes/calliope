import assert from 'node:assert/strict';
import './calliope/floor-stream.js';
const queued=[],events=[];
const stream=new FloorStream((params,signal)=>new Promise((resolve,reject)=>queued.push({params,signal,resolve,reject})),(data,event)=>events.push({data,event}));
const page=(id,next=null)=>({cards:[{item_id:id,column_state:'done'}],total:2600,lanes:[{column_state:'done',pieces:2600,matched:2600}],pages:{done:{next_cursor:next,has_more:!!next}},facets:{}});
const old=stream.reset({q:'old'});
const fresh=stream.reset({q:'new',as_of:'2026-01-01T00:00:00Z'});
assert.equal(queued[0].signal.aborted,true);
queued[1].resolve(page('new','cursor'));await fresh;
queued[0].resolve(page('old'));await old;
assert.equal(stream.data.cards[0].item_id,'new');
const more=stream.request('done');await stream.request('done');
assert.equal(queued.length,3);assert.equal(queued[2].params.as_of,'2026-01-01T00:00:00Z');
queued[2].resolve({...page('new'),cards:[{item_id:'new'},{item_id:'older'}]});await more;
assert.deepEqual(stream.data.cards.map(c=>c.item_id),['new','older']);
const replacement=stream.reset({});queued[3].resolve(page('live','next'));await replacement;
const failure=stream.request('done');queued[4].reject(new Error('offline'));await failure;
assert.equal(stream.data.cards[0].item_id,'live');assert.equal(events.at(-1).event.error.message,'offline');
const retry=stream.request('done');queued[5].resolve(page('recovered'));await retry;
assert.deepEqual(stream.data.cards.map(c=>c.item_id),['live','recovered']);
console.log('Floor transport: stale frame/search rejection, AS-OF continuation, duplicate suppression, and retry passed');

const currentRevision=stream.revision;
const originalIds=stream.data.cards.map(c=>c.item_id);
assert.equal(stream.applyPresentations([{item_id:'live',semantic_title:'Enriched project',semantic_status:'Ready'}],currentRevision),true);
assert.equal(stream.data.cards[0].semantic_title,'Enriched project');
assert.deepEqual(stream.data.cards.map(c=>c.item_id),originalIds);
assert.equal(stream.applyPresentations([{item_id:'unauthorized',semantic_title:'Private'}],currentRevision),false);
stream.cancel();
assert.equal(stream.applyPresentations([{item_id:'live',semantic_title:'Late overwrite'}],currentRevision),false);
assert.equal(stream.data.cards[0].semantic_title,'Enriched project');
console.log('Floor labels: in-place update, no card insertion, and stale navigation rejection passed');
const kept=stream.data;
const resetPending=stream.reset({as_of:'2026-01-01T00:00:00Z'});
assert.equal(stream.data,kept,'Keep committed data while a frame is pending');
const requestsBefore=queued.length;await stream.request('done');
assert.equal(queued.length,requestsBefore,'Do not append old pages during frame replacement');
queued.at(-1).reject(new Error('historical frame unavailable'));await resetPending;
assert.equal(stream.data,kept,'A failed frame retains the last committed data');
console.log('Floor scrubber: atomic frame replacement and error retention passed');

// A viewport can request the next page directly inside onChange. Completing
// the previous request must not erase the newly registered in-flight request.
const continuation=[];let flow;
flow=new FloorStream((params,signal)=>new Promise(resolve=>continuation.push({params,resolve})),(data,event)=>{
  if(!event.loading&&!event.error&&data.pages.done?.has_more)flow.request('done');
});
const initialFlow=flow.reset({});continuation[0].resolve(page('a','next-a'));await initialFlow;
assert.equal(continuation.length,2);
assert.equal(flow.pending.has('done'),true);
await flow.request('done');assert.equal(continuation.length,2);
continuation[1].resolve(page('b','next-b'));await new Promise(resolve=>setImmediate(resolve));
assert.equal(continuation.length,3);assert.equal(flow.pending.has('done'),true);
continuation[2].resolve(page('c'));await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(flow.data.cards.map(c=>c.item_id),['a','b','c']);assert.equal(flow.pending.size,0);
console.log('Floor continuation: immediate automatic paging preserves in-flight guards');
