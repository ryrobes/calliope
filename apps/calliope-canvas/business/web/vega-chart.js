import React, {useEffect, useRef, useState} from 'react';
import {parse, View} from 'vega';
import {compile} from 'vega-lite';
const h=React.createElement;

// This catalog accepts inline datasets only. SQL execution stays on the server.
export function validateChart(spec) {
  if (!spec || typeof spec!=='object' || Array.isArray(spec) || JSON.stringify(spec).length>1_000_000) throw Error('Expected a Vega-Lite object of at most 1 MB.');
  let rows=0;
  const dataset=v=>{if(!v||typeof v!=='object')throw Error('Use inline datasets');rows+=Array.isArray(v)?v.length:(v.features?.length||1);if(rows>5000)throw Error('Chart datasets exceed 5000 rows');};
  const visit=(value,depth=0)=>{
    if(depth>32) throw Error('Chart nesting is too deep.');
    if(Array.isArray(value)) value.forEach(v=>visit(v,depth+1));
    else if(value && typeof value==='object') for(const [k,v] of Object.entries(value)) {
      if(k==='datasets') {Object.values(v).forEach(dataset);continue;}
      if(k==='values' && depth && v && typeof v==='object') {dataset(v);continue;}
      if(['url','href','signals'].includes(k) || (k==='mark' && (v==='image'||v?.type==='image'))) throw Error('External chart data, links and image marks are disabled');
      visit(v,depth+1);
    }
  };
  visit(spec);return spec;
}
export function fitChart(spec,width,height) {
 const doc=structuredClone(spec);
 const facetCount=(node,channel)=>{const field=(node.facet||node.encoding||{})[channel]?.field,data=node.data||doc.data||{},values=data.values||doc.datasets?.[data.name]||[];return field&&Array.isArray(values)?Math.max(1,new Set(values.map(r=>JSON.stringify(r[field]))).size):1;};
 const size=(node,w,h)=>{
  delete node.autosize;
  if(['hconcat','vconcat','concat','facet','repeat'].some(k=>k in node)||['row','column','facet'].some(k=>k in (node.encoding||{}))) {
   delete node.width;delete node.height;
   for(const key of ['hconcat','vconcat','concat'])if(node[key]) {
    const children=node[key],cols=key==='hconcat'?children.length:key==='vconcat'?1:Math.min(node.columns||2,children.length),rows=Math.ceil(children.length/Math.max(1,cols));
    children.forEach(c=>size(c,Math.max(100,Math.floor(w/Math.max(1,cols))-24),Math.max(100,Math.floor(h/rows)-24)));
   }
   if(node.spec){const repeat=node.repeat,cols=Array.isArray(repeat)?Math.min(node.columns||2,Math.max(1,repeat.length)):repeat?Math.max(1,repeat.column?.length||1):facetCount(node,'column'),rows=repeat&&!Array.isArray(repeat)?Math.max(1,repeat.row?.length||1):facetCount(node,'row');size(node.spec,Math.max(100,Math.floor(w/Math.max(1,cols))-40),Math.max(120,Math.floor(h/rows)-70));}
   if(node.encoding){node.width=Math.max(100,Math.floor(w/2)-32);node.height=Math.max(120,h-70);}
  }else Object.assign(node,{width:w,height:h,autosize:{type:'fit',contains:'padding'}});
 };
 size(doc,Math.max(180,Math.min(2048,width)),Math.max(180,Math.min(960,height)));return doc;
}

export function VegaChart({spec,height=260,description,onSelect,context}) {
  const host=useRef(null), [error,setError]=useState(''), [tip,setTip]=useState(null);
  const action=useRef(); action.current={onSelect,context};
  // A2UI can hand us new object identities without changing the chart.
  const serialized=JSON.stringify(spec);
  useEffect(()=>{
    let view, timer, generation=0, disposed=false;
    const el=host.current;
    setTip(null);
    const render=async()=>{
      const run=++generation;
      el.dataset.renderState='pending';
      try {
        const definition=validateChart(JSON.parse(serialized));
        const width=Math.max(180,Math.min(2048,Math.floor(el.getBoundingClientRect().width)));
        const chart={...fitChart(definition,width,height),description};
        view?.finalize(); el.replaceChildren();
        view=new View(parse(compile(chart).spec), {renderer:'svg',loader:{load:async()=>{throw Error('External chart data is disabled.');},sanitize:async()=>{throw Error('Chart links are disabled.');}}})
          .initialize(el).hover().tooltip((_handler,event,_item,value)=>{
            if(!disposed) setTip(value==null ? null : {value:typeof value==='object' ? Object.entries(value).map(([k,v])=>`${k}: ${v}`).join(' · ') : String(value)});
          });
        view.addEventListener('click',(_event,item)=>{
          const {onSelect,context}=action.current;
          const kind=item?.mark?.marktype;
          if(disposed || run!==generation || !onSelect || !item?.datum || !['rect','symbol','text'].includes(kind)) return;
          // Native picking supports these marks; layered text delegates to its
          // underlying heatmap datum. No action fires for axes or blank space.
          if(item.mark.role!=='mark') return;
          const datum=Object.fromEntries(Object.entries(item.datum).filter(([,v])=>v==null||['string','number','boolean'].includes(typeof v)));
          const tooltip=item.tooltip==null?'':typeof item.tooltip==='object'?Object.entries(item.tooltip).map(([k,v])=>`${k}: ${v}`).join('\n'):String(item.tooltip);
          context.dispatchAction({event:{name:onSelect,context:{componentId:context.componentModel.id,datum,tooltip}}});
        });
        await view.runAsync();
        const svg=el.querySelector('svg');if(svg){svg.setAttribute('viewBox',`0 0 ${svg.getAttribute('width')} ${svg.getAttribute('height')}`);svg.style.width='100%';svg.style.height='100%';svg.style.display='block';}
        if(!disposed && run===generation){setError('');el.dataset.renderState='ready';}
      } catch(e) {if(!disposed && run===generation){setError(e.message);el.dataset.renderState='failed';}}
    };
    const resize=new ResizeObserver(()=>{clearTimeout(timer); timer=setTimeout(render,120);});
    resize.observe(el); render();
    return ()=>{disposed=true;generation++;clearTimeout(timer);resize.disconnect();view?.finalize();};
  },[serialized,height,description]);
  const rows=spec?.data?.values||[], keys=Object.keys(rows[0]||{});
  return h('div',{className:'c-vega'},
    h('div',{ref:host,className:'vega-plot',role:'img','aria-label':description,style:{height}}),
    error && h('p',{role:'alert',className:'error'},`Chart unavailable: ${error}`),
    tip && h('div',{className:'vega-tooltip',role:'tooltip'},tip.value),
    spec?.data?.values && !rows.length && h('div',{className:'muted'},'No data in this selection.'),
    h('details',{className:'vega-data'},h('summary',null,'Chart data'),h('div',{className:'vega-table-scroll'},h('table',null,h('thead',null,h('tr',null,keys.map(k=>h('th',{key:k,scope:'col'},k.replaceAll('_',' '))))),h('tbody',null,rows.map((r,i)=>h('tr',{key:i},keys.map(k=>h('td',{key:k},String(r[k]??'')))))))))
  );
}
