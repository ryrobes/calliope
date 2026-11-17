import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {RollingQueries, receipt} = createRequire(import.meta.url)('./calliope/query-work.js');
const result = (id, changes = {}) => ({id, kind: 'query', turn_id: 'turn', tool_call_id: id,
  title: `Result ${id}`, created_at: new Date(1000).toISOString(),
  payload: {columns: [{name: 'total'}], rows: [{total: 0}], row_count: 1}, ...changes});
const snapshot = (surfaces = [], tasks = [], extra = {}) => ({session: 'a', runs: {turn: 'running', specialist: 'running'}, surfaces, tasks, ...extra});
const task = (scope, id, changes = {}) => ({scope, call: `${scope}:${id}`, name: 'mcp__Warehouse__run_sql', status: 'running', started: 1000, ...changes});

// Native call IDs are scoped by agent run. Batches replace their placeholder;
// replay neither duplicates nor reorders receipts.
{
  const store = new RollingQueries(), tasks = [task('turn', 'one'), task('specialist', 'one', {agent: 'iris'})];
  store.sync(snapshot([], tasks), 1000);
  assert.equal(store.view(1000).running, 2);
  const surfaces = [result('a', {tool_call_id: 'one'}), result('b', {tool_call_id: 'one'})];
  store.sync(snapshot(surfaces, tasks), 2000);
  let view = store.view(2000);
  assert.equal(view.total, 3); assert.equal(view.running, 1);
  const order = view.items.map(item => item.key);
  store.sync(snapshot([...surfaces].reverse(), tasks), 3000);
  assert.deepEqual(store.view(3000).items.map(item => item.key), order);
  store.sync(snapshot([result('c', {tool_call_id: 'one', source: {specialist_run_id: 'specialist', specialist_key: 'iris'}})]), 4000);
  assert.equal(store.view(4000).running, 0);
  assert.equal(store.view(4000).total, 3);
}

// Completion updates in place, even for slow queries, without losing preview lifetime.
{
  const store = new RollingQueries(), tasks = [task('turn', 'slow'), task('specialist', 'newer', {started: 2000})];
  store.sync(snapshot([], tasks), 2000);
  store.view(2000);
  const surfaces = [result('slow-result', {tool_call_id: 'slow', created_at: new Date(100000).toISOString()})];
  store.sync(snapshot(surfaces, tasks), 100000);
  assert.deepEqual(store.view(100000, true).items.map(item=>item.key), ['call:specialist:newer', 'receipt:slow-result']);
  store.sync(snapshot(surfaces, tasks), 110000);
  assert.equal(store.view(110000).items[1].at, 1000, 'The completed card keeps its original place');
  assert.equal(store.view(189000).items.length, 2, 'A slow query gets the full preview lifetime after completion');
  assert.equal(store.view(191000).items.length, 1);
}

// Strict descending time, capacity follows width, and overflow never resurfaces.
{
  const store = new RollingQueries();
  const surfaces = Array.from({length: 20}, (_, i) => result(String(i), {created_at: new Date(1000+i).toISOString()}));
  store.sync(snapshot(surfaces), 2000);
  assert.deepEqual(store.view(2000, false, 4).items.map(item => item.surfaceId), ['19','18','17','16']);
  const newest = result('new', {created_at: new Date(3000).toISOString()});
  store.sync(snapshot([...surfaces, newest]), 3000);
  assert.deepEqual(store.view(3000, true, 4).items.map(item => item.surfaceId), ['19','18','17','16']);
  assert.deepEqual(store.view(4000, false, 4).items.map(item => item.surfaceId), ['new','19','18','17']);
  assert.equal(store.view(4000, false, 1).items[0].surfaceId, 'new');
  store.sync(snapshot([...surfaces,newest]), 4100);
  assert.equal(store.view(4100, false, 10).items.length, 1, 'Widening cannot resurrect retired receipts');
}

// Each producing run ends independently, even if other agents or mailbox work
// keep the notebook busy. Hover cannot retain a finished run indefinitely.
{
  const store = new RollingQueries(), surfaces = [result('parent'), result('child', {source: {specialist_run_id: 'specialist'}})];
  const original = JSON.stringify(surfaces);
  store.sync(snapshot(surfaces), 1000);
  store.sync(snapshot(surfaces, [], {runs: {turn: 'ended', specialist: 'running'}}), 2000);
  assert.equal(store.view(3000).items.length, 2);
  assert.deepEqual(store.view(4000,true).items.map(item=>item.surfaceId), ['child']);
  store.sync(snapshot(surfaces, [], {runs: {turn: 'ended', specialist: 'ended'}}), 5000);
  assert.equal(store.view(7000,true).items.length, 0);
  store.sync(snapshot(surfaces), 8000);
  assert.equal(store.view(8000).items.length, 0, 'Stale running snapshots cannot revive retired items');
  assert.equal(JSON.stringify(surfaces), original);
}

// Idle gaps expire completed samples using server timestamps; actual running
// queries survive. Refresh and delayed updates must not restart the age clock.
{
  const store = new RollingQueries(), old = result('old');
  store.sync(snapshot([old], [task('specialist','slow')]), 1000);
  store.sync(snapshot([old], [], {runs:{turn:'unknown', specialist:'unknown'},delayed:true}), 90000);
  assert.equal(store.view(90000).items.length, 2);
  assert.equal(store.view(92000).items.length, 1);
  assert.equal(store.view(92000).items[0].status, 'running');
  const reload = new RollingQueries();
  reload.sync(snapshot([old]), 92000);
  assert.equal(reload.view(92000).items.length, 0);
  store.sync(snapshot([],[],{session:'b'}),93000);
  assert.equal(store.view(93000).items.length,0);
}

// Bounded samples preserve data and render empty/error results correctly.
{
  const sample = receipt(result('array', {payload: {columns: ['zero','no','nil','text','hidden'],
    rows: Array.from({length:20},()=>[0,false,null,'<script>alert(1)</script>','hidden']),row_count:5000}}));
  assert.deepEqual(sample.rows[0],['0','false','—','<script>alert(1)</script>']);
  assert.equal(sample.rows.length,3);assert.equal(sample.columns.length,4);assert.equal(sample.rowCount,5000);
  assert.equal(receipt(result('empty',{payload:{columns:['x'],rows:[],row_count:0}})).rowCount,0);
  assert.equal(receipt(result('failure',{payload:{error:'bad query'}})).status,'failed');
}
console.log('Query row: descending order, capacity, overflow, replay, independent run lifetime, expiry and safe samples passed');
