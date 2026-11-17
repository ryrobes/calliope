import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeLayout,panelFraction} from './calliope/business-logic-workspace.mjs';

test('saved dock orientations retain independent shares and invalid preferences recover',()=>{
 const saved=normalizeLayout({dock:'right',bottom:.27,right:.61});
 assert.deepEqual(normalizeLayout(JSON.parse(JSON.stringify(saved))),saved);
 assert.equal(normalizeLayout({...saved,dock:'bottom'}).bottom,.27);
 assert.equal(normalizeLayout({...saved,dock:'bottom'}).right,.61);
 assert.deepEqual(normalizeLayout({dock:'unknown',bottom:null,right:'80%'}),normalizeLayout(null));
 assert.equal(normalizeLayout({bottom:Infinity}).bottom,.32);
 assert.equal(normalizeLayout({right:100}).right,.8);
});

test('split resizing reserves usable room for both surfaces even in a small container',()=>{
 for(const dock of ['bottom','right'])for(const extent of [240,480,900,1800]){
  const small=panelFraction(-2,extent,dock),large=panelFraction(3,extent,dock);
  assert.ok(small>=.15&&large<=.8&&small<=large);
  assert.ok(extent*(1-large)>=Math.min(dock==='right'?240:180,extent*.35)-.001);
 }
 // The preferred fraction survives a resize; only the rendered size is clamped.
 const saved=normalizeLayout({dock:'right',right:.2});
 assert.ok(panelFraction(saved.right,600,'right')>saved.right);
 assert.equal(panelFraction(saved.right,1800,'right'),saved.right);
});
