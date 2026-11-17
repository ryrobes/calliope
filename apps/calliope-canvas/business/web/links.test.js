import test from 'node:test';
import assert from 'node:assert/strict';
import {safeLink} from './links.js';
test('browser links preserve instance paths and reject executable or ambiguous URLs',()=>{
  for(const good of ['/d/visits?job=HV-1001','https://docs.example.test/help'])assert.equal(safeLink(good),good);
  for(const bad of ['',null,'//evil.test','/\\evil.test','javascript:alert(1)','file:///tmp/x','http://elsewhere.test','https://name:secret@site.test','/x\ny','https:evil.test','/x\u007fy'])assert.equal(safeLink(bad),undefined);
});
