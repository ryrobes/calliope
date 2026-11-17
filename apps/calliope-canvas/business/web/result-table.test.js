import test from 'node:test';
import assert from 'node:assert/strict';
import {display,tableModel} from './result-table.js';

// Shared vectors with a2ui-gpui/src/result_table.rs.
test('numbers follow the column format',()=>{
  assert.equal(display(15011642.52,'decimal'),'15,011,642.52');
  assert.equal(display('15011642.5','decimal'),'15,011,642.50');
  assert.equal(display('1234','decimal'),'1,234');
  assert.equal(display(0.0034,'decimal'),'0.0034');
  assert.equal(display(0.0000012,'decimal'),'1.2e-6');
  assert.equal(display(-2500000,'integer'),'-2,500,000');
  assert.equal(display(2019,'plain'),'2019');
  assert.equal(display('123456789012345678901','integer'),'123,456,789,012,345,678,901');
  assert.equal(display('n/a','integer'),'n/a');
  assert.equal(display(null,'integer'),'—');
  assert.equal(display(1234,'text'),'1234');
  assert.equal(display({a:1},'json'),'{"a":1}');
});

test('array and object rows read the same columns',()=>{
  const columns=[{name:'product',format:'text'},{name:'revenue',format:'decimal'}];
  const arrays=tableModel(columns,[['Golf Caddy',15011642.52]],72,false);
  const objects=tableModel(columns,[{revenue:15011642.52,product:'Golf Caddy'}],undefined,true);
  assert.deepEqual(arrays.cells,objects.cells);
  assert.equal(arrays.cells[0][1],'15,011,642.52');
  assert.equal(arrays.summary,'Showing 1 of 72 rows · 2 columns');
  assert.equal(objects.summary,'1 row · 2 columns · the query had more rows');
  assert.equal(objects.raw(0,1),'15011642.52');
});
