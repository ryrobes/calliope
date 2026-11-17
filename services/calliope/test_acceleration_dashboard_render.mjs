import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('./system-dashboards/acceleration-health.html', import.meta.url), 'utf8');
const script = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].at(-1)[1];
const queryNames = new Map([...script.matchAll(/([a-z]+):`(select [^`]+)`/g)].map(([, key, sql]) => [sql, key]));
const now = Date.now();
const fixtures = {
  health: [{eligible: 10, current: 8, unaccelerated: 2, baseline: 1}],
  vortex: [{applicable: 1, fresh: 1}], ages: [], strategy: [], layouts: [], expensive: [],
  timeline: [
    {table_oid: 42, table_name: null, action: 'full', status: 'ok', started_at: new Date(now-600000).toISOString(), finished_at: new Date(now-300000).toISOString()},
    {table_name: null, action: 'delta', started_at: new Date(now-120000).toISOString(), finished_at: new Date(now-60000).toISOString()},
    {table_name: 'bad.time', started_at: null, finished_at: new Date(now).toISOString()},
  ],
  live: [{table_name: null, action: 'full', current_work_started_at: new Date(now-90000).toISOString()}],
  queue: [{table_name: null, table_oid: 42}],
  runtime: [{table_name: null}],
};

async function run({failQuery, failDisplay, data = fixtures} = {}) {
  const elements = new Map(), events = {}, errors = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {innerHTML: '', textContent: '', value: '', style: {}, clientWidth: 800,
      classList: {add() {}, remove() {}, toggle() {}}});
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {getElementById(id) {if (failDisplay && id === 'gantt') throw Error('display probe'); return element(id);}, querySelectorAll() {return [];}},
    localStorage: {getItem() {return null;}, setItem() {}},
    console: {error: error => errors.push(error.message)},
    setInterval() {}, clearInterval() {}, addEventListener: (name, fn) => {events[name] = fn;},
    rvbbitQuery: async sql => {
      const name = queryNames.get(sql);
      assert.ok(name, 'Unexpected dashboard query');
      if (name === failQuery || data[name]?.error) throw Error('query probe');
      return data[name];
    },
  });
  await vm.runInContext(script, context);
  return {elements, events, errors, context};
}

const good = await run();
assert.match(good.elements.get('status').textContent, /^Live/);
assert.deepEqual(good.errors, []);
assert.match(good.elements.get('gantt').innerHTML, /Relation OID 42/);
assert.match(good.elements.get('gantt').innerHTML, /Unresolved relation/);
assert.doesNotMatch(good.elements.get('gantt').innerHTML, /NaN|bad\.time/);
assert.match(good.elements.get('queue').innerHTML, /Relation OID 42/);
assert.match(good.elements.get('runtime').innerHTML, /Unresolved relation/);
good.events.resize();
vm.runInContext("document.getElementById('search').value='unresolved'; tables(); hours=1; gantt();", good.context);
assert.match(good.elements.get('runtime').innerHTML, /Unresolved relation/);

// The alternate query bridge shape also normalizes null relation names.
const normalized = vm.runInContext("rows({columns:['table_name','table_oid'],rows:[[null,99]]})", good.context);
assert.equal(normalized[0].table_name, 'Relation OID 99');
const queryError = await run({failQuery: 'timeline'});
assert.equal(queryError.elements.get('status').textContent, 'Partial · query failed: timeline');
assert.match(queryError.elements.get('gantt').innerHTML, /Query unavailable: timeline/);
assert.match(queryError.elements.get('runtime').innerHTML, /Unresolved relation/);
queryError.events.resize();
assert.match(queryError.elements.get('gantt').innerHTML, /Query unavailable: timeline/);
const coverageError = await run({failQuery: 'health'});
assert.match(coverageError.elements.get('coverage').innerHTML, /Query unavailable: health/);
assert.match(coverageError.elements.get('gantt').innerHTML, /Relation OID 42/);
const displayError = await run({failDisplay: true});
assert.equal(displayError.elements.get('status').textContent, 'Display failed');

if (process.argv[2]) {
  const live = await run({data: JSON.parse(readFileSync(process.argv[2], 'utf8'))});
  const failed = Object.entries(JSON.parse(readFileSync(process.argv[2], 'utf8'))).filter(([, rows]) => rows?.error);
  assert.equal(live.errors.length, failed.length);
  assert.match(live.elements.get('status').textContent, failed.length ? /^Partial/ : /^Live/);
  live.events.resize();
  console.log('Live query snapshot: full render and resize passed');
}
console.log('Acceleration dashboard: null relations, invalid times, search, resize, partial query failures and display errors passed');
