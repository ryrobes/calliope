#!/usr/bin/env node
/* An adapter supplies live browser observations or an explicitly offline data
 * test. Never infer correctness from CSS, screenshots, or query boot counts. */
const {createHash} = require('node:crypto');
function canonical(value) {
  if (value === undefined) throw Error('Missing data observation');
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])]));
  if (typeof value === 'number' && !Number.isFinite(value)) throw Error('Non-finite observation');
  if (!['string','number','boolean'].includes(typeof value) && value !== null) throw Error('Observation must be JSON data');
  return value;
}
function digest(value) {return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');}
async function check(adapter) {
  if (!adapter.artifact?.slug || !Number.isInteger(adapter.artifact.version) || adapter.artifact.version<1)
    throw Error('Bind checks to the exact published artifact slug and version');
  if (!['live-data','offline-fixture'].includes(adapter.basis)) throw Error('Declare live-data or offline-fixture evidence');
  if (!Array.isArray(adapter.cases) || !adapter.cases.length) throw Error('No interaction cases');
  const results=[];
  for (const c of adapter.cases) {
    const result={id:c.id,status:'failed',checks:[]}; results.push(result);
    let baseline;
    try {
      if (!c.id || !c.affected?.length || !c.observation) throw Error('Each case needs id, affected panels and a description of observed rows/counts');
      await c.clear(); await c.settle(); baseline=canonical(await c.snapshot());
      await c.select(); await c.settle(); const selected=canonical(await c.snapshot());
      for (const panel of c.affected) result.checks.push({panel,kind:'data_changed',passed:digest(baseline[panel])!==digest(selected[panel])});
      for (const panel of c.unchanged || []) result.checks.push({panel,kind:'data_preserved',passed:digest(baseline[panel])===digest(selected[panel])});
      // Exact predicates/expected counts supplement change detection. A random
      // change alone is not a correctness claim.
      if (typeof c.verify !== 'function') throw Error('Supply verify(baseline, selected) for expected rows/counts/denominators');
      result.checks.push({kind:'expected_data',passed:(await c.verify(baseline,selected))===true});
      result.observation=c.observation;
      result.baseline_sha256=digest(baseline); result.selected_sha256=digest(selected);
    } catch (e) {result.error=String(e.message || e);}
    finally {
      try {
        await c.clear(); await c.settle(); const restored=canonical(await c.snapshot());
        result.checks.push({kind:'deselection_restores_data',passed:baseline!==undefined && digest(baseline)===digest(restored)});
      } catch(e) {result.restore_error=String(e.message || e);}
    }
    if (!result.error && !result.restore_error && result.checks.every(c=>c.passed)) result.status='passed';
  }
  return {schema_version:'calliope.interaction-checks.v1',artifact:adapter.artifact,basis:adapter.basis,
    checked_at:new Date().toISOString(),status:results.every(r=>r.status==='passed')?'passed':'failed',cases:results};
}
module.exports={check,digest};
if (require.main===module) {
  const {resolve}=require('node:path');
  (async()=>{
    if (process.argv.length!==3) throw Error('Usage: calliope-interaction-check /absolute/path/to/adapter.cjs');
    const adapter=require(resolve(process.argv[2]));
    const result=await check(adapter);
    process.stdout.write(JSON.stringify(result,null,2)+'\n');
    if (result.status!=='passed')process.exitCode=1;
  })().catch(e=>{process.stderr.write(String(e.message || e)+'\n');process.exitCode=2;});
}
