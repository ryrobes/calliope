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
const agents = [
  {key: 'calliope', name: 'Calliope'},
  {key: 'platform-identity', name: 'Aletheia', signature_font: 'Italiana'},
  {key: 'dashboard-studio', name: 'Iris', avatar_url: '/iris.png'},
];
const state = {current: {specialist_key: 'platform-identity'}, voice: {}, surfaces: [], turns: [
  {id: 'turn', turn_kind: 'setup_conductor', status: 'complete', assistant_message: 'Hello'},
]};
const els = {messages: {innerHTML: ''}, chatEmpty: {}};
const context = vm.createContext({state, els, window: {}, chatAgents: () => agents, escapeHtml: String,
  zenDeliveredRounds: () => new Map(), zenThreadEventsByTurn: () => new Map(),
  zenExchangeRows: () => [], voicePresentationPending: () => false, voicePresentationEnabled: () => false,
  renderTurnReceipt: () => '', assistantBody: t => t.assistant_message, turnClockMarkup: () => '',
  turnAwaitingAgents: () => false, relativeTime: () => 'now', renderVoiceControl: () => '', renderTurnVisualChecks: () => '',
  renderSavePlaybookAction: () => '', zenStageLinks: () => '', captureStageInputDrafts: () => ({}),
  restoreStageInputDrafts() {}, hydratePersonAvatars() {}, hydrateChatImagePreviews() {},
  syncTurnClocks() {}, renderTokenMeter() {}, renderChatDecisions() {},
});
for (const name of ['chatAgent', 'signatureAgent', 'signatureKey', 'signatureAttr',
  'calliopeAgentAvatarMarkup', 'specialistAvatarUrl', 'specialistAgentAvatarMarkup',
  'zenAgentAvatar', 'zenCalliopeAvatar', 'zenAgentNameMarkup', 'zenChatIdentity', 'zenRow', 'renderChatZen']) {
  vm.runInContext(functionSource(name), context);
}
function render(key) {
  state.current.specialist_key = key;
  vm.runInContext('renderChatZen()', context);
  return els.messages.innerHTML;
}
const placeholder = render('platform-identity');
assert.match(placeholder, /zen-av specialist[^>]*title="Aletheia"[^>]*><i /);
assert.match(placeholder, /zen-name-agent[^>]*data-agent-signature-key="platform-identity"[^>]*>Aletheia/);
assert.match(placeholder, /data-zen-author="agent:Aletheia"/);
assert.doesNotMatch(placeholder, /callie-avatar|zen-name-calliope/);
agents[1].avatar_url = '/aletheia-generated.png';
assert.match(render('platform-identity'), /src="\/aletheia-generated.png"/);
assert.match(render('dashboard-studio'), /src="\/iris.png"/);
const primary = render('calliope');
assert.match(primary, /callie-avatar-(day|night).jpg/);
assert.match(primary, /zen-name-calliope[^>]*>Calliope/);
assert.match(primary, /data-zen-author="calliope"/);
console.log('Direct chat identity: dynamic placeholder, generated portrait, signature key, built-in and Calliope OK');
