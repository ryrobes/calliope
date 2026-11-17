import test from 'node:test';
import assert from 'node:assert/strict';
import {dataValue,exactNumber,timestamp,dateParts,sqlMarkup,numericHistory,historyChart,matchingFields} from './calliope/meaning-present.mjs';

test('numeric presentation retains precision and does not infer units or coerce strings',()=>{
 assert.equal(exactNumber(123456789.125),'123,456,789.125');
 assert.equal(exactNumber('00123'),'00123');
 assert.match(dataValue(0),/>0</);
 assert.match(dataValue(null),/Not recorded/);
 assert.match(dataValue(false),/>false</);
 assert.doesNotMatch(dataValue({cost:12.75}),/\$/);
});
test('structured values and invalid dates cannot inject markup',()=>{
 const attack='<img src=x onerror=alert(1)>';
 for(const html of [dataValue({[attack]:[attack]}),timestamp(attack),sqlMarkup(attack)]){
  assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
 }
 assert.match(dataValue({window:{days:30}}),/data-properties/);
 assert.equal(dateParts(null),null);
 assert.equal(dateParts('2026-10-09 12:30:00+00:00').iso,'2026-10-09T12:30:00.000Z');
 assert.match(timestamp('2026-10-09T12:30:00Z'),/datetime="2026-10-09T12:30:00.000Z"/);
});
test('SQL highlighting retains original SQL text including literal keywords and comments',()=>{
 const sql="SELECT sum(cost) FROM jobs WHERE title = 'select <data> & it''s' AND days > {days} -- from comment\n";
 const html=sqlMarkup(sql);
 const decoded=html.replace(/<[^>]+>/g,'').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&#39;/g,"'").replace(/&quot;/g,'"').replace(/&amp;/g,'&');
 assert.equal(decoded,sql);assert.match(html,/sql-parameter/);assert.match(html,/sql-comment/);
});
test('history omits nonnumeric observations and invalid time without inventing values',()=>{
 const rows=[{value:12,time:200},{value:null,time:50},{value:'15',time:100},{value:11,time:100},{value:9,time:null},{value:20,time:''},{value:30,time:'NaN'}];
 assert.deepEqual(numericHistory(rows).map(x=>[x.time,x.value]),[[100,11],[200,12]]);
 const html=historyChart(rows);assert.equal((html.match(/data-observation=/g)||[]).length,2);
 assert.doesNotMatch(html,/NaN|Infinity/);
 assert.match(historyChart([{value:12,time:100}]),/needs two/);
 assert.doesNotMatch(historyChart([{value:12,time:100},{value:12,time:100}]),/NaN|Infinity/);
});
test('field dictionary filters by source metadata and role together',()=>{
 const fields=[{col_name:'job_id',role:'identifier',data_type:'integer',what:'Visit identity'}, {col_name:'cost',role:'measure',data_type:'numeric',what:'Recorded expense'}, {col_name:'note',data_type:'text'}];
 assert.equal(matchingFields(fields,'expense','measure')[0].col_name,'cost');
 assert.equal(matchingFields(fields,'expense','identifier').length,0);
 assert.equal(matchingFields(fields,'integer').length,1);
 assert.equal(matchingFields(fields,'','unclassified')[0].col_name,'note');
});
