import React from 'react';
const h=React.createElement;
export const ReactiveContext=React.createContext(false);
export function ResponsiveStack({props:p,buildChild}) {
  const ref=React.useRef(null),[width,setWidth]=React.useState(null);
  React.useLayoutEffect(()=>{
    if(!ref.current||!globalThis.ResizeObserver)return;
    const observer=new ResizeObserver(([entry])=>setWidth(entry.contentRect.width));
    observer.observe(ref.current);
    return ()=>observer.disconnect();
  },[]);
  const threshold=p.collapse==='phone'?600:p.collapse==='tablet'?900:0;
  const row=p.direction==='row'&&!(threshold&&width!==null&&width<=threshold);
  return h('div',{ref,className:`c-stack c-${row?'row':'column'} gap-${p.gap||'md'} ${p.wrap?'wrap':''}`,
    'data-layout':row?'row':'column',style:{minWidth:0,...(p.weight?{flex:`${p.weight} 1 0`}:{})}},
    p.children.map(id=>h(React.Fragment,{key:id},buildChild(id))));
}
export function ScrollArea({props:p,buildChild}) {
  const available=p.height===undefined||p.height==='available';
  const minimum=p.minHeight||160;
  const ref=React.useRef(null),[height,setHeight]=React.useState(minimum);
  React.useLayoutEffect(()=>{
    const el=ref.current;
    if(!available||!el)return;
    let frame;
    const measure=()=>{
      cancelAnimationFrame(frame);
      frame=requestAnimationFrame(()=>{
        // Ignore scrolling; respond to actual changes in the layout above us.
        let top=el.getBoundingClientRect().top+window.scrollY;
        for(let parent=el.parentElement;parent&&parent!==document.body&&parent!==document.documentElement;parent=parent.parentElement)top+=parent.scrollTop;
        const next=Math.max(minimum,(window.visualViewport?.height||window.innerHeight)-top-24);
        setHeight(old=>Math.abs(old-next)>.5?next:old);
      });
    };
    const observer=new ResizeObserver(measure);
    // Headers, wrapped filters, and preceding siblings can move our origin
    // without changing our width. Observe those sources, not just the list.
    for(let node=el;node&&node!==document.documentElement;node=node.parentElement){
      observer.observe(node);
      for(let sibling=node.previousElementSibling;sibling;sibling=sibling.previousElementSibling)observer.observe(sibling);
    }
    window.addEventListener('resize',measure);
    window.visualViewport?.addEventListener('resize',measure);
    measure();
    return ()=>{observer.disconnect();cancelAnimationFrame(frame);window.removeEventListener('resize',measure);window.visualViewport?.removeEventListener('resize',measure);};
  },[available,minimum]);
  return h('div',{ref,className:'c-scroll-area',role:'region','aria-label':p.label,tabIndex:0,'data-sizing':available?'available':'bounded',
    style:{maxHeight:available?`${height}px`:`min(${p.height}px, 60vh)`,...(p.weight?{flex:`${p.weight} 1 0`}:{})}},buildChild(p.child));
}
export function RecordItem({props:p,context}) {
  const selected=p.recordId===p.selectedId;
  return h('button',{type:'button',className:`c-record-item${selected?' selected':''}`,'aria-pressed':selected,
    onClick:()=>context.dispatchAction({event:{name:p.onSelect,context:{id:p.recordId}}})},
    h('span',{className:'record-item-heading'},h('span',null,p.label),selected&&h('span',{className:'record-selected'},'Selected')),
    p.detail&&h('span',{className:'record-detail'},p.detail),p.badge&&h('span',{className:'record-badge'},p.badge));
}
export function ReactiveRegion({props:p,buildChild}) {
  const pending=React.useContext(ReactiveContext);
  const message=pending?'Considering this context…':p.status==='held'?'Held · resume to follow context':
    ['unavailable','error'].includes(p.status)?'Suggestion unavailable · showing fallback':
    p.status==='pending'?'Waiting for context…':'Automatic · follows the current context';
  const content=React.useRef(null),previous=React.useRef(p.revision);
  React.useEffect(()=>{
    if(previous.current===p.revision)return;
    previous.current=p.revision;
    if(globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches)return;
    const animation=content.current?.animate?.([{opacity:.45},{opacity:1}],{duration:240,easing:'ease-out'});
    return ()=>animation?.cancel();
  },[p.revision]);
  // Animate the existing subtree: editing focus survives a semantic revision.
  return h('section',{className:'c-reactive-region','aria-label':p.label,'aria-busy':pending},
    h('div',{className:'region-status',role:'status'},message),
    h('div',{className:'region-content',ref:content},buildChild(p.child)));
}
