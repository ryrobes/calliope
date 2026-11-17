import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {RecordItem,ReactiveRegion,ReactiveContext} from './regions.js';

test('record selection exposes the bound identity and dispatches the normal event contract',()=>{
 const events=[];
 const props={label:'Visit',detail:'Today',recordId:'v2',selectedId:'v2',onSelect:'select_visit'};
 const element=RecordItem({props,context:{dispatchAction:e=>events.push(e)}});
 assert.equal(element.props['aria-pressed'],true);
 element.props.onClick();
 assert.deepEqual(events,[{event:{name:'select_visit',context:{id:'v2'}}}]);
 assert.equal(RecordItem({props:{...props,selectedId:'v1'},context:{}}).props['aria-pressed'],false);
});

test('a pending reactive region retains its content and distinguishes held and unavailable results',()=>{
 const render=(pending,status)=>renderToStaticMarkup(React.createElement(ReactiveContext.Provider,{value:pending},
  React.createElement(ReactiveRegion,{props:{child:'form',label:'Next workspace',status,revision:'parts'},buildChild:()=>React.createElement('textarea',{defaultValue:'Unsaved findings'})})));
 const pending=render(true,'complete');
 assert.match(pending,/aria-busy="true"/);
 assert.match(pending,/Considering this context/);
 assert.match(pending,/Unsaved findings/);
 assert.match(render(false,'held'),/Held · resume/);
 assert.match(render(false,'unavailable'),/Suggestion unavailable/);
});
