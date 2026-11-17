const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/lib/rvbbit/cubes.ts'), 'utf8');
const code = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const calls=[];let fail=false;
const context=vm.createContext({exports:{},fetch:async(url,request)=>{
  const {sql}=JSON.parse(request.body);calls.push(sql);
  return {json:async()=>fail?{ok:false,error:'cube busy'}:{ok:true,rows:[]}};
}});
vm.runInContext(code,context);
(async()=>{
 await context.exports.maintainCube('db',"a'b");
 assert.equal(calls.length,2);assert.match(calls[0],/prepare_cube_refresh\('a''b'\)/);assert.match(calls[1],/maintain_cube/);
 calls.length=0;await context.exports.maintainCube('db','x',{dryRun:true});
 assert.equal(calls.length,1);assert.doesNotMatch(calls[0],/prepare_cube_refresh/);
 calls.length=0;await context.exports.refreshCube('db','x');
 assert.equal(calls.length,2);assert.match(calls[0],/prepare_cube_refresh/);assert.match(calls[1],/refresh_cube/);
 calls.length=0;fail=true;const result=await context.exports.maintainCube('db','x');
 assert.equal(calls.length,1);assert.equal(result.error,'cube busy');
 console.log('Cube refresh: separate awaited repair, read-only dry run, escaped name, failure stops refresh OK');
})().catch(e=>{console.error(e);process.exitCode=1;});
