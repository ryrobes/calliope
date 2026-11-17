import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const context=vm.createContext({window:{}});
vm.runInContext(readFileSync(new URL('./calliope/computer.js',import.meta.url),'utf8'),context);
const seek=context.window.CalliopeComputer.prototype.replayPosition;
const spans=[{source_start:0,source_end:2,replay_start:0,replay_end:2},
             {source_start:28,source_end:32,replay_start:2,replay_end:6},
             {source_start:60,source_end:62,replay_start:6,replay_end:8}];
for(const [at,seconds,skipped] of [[1,1,false],[15,2,true],[29,3,false],[32,6,true],[60,6,false],[100,7.5,true]]) {
  const result=seek(spans,at);
  assert.equal(result.seconds,seconds);
  assert.equal(result.skipped,skipped);
}
assert.equal(seek([],20).seconds,0);
console.log('Receipt seeking preserves source time across idle cuts and capture gaps.');
