const test=require('node:test'),assert=require('node:assert/strict');
const {check}=require('./check.cjs');
function adapter({change=true,leak=false,restore=true}={}) {
  let selected=false;
  return {artifact:{slug:'test',version:3},basis:'offline-fixture',cases:[{
    id:'source-season',observation:'Rows in both archives, fixed national denominator',affected:['a'],unchanged:['b','reference'],
    select:async()=>{selected=true;},clear:async()=>{if(restore)selected=false;},settle:async()=>{},
    snapshot:async()=>({a:change&&selected?[1]:[1,2],b:leak&&selected?[]:[3,4],reference:4}),
    verify:(base,now)=>now.a.length===1&&now.a[0]===1&&now.b.length===2&&now.reference===4,
  }]};
}
test('data changes, source scope and restoration pass',async()=>assert.equal((await check(adapter())).status,'passed'));
test('cosmetic-only filter fails even if select succeeds',async()=>assert.equal((await check(adapter({change:false}))).status,'failed'));
test('cross-source leakage fails',async()=>assert.equal((await check(adapter({leak:true}))).status,'failed'));
test('failure to clear remains visible',async()=>assert.equal((await check(adapter({restore:false}))).status,'failed'));
test('missing observation cannot pass',async()=>{const a=adapter();a.cases[0].snapshot=async()=>({});assert.equal((await check(a)).status,'failed');});
