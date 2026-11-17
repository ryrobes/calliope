import React from 'react';
const h=React.createElement;
// Same rules as the native renderer (a2ui-gpui/src/result_table.rs); the server
// chooses each column's format, renderers only apply it.
const NUMERIC=new Set(['integer','plain','decimal']);
const CELL_CHARS=200;
export function group(text){
  const sign=text.startsWith('-')?'-':'',digits=sign?text.slice(1):text,[whole,fraction]=digits.split('.');
  const grouped=whole.replace(/\B(?=(\d{3})+(?!\d))/g,',');
  return sign+grouped+(fraction===undefined?'':'.'+fraction);
}
function fromValue(value){
  if(Number.isInteger(value))return group(value.toFixed(0));
  if(Math.abs(value)>=1)return group(value.toFixed(2));
  const short=value.toFixed(4).replace(/0+$/,'').replace(/\.$/,'');
  return short===''||short==='0'||short==='-0'?value.toExponential().replace('e+','e'):short;
}
function number(text,format){
  text=String(text).trim();
  if(!/^-?\d+(\.\d+)?$/.test(text)){
    // Exponent forms and the like: format the parsed value.
    const value=Number(text);
    if(text===''||!Number.isFinite(value))return null;
    return format==='plain'?String(value):fromValue(value);
  }
  if(format==='plain')return text.replace(/\.0$/,'');
  if(text.replace(/^-/,'').split('.')[0].length>15)return group(text);
  return fromValue(Number(text));
}
export function display(value,format){
  if(value===null||value===undefined)return '—';
  let text;
  if(typeof value==='boolean')text=String(value);
  else if(NUMERIC.has(format)&&(typeof value==='number'||typeof value==='string'))text=number(value,format)??String(value);
  else if(typeof value==='string')text=value;
  else if(typeof value==='number')text=String(value);
  else text=JSON.stringify(value);
  text=text.replace(/[\n\r\t]/g,' ');
  return [...text].length>CELL_CHARS?[...text].slice(0,CELL_CHARS).join('')+'…':text;
}
const raw=value=>value===null||value===undefined?'NULL':typeof value==='string'?value:typeof value==='object'?JSON.stringify(value,null,2):String(value);
const count=n=>group(String(n));
export function tableModel(columns,rows,rowCount,truncated){
  rows=Array.isArray(rows)?rows:[];
  const cols=(columns||[]).map((c,i)=>({name:typeof c==='string'?c:c.name||`Column ${i+1}`,format:c.format||'text'}));
  const value=(row,i,name)=>Array.isArray(row)?row[i]:row?.[name];
  const cells=rows.map(row=>cols.map((c,i)=>display(value(row,i,c.name),c.format)));
  const stored=Math.max(rowCount??rows.length,rows.length);
  let summary=stored>rows.length?`Showing ${count(rows.length)} of ${count(stored)} rows`:`${count(rows.length)} ${rows.length===1?'row':'rows'}`;
  summary+=` · ${count(cols.length)} ${cols.length===1?'column':'columns'}`;
  if(truncated)summary+=' · the query had more rows';
  return {cols,cells,summary,raw:(r,c)=>raw(value(rows[r],c,cols[c].name))};
}
export function ResultTable({props:p}){
  const model=React.useMemo(()=>tableModel(p.columns,p.rows,p.rowCount,p.truncated),[p.columns,p.rows,p.rowCount,p.truncated]);
  const [cell,setCell]=React.useState(null);
  React.useEffect(()=>setCell(null),[model]);
  const visible=Math.min(Math.max(model.cells.length,1),p.visibleRows||12);
  const numeric=c=>NUMERIC.has(model.cols[c].format)?'numeric':undefined;
  return h('div',{className:'c-result-table'},
    h('div',{className:'result-scroll',style:{maxHeight:30*(visible+1)+2}},
      h('table',null,
        h('thead',null,h('tr',null,model.cols.map((c,i)=>h('th',{key:i,className:numeric(i),scope:'col'},c.name.replaceAll('_',' '))))),
        h('tbody',null,model.cells.length?model.cells.map((row,r)=>h('tr',{key:r},row.map((text,c)=>h('td',{key:c,className:numeric(c),title:text,onClick:()=>setCell([r,c])},text))))
          :h('tr',null,h('td',{colSpan:model.cols.length,className:'muted'},'No rows returned'))))),
    h('div',{className:'result-summary'},model.summary),
    cell&&h('div',{className:'result-detail',role:'dialog','aria-label':'Cell value'},
      h('div',{className:'result-detail-bar'},
        h('button',{type:'button',onClick:()=>setCell(null)},'← Table'),
        h('span',null,`${model.cols[cell[1]].name.replaceAll('_',' ')} · row ${cell[0]+1}`),
        h('button',{type:'button',onClick:()=>navigator.clipboard?.writeText(model.raw(cell[0],cell[1]))},'Copy')),
      h('pre',null,model.raw(cell[0],cell[1]))));
}
