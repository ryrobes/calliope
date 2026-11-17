const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/calliope/calliope.js', 'utf8');
const helper = source.match(/  function artifactFrameConfig\([\s\S]*?(?=\n  function renderInstrument)/)[0];
const context = vm.createContext({artifactEmbedUrl: url => url + '?embed=1'});
vm.runInContext(helper, context);
test('only native apps use the dedicated authenticated host', () => {
  const surface = {artifact_slug:'northstar',artifact_version:3,payload:{runtime_kind:'a2ui'}};
  const native = context.artifactFrameConfig(surface, '/untrusted-fallback');
  assert.equal(native.url, '/api/apps/northstar/embed/3');
  assert.ok(native.sandbox.includes('allow-same-origin'));
  assert.equal(native.scrolling, 'auto');
  for (const other of [{...surface,payload:{runtime_kind:'html'}},{...surface,artifact_slug:'../bad'},{...surface,artifact_version:0}]) {
    const frame = context.artifactFrameConfig(other, '/historical');
    assert.equal(frame.url, '/historical?embed=1');
    assert.ok(!frame.sandbox.includes('allow-same-origin'));
  }
});
