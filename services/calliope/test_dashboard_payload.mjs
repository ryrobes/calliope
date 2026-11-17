import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

for (const file of ['dashboard_template.html','examples/tanstack-charts-dashboard.html']) {
  const html = fs.readFileSync(new URL(file, import.meta.url), 'utf8');
  const normalize = html.slice(html.indexOf('function rowsToObjects('), html.indexOf('function bridgeResult('));
  // The TanStack example has only rowsToObjects here; the main template also has comments.
  const helpers = html.slice(html.indexOf('function bridgeResult('), html.indexOf('\n/* --- chart + table helpers') < 0
    ? html.indexOf('const runSql = makeRunSql(SERVER_ID);') : html.indexOf('\n/* --- chart + table helpers'));
  function runtime(window) {
    const ctx = vm.createContext({window, setTimeout});
    vm.runInContext(`const SERVER_ID='test'; ${normalize}\n${helpers}\nconst runSql=makeRunSql(SERVER_ID); globalThis.compose=composePayload;`,ctx);
    return ctx.compose;
  }
  const parts={fast:{sql:'fast',shape:'row'},slow:{sql:'slow',shape:'row'}};
  test(`${file}: hosted parts stream independently and never replay a failure`,async()=>{
    const calls=[], notices=[]; let finish;
    const compose=runtime({rvbbitQuery:async sql=>{
      calls.push(sql);
      if(sql==='slow') await new Promise(resolve=>finish=resolve);
      return sql==='slow'?{error:{message:'statement timeout'}}:{columns:['value'],rows:[[7]]};
    },cowork:{callMcpTool(){throw Error('must use hosted bridge');}}});
    const pending=compose(parts,{onPart:(name,result)=>notices.push([name,result])});
    await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(calls,['fast','slow']);
    assert.equal(notices[0][0],'fast'); assert.equal(notices[0][1].data.value,7);
    finish();
    await assert.rejects(pending,error=>error.code==='QUERY_ERROR' && error.results.fast.value===7 && error.errors.slow.message==='statement timeout');
    assert.equal(calls.length,2);
  });
  test(`${file}: batch preserves successes and identifies failed parts`,async()=>{
    let calls=0;
    const compose=runtime({cowork:{callMcpTool:async()=>{
      calls++;return {structuredContent:{results:{fast:{rows:[{value:7}]},slow:{error:{message:'SQL failed'}}}}};
    }}});
    await assert.rejects(compose(parts),e=>e.results.fast.value===7 && e.errors.slow.message==='SQL failed');
    assert.equal(calls,1);
  });
  test(`${file}: only missing batch capability falls back`,async()=>{
    const calls=[];
    const compose=runtime({cowork:{callMcpTool:async(tool,args)=>{
      calls.push(tool);
      if(tool.endsWith('run_sql_multi'))return {isError:true,structuredContent:{error:{code:-32601,message:'Unknown tool: run_sql_multi'}}};
      return {structuredContent:{columns:['value'],rows:[[args.sql]]}};
    }}});
    const result=await compose(parts);
    assert.equal(result.fast.value,'fast'); assert.equal(result.slow.value,'slow');assert.equal(calls.length,3);
  });
  for(const failure of ['timeout','bad_response','permission']) test(`${file}: ${failure} never replays the batch`,async()=>{
    let calls=0;
    const compose=runtime({cowork:{callMcpTool:async()=>{
      calls++;
      if(failure==='timeout')throw Error('statement timeout');
      if(failure==='permission')return {isError:true,structuredContent:{error:{code:'FORBIDDEN',message:'access denied'}}};
      return {};
    }}});
    await assert.rejects(compose(parts));assert.equal(calls,1);
  });
}
