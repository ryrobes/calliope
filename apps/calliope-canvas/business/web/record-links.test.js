import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readRecord,recordHref} from './record-links.js';
test('record links preserve exact identities and reject cross-app or malformed state',()=>{
  const reference={slug:'field',record:'visit',id:'HV /#? é'};
  const hash='#'+new URLSearchParams({record:JSON.stringify(reference)});
  assert.deepEqual(readRecord(hash,'field'),{reference});
  assert.ok(readRecord(hash,'another').error);
  assert.ok(readRecord('#record={','field').error);
  assert.ok(readRecord('#'+new URLSearchParams({record:JSON.stringify({...reference,state:{}})}),'field').error);
  assert.deepEqual(readRecord('','field'),{});
});

test('record navigation round trips exact identity and rejects executable targets',()=>{
  const reference={slug:'visits',record:'visit',id:'HV / 2 & ? 日本'};
  const url=new URL(recordHref(reference),'https://instance.test');
  assert.equal(url.pathname,'/apps/visits');
  assert.deepEqual(readRecord(url.hash,'visits').reference,reference);
  for(const bad of [null,{...reference,slug:'../admin'},{...reference,sql:'delete'},{...reference,id:{sql:'x'}}])assert.equal(recordHref(bad),null);
});
