import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('./calliope/calliope.js', import.meta.url), 'utf8');
function declaration(name) {
  const start = source.search(new RegExp(`^  (?:async )?function ${name}\\(`, 'm'));
  assert.ok(start >= 0, name);
  const tail = source.slice(start + 1);
  const end = tail.search(/^  (?:async )?function /m);
  return source.slice(start, end < 0 ? undefined : start + 1 + end);
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
function harness(names) {
  const messages = [], calls = [];
  const state = {
    current: { id: 'old', title: 'Old thread' }, sessions: [{ id: 'old', title: 'Old thread' }],
    intake: null, intakeStarting: false, sessionSelectSequence: 0, turnActivityGeneration: 0,
    lastSessionId: 'old', lastSessionsByTab: {}, sessionTab: 'chats', zen: true,
    frontDoor: {}, voice: { pendingTurns: new Set(), failures: new Set() },
    desktop: {}, playbookSavingTurns: new Set(), cubeBuilders: new Map(),
    attachments: [], spatialSelections: [], evidenceSelections: [], composerPlaybooks: [],
    speech: { phase: 'idle' },
  };
  const context = {
    state, console, URL, SETUP_MODE: false, favoriteVersion: 0,
    draft: 'A brand new question', messages, calls,
    els: { send: {}, archiveSession: {}, sessionTitle: {} },
    window: {
      CalliopePlaybookPicker: { withReferences: value => value },
      location: { href: 'https://example.test/calliope?session=old&surface=old-chart&view=zen' },
      history: {
        replaceState(_state, _title, path) { context.window.location.href = new URL(path, context.window.location.href).href; },
        pushState(_state, _title, path) { context.window.location.href = new URL(path, context.window.location.href).href; },
      },
    },
    document: { body: { classList: { contains: () => false } } },
    requestAnimationFrame() {},
    composerValue: () => context.draft,
    validateComposerMessage: () => true,
    toast: text => messages.push(text),
    loadFrontDoor: async () => {}, loadIntakeHome: async () => {},
    visibleSessionTabs: () => [{ id: 'chats' }],
    sessionsForTab: () => state.sessions,
    sessionTabFor: () => 'chats',
    selectSession: async id => { calls.push(id); state.current = { id }; },
    api: async () => ({ sessions: state.sessions }),
  };
  for (const name of [
    'clearComposerRailPicks', 'cancelArtifactTextEdit', 'cancelSpeechRecording', 'stopVoicePlayback', 'clearReplyChoices',
    'clearSpatialSelections', 'clearLiveActivity', 'stopDesktopPolling', 'composerSetDisabled',
    'syncTurnActionControls', 'renderSelected', 'renderEvidenceContextTray', 'renderDesignProfileChip',
    'renderSpatialSelectionTray', 'renderChat', 'renderStage', 'syncEvidenceSearchControls',
    'syncSpeechControls', 'syncGoogleSheetImportControls', 'renderSessionAccess', 'setZenMode',
    'renderChatAgentPicker', 'renderSessions', 'renderIntake', 'setMobilePanel', 'composerFocus',
    'setSessionTabState', 'rememberSession',
  ]) context[name] = () => {};
  vm.createContext(context);
  for (const name of names) vm.runInContext(declaration(name), context);
  return context;
}
const intakeFunctions = ['intakeActive', 'writeLocation', 'setStartLocation', 'setThreadLocation', 'clearSession', 'openIntake'];

for (const hadCurrent of [true, false]) {
  test(`a delayed old notebook cannot replace New chat (current=${hadCurrent})`, async () => {
    const h = harness([...intakeFunctions, 'selectSession']);
    if (!hadCurrent) h.state.current = null;
    const old = deferred(); h.api = () => old.promise;
    const loading = h.selectSession('old');
    h.openIntake();
    old.resolve({ session: { id: 'old' }, turns: [], surfaces: [] });
    assert.equal(await loading, false);
    assert.equal(h.state.current, null);
    assert.ok(h.intakeActive());
    assert.equal(h.draft, 'A brand new question');
    const url = new URL(h.window.location.href);
    assert.equal(url.searchParams.has('start'), false, 'a fresh chat is the plain address');
    assert.equal(url.searchParams.has('session'), false);
    assert.equal(url.searchParams.has('surface'), false);
    assert.equal(url.searchParams.get('view'), 'zen');
  });
}

test('a delayed background list cannot restore an old notebook after New chat', async () => {
  const h = harness([...intakeFunctions, 'loadSessions']);
  const old = deferred(); h.api = () => old.promise;
  const refresh = h.loadSessions('old', true);
  h.openIntake();
  old.resolve({ sessions: [{ id: 'old' }] });
  assert.equal((await refresh).deferred, true);
  assert.equal(h.calls.length, 0);
  assert.ok(h.intakeActive());
});

test('an explicit missing session never falls back to a remembered thread', async () => {
  const h = harness(['intakeActive', 'loadSessions']);
  assert.equal((await h.loadSessions('new')).deferred, true);
  assert.equal(h.calls.length, 0);
});

test('new-thread startup can populate the rail without opening the remembered thread', async () => {
  const h = harness(['intakeActive', 'loadSessions']);
  h.state.current = null;
  await h.loadSessions(null, false, { listOnly: true });
  assert.equal(h.calls.length, 0);
  assert.equal(h.state.current, null);
});

test('a list load without an address never restores a remembered thread', async () => {
  const h = harness(['intakeActive', 'loadSessions', 'writeLocation', 'setStartLocation', 'openIntake', 'clearSession']);
  h.state.current = null; h.state.lastSessionId = 'old';
  h.window.location.href = 'https://example.test/calliope?view=zen';
  await h.loadSessions();
  assert.deepEqual(h.calls, [], 'nothing opens from memory');
  assert.ok(h.intakeActive(), 'the plain address is a fresh chat');
  assert.equal(new URL(h.window.location.href).search, '?view=zen');
});

test('the address names the open thread and New chat is a step Back can undo', async () => {
  const h = harness(['intakeActive', 'writeLocation', 'setStartLocation', 'setThreadLocation', 'openIntake', 'clearSession']);
  const pushes = [];
  h.window.history.pushState = (_s, _t, path) => { pushes.push(path); h.window.location.href = new URL(path, h.window.location.href).href; };
  h.window.location.href = 'https://example.test/calliope?view=zen&start=1';
  h.setThreadLocation('created', { replace: true });
  assert.equal(new URL(h.window.location.href).search, '?view=zen&session=created', 'the first message names the new thread in place');
  h.setThreadLocation('other');
  assert.deepEqual(pushes, ['/calliope?view=zen&session=other'], 'opening another thread adds a history step');
  h.window.location.href = 'https://example.test/calliope?view=zen&session=other&surface=chart';
  h.state.current = { id: 'other' };
  h.openIntake();
  assert.deepEqual(pushes.at(-1), '/calliope?view=zen', 'New chat drops the id and surface');
  h.setThreadLocation('other');
  h.setThreadLocation('other');
  assert.equal(pushes.length, 3, 'reopening the same thread does not stack history');
});

test('first turn opens the exact created session, without consulting a stale list', async () => {
  const h = harness([...intakeFunctions, 'beginIntakeTurn']);
  h.openIntake();
  const creation = deferred(); let creates = 0;
  h.createSession = () => { creates++; return creation.promise; };
  h.loadSessions = () => { throw Error('Session list must not decide the new destination'); };
  h.selectSession = async (id, options) => {
    assert.equal(options.intakeLaunch, h.state.intake);
    h.calls.push(id); h.state.current = { id }; h.state.intake = null;
    return true;
  };
  h.applyDesignProfileToSession = async () => {};
  h.api = async url => { assert.ok(url.endsWith('/new')); return { session: { id: 'new' } }; };
  h.sendTurn = async options => {
    assert.equal(options.expectedSessionId, 'new');
    assert.equal(h.state.intakeStarting, false, 'Navigation unlocks before waiting for the agent response');
    h.calls.push('send:new');
  };
  const start = h.beginIntakeTurn();
  await h.beginIntakeTurn();
  creation.resolve({ id: 'new', title: 'New thread' });
  await start;
  assert.equal(creates, 1);
  assert.deepEqual(h.calls, ['new', 'send:new']);
  assert.equal(h.state.intakeStarting, false);
  assert.equal(h.state.sessions[0].id, 'new');
});

test('an unsuccessful new-thread load keeps the draft and does not send', async () => {
  const h = harness([...intakeFunctions, 'beginIntakeTurn']);
  h.openIntake();
  h.createSession = async () => ({ id: 'new' });
  h.selectSession = async () => false;
  h.sendTurn = async () => assert.fail('Must not send');
  await h.beginIntakeTurn();
  assert.ok(h.intakeActive());
  assert.equal(h.state.current, null);
  assert.equal(h.draft, 'A brand new question');
  assert.match(h.messages[0], /new notebook did not open/);
});

test('background selections and manual sends cannot interrupt first-turn setup', async () => {
  const h = harness(['selectSession', 'sendTurn']);
  h.state.intakeStarting = true;
  h.api = async () => assert.fail('No navigation/request is allowed');
  assert.equal(await h.selectSession('old'), false);
  await h.sendTurn();
});

test('a first-turn send refuses a different current notebook', async () => {
  const h = harness(['sendTurn']);
  h.api = async () => assert.fail('Never send to old');
  await h.sendTurn({ expectedSessionId: 'new' });
  assert.match(h.messages[0], /has not been sent/);
  assert.equal(h.draft, 'A brand new question');
});

test('Data-only intake creates once, preserves choices, and never sends a turn', async () => {
  const h = harness([...intakeFunctions, 'beginIntakeTurn']);
  h.openIntake({agentKey:'cassandra'}); h.draft='';
  h.state.intake.teamMode=false; h.state.intake.designVersionId='chosen-design';
  const creation=deferred(); let creates=0;
  h.createSession = ({agentKey}) => { assert.equal(agentKey,'cassandra'); creates++; return creation.promise; };
  h.selectSession = async id => { h.state.current={id}; h.state.intake=null; return true; };
  h.applyDesignProfileToSession = async id => assert.equal(id,'chosen-design');
  h.api = async (url, options) => {
    assert.equal(url,'/api/calliope/sessions/data-notebook');
    assert.deepEqual(JSON.parse(options.body),{team_mode:false,authoring_mode:'html'});
    return {session:{id:'data-notebook'}};
  };
  h.selectWorkspaceView = (view, options) => { assert.equal(options.focus,true); h.calls.push(view); };
  h.sendTurn = async () => assert.fail('Data entry must not send');
  const launch=h.beginIntakeTurn({openData:true});
  await h.beginIntakeTurn({openData:true});
  creation.resolve({id:'data-notebook'}); await launch;
  assert.equal(creates,1); assert.deepEqual(h.calls,['data']);
  assert.equal(h.state.intakeStarting,false);
});

for (const content of ['text','attachment','playbook']) {
  test(`Data-only intake refuses to consume existing ${content}`, async () => {
    const h=harness([...intakeFunctions,'beginIntakeTurn']); h.openIntake(); h.draft='';
    if(content==='text')h.draft='Keep my message';
    if(content==='attachment')h.state.attachments=[{name:'image.png'}];
    if(content==='playbook')h.state.composerPlaybooks=[{ref_id:'book'}];
    h.createSession=async()=>assert.fail('Do not start Data with a draft');
    await h.beginIntakeTurn({openData:true});
    assert.ok(h.intakeActive());
  });
}
