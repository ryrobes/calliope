import React from 'react';
import {GridContext,fieldError} from './fields.js';
const h=React.createElement;
const ref=c=>typeof c==='string'?c:c.id;
export function pasteMatrix(text,row,col,rows,fields){
  if(text.length>16000)throw Error('Paste at most 400 cells.');
  const matrix=text.replaceAll('\r\n','\n').replace(/\n$/,'').split('\n').map(r=>r.split('\t'));
  if(matrix.reduce((n,r)=>n+r.length,0)>400)throw Error('Paste at most 400 cells.');
  const writes=[];
  for(let y=0;y<matrix.length;y++)for(let x=0;x<matrix[y].length;x++){
    const id=rows[row+y]?.[col+x],field=id&&fields.get(ref(id));
    if(!field||field.props.readOnly||field.props.visible===false)throw Error('Paste must stay within editable cells on this page.');
    const value=matrix[y][x].trim(),error=fieldError(value,field.props);
    if(error)throw Error(`${field.props.label}: ${error}`);
    writes.push([ref(id),value]);
  }return writes;
}
export function DataGrid({props:p,buildChild,context}){
  const cols=p.columns,rows=[],registry=React.useRef(new Map()),root=React.useRef(null),id=context.componentModel.id;
  const [error,setError]=React.useState('');
  const [widths,setWidths]=React.useState(()=>{try{const v=JSON.parse(localStorage.getItem('calliope-grid:'+id));return Array.isArray(v)&&v.length===cols.length&&v.every(x=>Number.isFinite(x)&&x>=80&&x<=600)?v:null;}catch{return null;}});
  for(let i=0;i<p.children.length;i+=cols.length)rows.push(p.children.slice(i,i+cols.length));
  const saveWidths=next=>{setWidths(next);try{localStorage.setItem('calliope-grid:'+id,JSON.stringify(next));}catch{}};
  const actualWidths=()=>[...root.current.querySelectorAll('thead th')].map(e=>Math.max(80,Math.min(600,e.getBoundingClientRect().width)));
  const resize=(e,i)=>{e.preventDefault();const start=e.clientX,initial=widths||actualWidths();e.currentTarget.setPointerCapture(e.pointerId);const target=e.currentTarget;
    const move=event=>{const next=[...initial];next[i]=Math.max(80,Math.min(600,initial[i]+event.clientX-start));saveWidths(next);};
    const done=()=>{target.removeEventListener('pointermove',move);target.removeEventListener('pointerup',done);target.removeEventListener('pointercancel',done);};
    target.addEventListener('pointermove',move);target.addEventListener('pointerup',done);target.addEventListener('pointercancel',done);
  };
  const keyDown=e=>{
    const cell=e.target.closest('td'),fieldId=e.target.dataset.fieldId;if(!cell||!fieldId||e.target.tagName==='TEXTAREA')return;
    const y=Number(cell.dataset.row),x=Number(cell.dataset.col);let target;
    if(e.key==='Enter'||e.key==='ArrowDown'||e.key==='ArrowUp'){
      const direction=e.key==='ArrowUp'||e.shiftKey?-1:1;for(let n=y+direction;n>=0&&n<rows.length;n+=direction){const f=registry.current.get(ref(rows[n][x]));if(f&&!f.props.readOnly&&f.props.visible!==false){target=ref(rows[n][x]);break;}}
    }else if(e.key==='Tab'){
      const editable=rows.flat().map(ref).filter(id=>{const f=registry.current.get(id);return f&&!f.props.readOnly&&f.props.visible!==false;});target=editable[editable.indexOf(fieldId)+(e.shiftKey?-1:1)];
    }
    if(target){e.preventDefault();const input=[...root.current.querySelectorAll('[data-field-id]')].find(n=>n.dataset.fieldId===target);input?.focus();input?.select();}
  };
  const paste=e=>{
    const cell=e.target.closest('td');const text=e.clipboardData.getData('text/plain');if(!cell||!/[\t\n]/.test(text))return;
    e.preventDefault();try{const writes=pasteMatrix(text,Number(cell.dataset.row),Number(cell.dataset.col),rows,registry.current);for(const [id,value]of writes)registry.current.get(id).setValue(value);setError('');}catch(error){setError(error.message);}
  };
  const headers=cols.map((c,i)=>h('th',{key:i,scope:'col'},c.label,
    p.resizable&&h('span',{
      className:'column-resizer',role:'separator','aria-label':'Resize '+c.label,
      'aria-orientation':'vertical','aria-valuenow':Math.round(widths?.[i]||100),
      'aria-valuemin':80,'aria-valuemax':600,tabIndex:0,onPointerDown:e=>resize(e,i),
      onKeyDown:e=>{
        if(!['ArrowLeft','ArrowRight'].includes(e.key))return;
        e.preventDefault();const next=[...(widths||actualWidths())];
        next[i]=Math.max(80,Math.min(600,next[i]+(e.key==='ArrowRight'?20:-20)));saveWidths(next);
      }
    })
  ));
  const body=rows.map((row,i)=>h('tr',{
    key:p.rowKeys?.[i]||ref(row[0]),'data-row-key':p.rowKeys?.[i]||ref(row[0])
  },row.map((child,j)=>h('td',{
    key:ref(child),'data-label':cols[j].label,'data-row':i,'data-col':j
  },buildChild(child)))));
  const columns=cols.map((c,i)=>h('col',{key:i,style:{
    width:widths?widths[i]:`${100*(c.weight||1)/cols.reduce((a,c)=>a+(c.weight||1),0)}%`
  }}));
  const table=h('table',{className:'c-grid',style:widths?{minWidth:widths.reduce((a,b)=>a+b,0)}:undefined},
    h('colgroup',null,columns),h('thead',null,h('tr',null,headers)),h('tbody',null,body));
  const reset=p.resizable&&widths&&h('button',{
    type:'button',className:'grid-reset',onClick:()=>{
      setWidths(null);try{localStorage.removeItem('calliope-grid:'+id);}catch{}
    }
  },'Reset column widths');
  return h(GridContext.Provider,{value:registry},
    h('div',{className:'c-grid-container',ref:root,onKeyDownCapture:keyDown,onPasteCapture:paste},
      error&&h('div',{className:'field-error',role:'alert'},error),
      h('div',{className:'c-grid-scroll'},table),reset));
}
