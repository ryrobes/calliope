import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const source = readFileSync(new URL('./calliope/calliope.js', import.meta.url), 'utf8');
const functions = source.slice(source.indexOf('  function resolveVoicePlaybackWaiters('), source.indexOf('  function enqueueVoicePcm('));
const spoken = [];
let fail = false;
const state = {current: {id: 'session'}, voice: {phase: 'idle', requestSequence: 0, sources: new Set(), pendingTurns: new Set()}};
const ctx = vm.createContext({state, Promise, reportVoiceLifecycle() {}, stopProgressVoice() {}, clearVoiceKaraoke() {}, syncVoiceUi() {},
  voiceSpokenElsewhere: () => false, liveVoiceTurn: t => t, revealVoicePresentation() {}, showVoiceFailure() {},
  async speakTurnPreparation(turn, session, pending, previous) {
    spoken.push([turn.id, previous]);
    if (fail) { fail = false; throw Error('provider failed'); }
    state.voice.turnId = turn.id;
    state.voice.phase = 'streaming'; // Network finished, but buffered PCM is still playing.
  }
});
vm.runInContext(functions, ctx);
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const queue = id => ctx.queueAutomaticVoice({id}, state.current.id, id);
const finish = () => ctx.finishVoicePlayback(state.voice.requestSequence);
const a = queue('a'); await tick();
const b = queue('b'), c = queue('c'); await tick();
assert.deepEqual(spoken, [['a', undefined]], 'buffered audio must finish before next rewrite');
finish(); await tick();
assert.deepEqual(spoken.at(-1), ['b', 'a']);
finish(); await tick();
assert.deepEqual(spoken.at(-1), ['c', 'b']);
finish(); await Promise.all([a,b,c]); await tick();
const d = queue('d'); await tick(); const e = queue('e');
ctx.stopVoicePlayback(); await Promise.all([d,e]); await tick();
assert.equal(spoken.some(([id]) => id === 'e'), false, 'Stop discards queued speech');
const f = queue('f'); await tick(); assert.deepEqual(spoken.at(-1), ['f', undefined]);
finish(); await f; await tick();
fail = true; const g = queue('g'), h = queue('h'); await tick();
assert.deepEqual(spoken.at(-1), ['h', undefined], 'failed audio must not introduce a phantom previous utterance');
finish(); await Promise.all([g,h]); await tick();
// A queued answer waiting behind manual playback cannot survive navigation.
state.voice.phase = 'streaming'; state.voice.turnId = 'manual';
const i = queue('i'); await tick(); state.current.id = 'other'; ctx.stopVoicePlayback();
await i; await tick(); assert.equal(spoken.some(([id]) => id === 'i'), false);
console.log('Voice queue: FIFO through playback completion, contextual transitions, cancellation, navigation and recovery pass.');
