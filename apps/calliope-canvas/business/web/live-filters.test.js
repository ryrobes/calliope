import {test} from 'node:test';
import assert from 'node:assert/strict';
import {LiveFilters} from './live-filters.js';

const nodes=[{component:'CFilterBar',value:{path:'/filters'},onChange:'work_filter'},
  {component:'CField',kind:'date',required:true,value:{path:'/filters/date'}},
  {component:'CRecordPicker',value:{path:'/filters/owner'}}];
const data=(search='',date='2026-10-08',owner=['Everyone'])=>({filters:{search,date,owner},draft:{notes:'Keep me'}});

test('coalesces rapid edits, rejects stale replies and cancels on navigation',()=>{
  const f=new LiveFilters();f.install(nodes,data());
  f.observe(data(),0);assert.equal(f.poll(1000),null);
  f.observe(data('a'),1000);f.observe(data('ab'),1200);
  assert.equal(f.poll(1300),null);
  const request=f.poll(1450);assert.equal(request.context.search,'ab');
  assert.equal(f.matches(request.context,data('abc')),false);
  assert.equal(f.matches(request.context,data('ab')),true);
  f.observe(data('abc'),1500);f.install([],{});
  assert.equal(f.poll(3000),null);
});

test('draft changes do not query; incomplete filters wait without sending invalid input',()=>{
  const f=new LiveFilters();f.install(nodes,data());
  f.observe({...data(),draft:{notes:'Edited'}},0);assert.equal(f.pending(),false);
  f.observe(data('find','2026-'),100);assert.equal(f.poll(1000),null);
  f.observe(data('find','2026-10-09',[]),1000);assert.equal(f.poll(2000),null);
  f.observe(data('find','2026-10-09'),2000);
  assert.deepEqual(f.poll(2300),{name:'work_filter',context:data('find','2026-10-09').filters});
  assert.equal(f.poll(2400),null);
  f.observe(data('changed'),2500);f.cancel();assert.equal(f.poll(4000),null);
});
