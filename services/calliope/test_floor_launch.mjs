import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const source = readFileSync(new URL('./calliope/floor.js', import.meta.url), 'utf8');
const starterSource = source.slice(source.indexOf('async function beginFloorStarter('), source.indexOf('\nfunction stagePrimary'));
for (const kind of ['dream', 'artifact', 'metric', 'suggestion']) {
  const requests = [], handoffs = [], destinations = [];
  const context = vm.createContext({
    state: {starterLaunching: false}, URL, Date, Math, console,
    showToast() {}, sendSparkEvents() {},
    api: async (url, options) => {
      requests.push(JSON.parse(options.body));
      return {session: {id: 'new-session'}, surface: {id: 'pinned'}, url: '/calliope?session=new-session&prompt=Run'};
    },
    writeFloorIntakeHandoff: async value => handoffs.push(value),
    window: {location: {origin: 'http://localhost', assign: value => destinations.push(value)}},
  });
  vm.runInContext(starterSource, context);
  await context.beginFloorStarter({title: 'Idea', prompt: 'Run it', action: {kind, id: 'dream', slug: 'artifact', name: 'metric'}});
  // Dream handoff now opens the prepared notebook directly.
  assert.equal(handoffs.length, kind === 'dream' ? 0 : 1);
  if (kind !== 'dream') assert.equal(handoffs[0].team_mode, true);
  if (kind !== 'suggestion') assert.equal(requests[0].origin, 'floor');
  assert.equal(destinations.length, 1);
}
assert.ok(!source.includes('team_mode:false'), 'Direct Floor actions must not force solo mode');
assert.ok(source.includes('team_mode:state.intakeTeamMode'), 'New Chat retains its explicit opt-out');
console.log('Floor launch: ideas, artifacts, metrics, suggestions default to Teams; New Chat opt-out preserved');
const chat = readFileSync(new URL('./calliope/calliope.js', import.meta.url), 'utf8');
const assignment = chat.match(/state\.intake\.teamMode = [^;]*floorIntake\.team_mode[^;]*;/)?.[0];
assert.ok(assignment);
for (const [handoff, expected] of [[{}, true], [{team_mode: true}, true], [{team_mode: false}, false]]) {
  const context = {state: {intake: {}}, floorIntake: handoff};
  vm.runInNewContext(assignment, context);
  assert.equal(context.state.intake.teamMode, expected);
}
console.log('Chat handoff: missing choice defaults On; explicit New Chat Off remains Off');
assert.ok(!chat.includes('state.intake.authoringMode'), 'Old handoffs must not re-enable Dashboard');
assert.ok(!chat.includes('dashboard-mode-toggle'));
assert.ok(source.includes("authoring_mode:'html'"));
assert.ok(source.includes('intakeDesignVersionId:null'));
console.log('Floor and chat start unpinned and use HTML; stale Dashboard choices are ignored');
