// Async derived atoms in the host, independent of React and the A2UI renderer.
import {at,clone} from './continuity.js';
export const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
export function compose(base,resources,states){
  const nodes=clone(base),byId=new Map(nodes.map(n=>[n.id,n]));
  for(const r of resources){
    const v=r.variants[states[r.id]?.label??r.initial];
    for(const [id,props] of Object.entries(v.patches||{}))Object.assign(byId.get(id),clone(props));
    if(r.target)byId.get(r.target).children=v.root?[v.root]:[];
    nodes.push(...clone(v.components||[]));
  }return nodes;
}
export class Reactive {
  constructor(){this.entries=new Map();this.serial=0;this.document='';}
  install(snapshot){
    const key=canonical([snapshot.view,snapshot.selected,snapshot.reactive||[]]);
    if(key===this.document)return;
    this.document=key;this.entries.clear();
    for(const r of snapshot.reactive||[])this.entries.set(r.id,{definition:r,key:null,due:0,inflight:null,ticket:++this.serial,held:false,
      state:{label:r.initial,value:clone(r.variants[r.initial].value||{}),status:'idle',message:'Waiting for context'}});
  }
  observe(data,now=Date.now()){
    let changed=false;
    for(const e of this.entries.values()){
      const payload=Object.fromEntries(Object.entries(e.definition.watch).map(([k,p])=>[k,at(data,p)??null]));
      const key=canonical(payload);
      if(key!==e.key){e.key=key;e.payload=payload;e.ticket=++this.serial;e.due=now+(e.definition.debounceMs??350);e.state.status=e.held?'held':'waiting';e.state.message=e.held?'Held · context changed':'Context changed · keeping current workspace';changed=true;}
    }return changed;
  }
  poll(now=Date.now()){
    const requests=[];
    for(const [id,e] of this.entries)if(!e.held&&!e.inflight&&e.state.status==='waiting'&&now>=e.due){
      e.inflight=e.ticket;e.state.status='pending';e.started=now;
      requests.push({id,ticket:e.ticket,body:{state:e.payload,tree:e.definition.tree,max_steps:4}});
    }return requests;
  }
  pending(now=Date.now()){
    let changed=false;
    for(const e of this.entries.values())if(e.state.status==='pending'&&now-e.started>=250&&e.state.message!=='◌ Considering this context…'){
      e.state.message='◌ Considering this context…';changed=true;
    }return changed;
  }
  receive(id,ticket,result,error){
    const e=this.entries.get(id);if(!e||e.inflight!==ticket)return false;
    e.inflight=null;
    if(e.ticket!==ticket||e.held)return false;
    const d=result?.decision,v=e.definition.variants[d?.label];
    if(error||d?.status!=='complete'||!v){e.state.status='error';e.state.message='Decision unavailable · previous workspace kept. Reconsider to retry.';e.state.receipt=result||null;}
    else e.state={label:d.label,value:clone(v.value||{}),status:'ready',message:`${d.label} · ${result.elapsed_ms} ms · ${d.steps} decision${d.steps===1?'':'s'}`,receipt:result};
    return true;
  }
  action(name,id){
    const e=this.entries.get(id);if(!e)return false;
    if(name==='reactive_hold')e.held=!e.held;else if(name!=='reactive_retry')return false;
    e.ticket=++this.serial;e.state.status=e.held?'held':'waiting';e.state.message=e.held?'Held · this workspace stays put':'Reconsidering context';e.due=0;return true;
  }
  states(){return Object.fromEntries([...this.entries].map(([id,e])=>[id,clone(e.state)]));}
  busy(){return [...this.entries.values()].some(e=>['waiting','pending'].includes(e.state.status));}
}
