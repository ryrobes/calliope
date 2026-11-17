import React from 'react';
const h=React.createElement;
const time=m=>`${String(Math.floor(m/60)).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`;
export function ResourceDay({props:p,buildChild}){
 const start=p.start??360,end=p.end??1200;
 return h('section',{className:'resource-day','aria-label':p.label},p.resources.map((name,lane)=>h('section',{className:'resource-lane',key:name,'aria-label':name},h('h3',null,name),h('div',{className:'resource-track',style:{height:(end-start)*.8}},Array.from({length:Math.ceil((end-start)/120)},(_,i)=>h('div',{className:'resource-hour',key:i,style:{top:i*120*.8}},time(start+i*120))),p.children.flatMap((id,i)=>p.positions[i].lane===lane?[h('div',{className:'resource-appointment',key:id,style:{top:(p.positions[i].start-start)*.8,height:Math.max(90,(p.positions[i].end-p.positions[i].start)*.8-4)}},buildChild(id))]:[])))));
}
