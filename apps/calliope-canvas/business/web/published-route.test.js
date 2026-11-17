import test from 'node:test';
import assert from 'node:assert/strict';
import {publishedRoute} from './published-route.js';

test('direct, Stage and historical links retain slug and explicit version',()=>{
  for(const path of ['/apps/northstar/versions/3','/d/northstar/versions/3','/api/apps/northstar/embed/3','/calliope/artifacts/northstar/versions/3']) {
    assert.deepEqual(publishedRoute(path).slice(1),['northstar','3']);
  }
  assert.deepEqual(publishedRoute('/apps/northstar').slice(1),['northstar',undefined]);
  assert.equal(publishedRoute('/api/apps/northstar/ui'),null);
  assert.equal(publishedRoute('/api/apps/northstar/embed/nope'),null);
});
