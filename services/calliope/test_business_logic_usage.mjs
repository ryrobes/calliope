import test from 'node:test';
import assert from 'node:assert/strict';
import {usageTracker,costLabel,usageDetail} from './calliope/business-logic-usage.mjs';

test('pending cost never displays as a free run; cache is explicit',()=>{
 assert.equal(costLabel({captured:true,pending:true,known_usd:0}),'Pending');
 assert.equal(costLabel({captured:true,pending:false,known_usd:0}),'$0.000000');
 assert.equal(costLabel({captured:true,pending:false,known_usd:.000000012}),'$0.000000012');
 assert.equal(costLabel({captured:true,pending:true,known_usd:.004}),'$0.004000 + pending');
 assert.match(usageDetail({captured:true,decisions:1,cached:1,known_usd:0,steps:[{node:'root',question:'<unsafe>',cache:'l2',known_usd:0}]}),/RVBBIT receipt cache/);
 assert.doesNotMatch(usageDetail({steps:[{question:'<unsafe>'}]}),/<unsafe>/);
});
test('late costs update and settled runs stop polling',async()=>{
 let callback,requests=0;const changes=[];
 const tracker=usageTracker({fetchUsage:async id=>(requests++,{id,pending:false,known_usd:.003}),updated:x=>changes.push(x),schedule:cb=>(callback=cb,1),cancel:()=>{}});
 tracker.track({id:'a',pending:true});await callback();
 assert.equal(requests,1);assert.equal(changes.at(-1).known_usd,.003);assert.equal(tracker.get('a').pending,false);
});
test('stale responses cannot attach to a new batch after clear',async()=>{
 let callback,resolve;const changes=[];
 const tracker=usageTracker({fetchUsage:()=>new Promise(r=>resolve=r),updated:x=>changes.push(x),schedule:cb=>(callback=cb,1),cancel:()=>{}});
 tracker.track({id:'old',pending:true});const pending=callback();tracker.clear();tracker.track({id:'new',pending:false});resolve({id:'old',pending:false});await pending;
 assert.equal(tracker.get('old'),undefined);assert.equal(changes.at(-1).id,'new');
});
test('a failed accounting refresh keeps pending data and retries',async()=>{
 let callback,attempts=0;
 const tracker=usageTracker({fetchUsage:async id=>{if(!attempts++)throw Error('offline');return {id,pending:false};},updated:()=>{},schedule:cb=>(callback=cb,1),cancel:()=>{}});
 tracker.track({id:'a',pending:true});await callback();assert.equal(tracker.get('a').pending,true);
 await callback();assert.equal(tracker.get('a').pending,false);assert.equal(attempts,2);
});
