import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('./calliope/calliope.js', import.meta.url), 'utf8');
function functionSource(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0, name);
  const rest = source.slice(start + 2);
  const end = rest.search(/\n  (?:async )?function /);
  assert.ok(end >= 0, name);
  return rest.slice(0, end);
}
const image = {id:'generated', kind:'document', turn_id:'calling-turn', title:'Generated image', ordinal:1,
  payload:{mime_type:'image/png', filename:'generated.png', download_url:'/api/calliope/files/generated'}};
const state = {surfaces:[
  image,
  {...image, id:'other-thread', turn_id:'other-turn'},
  {...image, id:'transient', transient:true},
  {...image, id:'document', payload:{mime_type:'text/plain', filename:'notes.txt', download_url:'/api/calliope/files/notes'}},
  {id:'capture', kind:'image', turn_id:'calling-turn', ordinal:2, payload:{image_url:'/api/calliope/surfaces/capture/image'}},
]};
const context = vm.createContext({state, escapeHtml: String});
for (const name of ['documentPreviewKind', 'documentPreviewUrl', 'visualChecksForTurn', 'renderTurnVisualChecks', 'renderDocument']) {
  vm.runInContext(functionSource(name), context);
}
const previews = vm.runInContext('visualChecksForTurn("calling-turn")', context);
assert.deepEqual(Array.from(previews, s => s.id), ['generated','capture']);
assert.equal(previews[0].payload.image_url, '/api/calliope/files/generated?preview=1');
assert.equal(image.payload.image_url, undefined, 'thumbnail view must not mutate stored surfaces');
const chat = vm.runInContext('renderTurnVisualChecks({id:"calling-turn"})', context);
assert.match(chat, /data-chat-image-preview/);
assert.match(chat, /\/api\/calliope\/files\/generated\?preview=1/);
context.image = image;
const stage = vm.runInContext('renderDocument(image)', context);
assert.match(stage, /data-document-image-preview/);
assert.doesNotMatch(stage, /<iframe/);
console.log('Generated image previews: correct turn, durable URLs, Stage image and chat thumbnail OK');
