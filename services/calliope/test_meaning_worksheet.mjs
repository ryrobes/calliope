import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import * as navigation from './calliope/meaning-data.mjs';
import * as presentation from './calliope/meaning-present.mjs';

// Run the actual page controller with deferred network responses and a mounted
// worksheet boundary. Browser checks cover the real DOM geometry and React grid.
const source=(await readFile(new URL('./calliope/meaning-desk.js',import.meta.url),'utf8'))
 .replace(/^import .*;\n/gm,'')
 .replace(/^if\(ownedNavigation\(history.state,owner\)\).*$/m,'');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(kind='cube'){
 const pending=[],updates=[],counts={shell:0,reparent:0};
 const reference={kind:'meaning',area:'subject:test',node:`${kind}:test`},node={id:reference.node,kind,source:'cubes.test',reference};
 const related={id:'relation:test',kind:'relation',source:'public.test',reference:{...reference,node:'relation:test'}};
 const graph={nodes:[node,related],edges:[{from:node.id,to:related.id,state:'declared'}]};
 const spec={groupBy:[],measures:[{id:'m',column:{name:'sales'},agg:'sum',alias:'sales'}]};
 const original={source:node.source,catalog:{relation:node.source,fields:[]},fields:[],rows:[{sales:'10'}],rollup:{spec,columns:['sales'],rows:[{sales:'10'}]},as_of:'2026-10-10T00:00:00Z'};
 const detail={detail:{observations:[{value:'99'}]}},host={isConnected:true},slot={firstChild:host,replaceChildren(){counts.reparent++}};
 const caption={},buttons={back:{},forward:{}},desk={scrollTop:0},body={scrollTop:140},catalog={scrollTop:60};
 const content={scrollTop:0,querySelector:q=>({'[data-query-caption]':caption,'.desk-object-body':body,'.desk-object-list':catalog}[q]||null),addEventListener(){}};
 const elements={'meaning-pivot-slot':kind==='cube'?slot:null,'meaning-desk':desk,'desk-content':content,'desk-nav':{querySelector:q=>buttons[q.includes('forward')?'forward':'back']}};
 const fixture={node,graph,spec,original,detail,host,updates,counts};
 const document={body:{dataset:{meaningOwner:'person'}},getElementById:id=>elements[id],addEventListener(){}};
 const history={state:null,snapshots:[],replaceState(state){this.state=state;this.snapshots.push(state)},pushState(state){this.state=state}};
 const listeners={};
 const context=vm.createContext({...navigation,...presentation,esc:presentation.escapeHtml,
  URL,URLSearchParams,AbortController,setTimeout,clearTimeout,document,history,
  window:{addEventListener:(name,fn)=>{listeners[name]=fn}},location:{href:`/meaning?area=subject:test&node=${kind}:test`},fixture,
  fetch:(path,options)=>new Promise((resolve,reject)=>pending.push({path,body:JSON.parse(options.body),signal:options.signal,reject,
   resolve:value=>resolve({ok:true,json:async()=>value})})),
 });
 vm.runInContext(source+`
  graph=fixture.graph;graphCache.set(route.area,graph);
  data=fixture.original;detail=fixture.detail;route.spec=fixture.spec;
  pivotHost=fixture.node.kind==='cube'?fixture.host:null;pivotSource=fixture.node.source;
  pivotMount=fixture.node.kind==='cube'?{update:props=>fixture.updates.push(props)}:null;
  readyContext=viewContext();render=()=>fixture.counts.shell++;
  syncPivot();fixture.navigate=navigate;
  fixture.state=()=>({data,detail,dataLoading,dataError,route});
 `,context);
 return {fixture,pending,updates,counts,original,detail,desk,body,catalog,history,listeners,latest:()=>updates.at(-1)};
}

