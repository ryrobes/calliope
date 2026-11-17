import test from 'node:test';
import assert from 'node:assert/strict';
import {initialObject,dataSource,routeFromUrl,routeUrl,nextRoute,bars,ownedNavigation} from './calliope/meaning-data.mjs';
test('captured row navigation cannot carry over to another signed-in account',()=>{
 const state={meaningDesk:true,owner:'alice',route:{record:{values:{private:'record'}}}};
 assert.equal(ownedNavigation(state,'alice'),state);assert.equal(ownedNavigation(state,'bob'),null);
 assert.equal(ownedNavigation({...state,owner:undefined},'alice'),null);
});
test('prefer stored business measure, then a cube over registration-only sample',()=>{
 const cube={id:'c',kind:'cube'},count={kind:'metric',metric_kind:'count',preview:{label:'Saved daily snapshot'}},measure={id:'m',kind:'metric',preview:{label:'Saved daily snapshot'}};
 assert.equal(initialObject([count,cube,measure]),measure);assert.equal(initialObject([count,cube]),cube);
});
test('source selector follows explicit dependencies, never suggested connections',()=>{
 const m={id:'m',kind:'metric'},a={id:'a',kind:'relation'},b={id:'b',kind:'cube'},g={nodes:[m,a,b],edges:[{from:'a',to:'m',state:'unverified'},{from:'b',to:'m',state:'declared'}]};
 assert.equal(dataSource(g,m,'a'),b);assert.equal(dataSource(g,a),a);
});
test('URL and navigation preserve filters and distinguish SQL null from text',()=>{
 const r={...routeFromUrl('/meaning?area=x&node=cube:y'),dimension:'branch',filter:{field:'branch',value:null},offset:40};
 assert.deepEqual(routeFromUrl(routeUrl(r)),r);assert.equal(nextRoute(r,{panel:'definition'}).offset,40);
 assert.equal(nextRoute(r,{dimension:'manufacturer'}).filter,null);assert.equal(nextRoute(r,{node:'metric:z'}).offset,0);
 assert.equal(nextRoute(r,{filter:{field:'branch',value:'NULL'}}).filter.value,'NULL');
});
test('group labels are escaped and exact decimal text is retained',()=>{
 const html=bars([{key:'<script>',value:'123456789012345678.12'}],null);
 assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('123456789012345678.12'));assert.ok(!html.includes('<script>'));
});

test('shelves and compound null/date selection survive URL and back navigation',()=>{
 const r=routeFromUrl('/meaning?area=x&node=cube:y');
 const spec={groupBy:[{column:{name:'region'}},{column:{name:'day'},grain:'month'}],measures:[{id:'m',alias:'sales',column:{name:'sales'},agg:'sum'}],pivot:{column:{name:'type'},values:[]}};
 const next=nextRoute(r,{spec});next.filter=[{field:'region',value:null},{field:'day',value:'2026-10-01',grain:'month'}];
 assert.deepEqual(routeFromUrl(routeUrl(next)),next);
 assert.equal(nextRoute(next,{spec:{...spec,pivot:null}}).filter,null);
 assert.equal(nextRoute(next,{source:'relation:other'}).spec,null);
});

test('worksheet result tabs and source drilldown restore with browser navigation',()=>{
 const start=routeFromUrl('/meaning?area=x&node=cube:y');
 assert.equal(start.dataView,'results');
 const drill=nextRoute(start,{filter:[{field:'region',value:'West'}]});
 assert.equal(drill.dataView,'records');assert.deepEqual(routeFromUrl(routeUrl(drill)),drill);
 assert.equal(nextRoute(drill,{dataView:'results'}).filter[0].value,'West');
 assert.equal(nextRoute(drill,{spec:{groupBy:[],measures:[]}}).dataView,'results');
 assert.equal(nextRoute(drill,{source:'relation:other'}).dataView,'results');
});

test('pinned collection has a reloadable route and opening an object leaves it',()=>{
 const start=routeFromUrl('/meaning?area=x&node=cube:y');
 const pins=nextRoute(start,{view:'pins'});
 assert.equal(routeFromUrl(routeUrl(pins)).view,'pins');
 assert.equal(pins.node,start.node);
 assert.equal(nextRoute(pins,{node:'metric:z'}).view,'data');
 assert.equal(nextRoute(pins,{area:'',node:'',view:'data'}).view,'data');
});
