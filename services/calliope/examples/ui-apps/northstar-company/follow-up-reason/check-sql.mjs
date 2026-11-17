const {PGlite}=await import(process.env.PGLITE_MODULE||'@electric-sql/pglite');
import {fileURLToPath} from 'node:url';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const work=fileURLToPath(new URL('.',import.meta.url)).replace(/\/$/,'');
const root=fileURLToPath(new URL('..',import.meta.url)).replace(/\/$/,'');
const read=p=>fs.readFileSync(p,'utf8'), parsed=p=>JSON.parse(read(p));
const db=new PGlite();
await db.exec('CREATE SCHEMA calliope_apps;');
await db.exec(read(root+'/setup.sql'));
const snapshot=async()=>JSON.stringify((await db.query('SELECT to_jsonb(r) AS row FROM calliope_apps.northstar_reports r ORDER BY id')).rows);
const before=JSON.parse(await snapshot());
await db.exec(read(work+'/migration.sql'));await db.exec(read(work+'/migration.sql'));
const after=JSON.parse(await snapshot());
assert.deepEqual(after.map(x=>{const r={...x.row};assert.equal(r.follow_up_reason,null);delete r.follow_up_reason;return {row:r};}),before);
const old=parsed(root+'/northstar-my-visits.manifest.json').actions.submit_report;
const next=parsed(work+'/northstar-my-visits.manifest.json').actions.submit_report;
const reportSql=read(work+'/report-summary.sql');
const actor='northstar-technician@local.test';
const draft={id:'NS-2001',version:1,notes:'Isolated PGlite fixture submission; never sent to Lab.',attachments:[]};
const key='33333333-3333-4333-8333-333333333333';
const results=[];
async function action(def,d,who=actor){
 for(const step of def.steps){
  const args=step.params.map(p=>typeof p==='object'&&p.actor?who:p==='draft'?d:p==='key'?key:{});
  const r=await db.query(step.sql,args);
  assert.equal(r.rows.length,step.expect_rows,'expect_rows guard');
 }
}
async function test(name,fn){await db.exec('BEGIN');try{await fn();results.push({name,passed:true});}finally{await db.exec('ROLLBACK');}}
await test('v2 insert survives additive migration and stores null',async()=>{await action(old,draft);assert.equal((await db.query('SELECT follow_up_reason FROM calliope_apps.northstar_reports WHERE id=$1',[key])).rows[0].follow_up_reason,null);});
for(const reason of ['No follow-up','Parts needed','Return diagnostic visit','Customer unavailable','Other']){
 await test('v3 stores exact choice: '+reason,async()=>{
  await action(next,{...draft,follow_up_reason:[reason]});
  assert.equal((await db.query('SELECT follow_up_reason FROM calliope_apps.northstar_reports WHERE id=$1',[key])).rows[0].follow_up_reason,reason);
  const summary=(await db.query(reportSql,['admin@example.com'])).rows[0];
  assert.equal(summary.report_count,4);assert.equal(summary.not_recorded_count,3);
  assert.equal(summary.breakdown.find(b=>b.reason===reason).report_count,1);
 });
}
for(const [name,value] of [['omitted',undefined],['empty',[]],['null',null]]) await test('v3 nullable compatibility: '+name,async()=>{await action(next,{...draft,follow_up_reason:value});assert.equal((await db.query('SELECT follow_up_reason FROM calliope_apps.northstar_reports WHERE id=$1',[key])).rows[0].follow_up_reason,null);});
for(const [name,value] of [['unknown',['Invented reason']],['multiple',['Other','Parts needed']],['string','Other'],['nonstring',[42]],['object',{}]]) await test('reject invalid reason: '+name,async()=>{await assert.rejects(action(next,{...draft,follow_up_reason:value}));assert.equal((await db.query('SELECT version FROM calliope_apps.northstar_visits WHERE id=$1',[draft.id])).rows[0].version,1);});
await test('reject foreign actor',async()=>{await assert.rejects(action(next,{...draft,follow_up_reason:['Other']},'northstar-other@local.test'));});
await test('reject author dispatcher submission',async()=>{await assert.rejects(action(next,{...draft,follow_up_reason:['Other']},'admin@example.com'));});
await test('reject stale version',async()=>{await assert.rejects(action(next,{...draft,version:0,follow_up_reason:['Other']}));});
await test('request replay cannot append twice',async()=>{await action(next,{...draft,follow_up_reason:['Other']});await assert.rejects(action(next,{...draft,version:2,follow_up_reason:['Other']}));assert.equal((await db.query('SELECT count(*)::integer n FROM calliope_apps.northstar_reports WHERE request_id=$1',[key])).rows[0].n,1);});
await test('old reports all remain Not recorded, never No follow-up',async()=>{const s=(await db.query(reportSql,['admin@example.com'])).rows[0];assert.equal(s.report_count,3);assert.equal(s.not_recorded_count,3);assert.equal(s.breakdown.find(x=>x.reason==='No follow-up').report_count,0);});
await test('non-dispatcher summary returns no report counts',async()=>{const s=(await db.query(reportSql,[actor])).rows[0];assert.equal(s.authorized,false);assert.equal(s.report_count,0);assert.deepEqual(s.breakdown,[]);});
const evidence={engine:'PGlite PostgreSQL WASM; disposable in-memory fixture only. No Lab SQL, actions, grants or records mutated.',migration_idempotent:true,existing_report_fields_unchanged:true,results};
fs.writeFileSync(work+'/evidence/sql-tests.json',JSON.stringify(evidence,null,2));
console.log(JSON.stringify(evidence,null,2));await db.close();
