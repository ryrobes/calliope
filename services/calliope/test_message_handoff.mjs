import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('./calliope/calliope.js',import.meta.url),'utf8');
const start=source.indexOf('  async function readFloorIntakeHandoff()');
const code=source.slice(start,source.indexOf('  async function clearFloorIntakeHandoff(',start));
const prompt='Order reconciliation notes\n'.repeat(2500)+'END OF REQUEST';
for (const legacy of [false,true]) {
  const value={id:'draft',prompt,created_at:Date.now()};
  let closed=false;
  const context=vm.createContext({SETUP_MODE:false,FLOOR_INTAKE_STORAGE_KEY:'key',FLOOR_INTAKE_DB_STORE:'drafts',Date,
    sessionStorage:{getItem:()=>legacy?JSON.stringify(value):'draft'},
    clearFloorIntakeHandoff:async()=>assert.fail('Valid draft must survive'),
    openFloorIntakeDb:async()=>({close(){closed=true},transaction:()=>({objectStore:()=>({get:()=>{
      const request={result:value};queueMicrotask(()=>request.onsuccess());return request;
    }})})}),
  });
  vm.runInContext(code,context);
  assert.equal((await context.readFloorIntakeHandoff()).prompt,prompt);
  assert.equal(closed,!legacy);
}
console.log('Long Floor drafts survive both IndexedDB and legacy handoffs intact.');
