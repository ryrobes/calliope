import test from 'node:test';
import assert from 'node:assert/strict';
import {remember,hints,acknowledged,updateProblem,Journal,definitivelyRejected} from './published-recovery.js';
const snapshot={version:1,forms:[{path:'/state/form',source:'/record',key:'id'}],messages:[{updateDataModel:{value:{record:{id:1,note:'Saved'},state:{form:{id:1,note:'Saved'}}}}}]};
test('drafts stay on their version and survive navigating to another record',()=>{
 const records=remember(snapshot,{state:{form:{id:1,note:'Unsaved'}}});
 assert.match(updateProblem(records,null),/original version/);
 assert.deepEqual(hints(records,2),[]);
 const second=structuredClone(snapshot);second.messages[0].updateDataModel.value.record={id:2,note:'Other'};
 assert.equal(remember(second,{state:{form:{id:2,note:'Other'}}},records).length,1);
 assert.equal(acknowledged(records,snapshot,{resets:['/state/form']},{state:{form:{id:1}}}).length,0);
 assert.equal(remember(snapshot,{state:{form:{id:1,note:'Saved'}}},records).length,0);
});
test('pending request persists before network and is restored unchanged after reload',async()=>{
 const storage=new Map(),write=async value=>storage.set(value.id,structuredClone(value));
 let journal=new Journal(async key=>storage.get(key),write);
 const pending={event:'save',version:1,request_key:'same-key',state:{form:{id:1,note:'edit'}}};
 const first=journal.save('alice','app',{version:1,pending:null});
 const second=journal.save('alice','app',{version:1,pending});await Promise.all([first,second]);
 journal=new Journal(async key=>storage.get(key),write);
 assert.deepEqual((await journal.get('alice','app')).pending,pending);
 assert.equal(await journal.get('bob','app'),undefined);
 assert.match(updateProblem([],pending),/pending/);
 assert.equal(definitivelyRejected('UNKNOWN'),false);
 assert.equal(definitivelyRejected('CONFLICT'),true);
});

test('another tab cannot overwrite a newer journal or pending command',async()=>{
 const storage=new Map();const read=async id=>structuredClone(storage.get(id));
 const write=async(value,revision)=>{assert.equal(storage.get(value.id)?.revision||0,revision,'draft changed in another tab');storage.set(value.id,structuredClone(value));};
 const a=new Journal(read,write),b=new Journal(read,write);
 await a.get('alice','app');await b.get('alice','app');
 await a.save('alice','app',{version:1,pending:{request_key:'original-key'}});
 await assert.rejects(b.save('alice','app',{version:2,pending:null}),/another tab/);
 assert.equal((await a.get('alice','app')).pending.request_key,'original-key');
});