test('shelf edits keep the mounted worksheet and surrounding detail while querying',async()=>{
 const h=harness(),changed={...h.fixture.spec,measures:[{...h.fixture.spec.measures[0],agg:'max'}]};
 h.latest().onChange(changed);
 assert.equal(h.pending.length,1);assert.equal(h.pending[0].path,'/api/calliope/meaning/data');
 assert.equal(h.latest().loading,true);assert.equal(h.latest().result,h.original.rollup);
 assert.equal(h.fixture.state().detail,h.detail);assert.equal(h.counts.shell,0);assert.equal(h.counts.reparent,0);
 assert.equal(h.body.scrollTop,140);
 const result={...h.original,rollup:{...h.original.rollup,spec:changed,rows:[{sales:'5'}]}};
 h.pending[0].resolve(result);await tick();
 assert.equal(h.latest().result,result.rollup);assert.equal(h.latest().loading,false);
 assert.equal(h.counts.shell,0);assert.equal(h.counts.reparent,0);
 // Result/source tabs and Back use the same mounted view and cached response.
 const saved=h.history.state;
 h.latest().onView('records');await tick();
 assert.equal(h.pending.length,1);assert.equal(h.latest().view,'records');
 h.listeners.popstate({state:saved});await tick();
 assert.equal(h.latest().view,'results');assert.equal(h.counts.shell,0);
});

test('rapid shelf edits ignore an older response even when transport completes after abort',async()=>{
 const h=harness();
 h.latest().onChange({...h.fixture.spec,limit:10});
 h.latest().onChange({...h.fixture.spec,limit:20});
 assert.equal(h.pending[0].signal.aborted,true);
 h.pending[0].resolve({...h.original,rollup:{rows:[{sales:'old'}]}});await tick();
 assert.equal(h.latest().result,h.original.rollup);assert.equal(h.latest().loading,true);
 const newest={...h.original,rollup:{...h.original.rollup,rows:[{sales:'new'}]}};
 h.pending[1].resolve(newest);await tick();
 assert.equal(h.latest().result,newest.rollup);assert.equal(h.latest().loading,false);
 assert.equal(h.counts.shell,0);assert.equal(h.counts.reparent,0);
});

test('query failure retains the last good result and retry updates in place',async()=>{
 const h=harness();h.latest().onChange({...h.fixture.spec,limit:10});
 h.pending[0].reject(new Error('Query timed out'));await tick();
 assert.equal(h.latest().result,h.original.rollup);assert.equal(h.latest().error,'Query timed out');
 assert.equal(h.latest().loading,false);assert.equal(h.counts.shell,0);
 h.latest().onRefresh();assert.equal(h.latest().error,'');
 h.pending[1].resolve(h.original);await tick();
 assert.equal(h.latest().loading,false);assert.equal(h.counts.shell,0);
});

test('opening another object still clears the prior object result and uses page navigation',()=>{
 const h=harness();h.fixture.navigate({node:'cube:other'});
 assert.equal(h.fixture.state().data,null);assert.ok(h.counts.shell>0);
});

test('metric pages inspect saved observations without querying their connected source',async()=>{
 const h=harness('metric');
 assert.equal(navigation.dataSource(h.fixture.graph,h.fixture.node).source,'public.test');
 h.fixture.navigate({panel:'definition'});
 assert.equal(h.pending.length,1);assert.equal(h.pending[0].path,'/api/calliope/meaning/inspect');
 h.pending[0].resolve(h.detail);await tick();
 h.fixture.navigate({panel:'data'});await tick();
 assert.equal(h.pending.length,1);
 assert.equal(h.fixture.state().data,null);
 assert.equal(h.fixture.state().detail,h.detail);
 assert.equal(h.fixture.state().dataLoading,false);
});

test('mode changes retain the object list position and Back restores the content pane',async()=>{
 const h=harness();h.fixture.navigate({panel:'definition'});
 const previous=h.history.snapshots.at(-1);
 assert.equal(previous.scroll.body,140);assert.equal(previous.scroll.catalog,60);
 h.pending[0].resolve(h.detail);await tick();
 assert.equal(h.body.scrollTop,0);assert.equal(h.catalog.scrollTop,60);
 h.listeners.popstate({state:previous});
 h.pending[1].resolve(h.original);await tick();
 assert.equal(h.body.scrollTop,140);assert.equal(h.catalog.scrollTop,60);
 // Entries created before the pane layout stored one numeric scroll position.
 h.listeners.popstate({state:{...previous,route:{...previous.route,panel:'definition'},scroll:55}});await tick();
 assert.equal(h.body.scrollTop,55);
});
