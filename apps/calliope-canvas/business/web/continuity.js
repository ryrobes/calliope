// Per-record draft reconciliation. No command replay or offline stock claims.
export const equal=(a,b)=>{
  if(a===b)return true;if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;
  const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(k=>Object.hasOwn(b,k)&&equal(a[k],b[k]));
};
export const clone=v=>v===undefined?undefined:structuredClone(v);
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const escape=s=>s.replaceAll('~','~0').replaceAll('/','~1');
export const recordKey=s=>`${s.view}:${s.selected}`;
// A command can acknowledge one editor without consuming the rest of a case.
export function acknowledge(entry,remote,paths=[]){
  const out=clone(entry);
  for(const path of paths){
    if(!['/notes','/handoff','/receipt','/appointment'].includes(path))continue;
    put(out.local,path,at(remote,path));put(out.base,path,at(remote,path));
    for(const conflict of Object.keys(out.conflicts||{}))if(conflict===path||conflict.startsWith(path+'/'))delete out.conflicts[conflict];
  }
  return out;
}
export function at(value,path){return path.split('/').slice(1).reduce((o,k)=>o?.[k.replaceAll('~1','/').replaceAll('~0','~')],value);}
export function put(value,path,next){const keys=path.split('/').slice(1).map(k=>k.replaceAll('~1','/').replaceAll('~0','~'));let o=value;for(const k of keys.slice(0,-1))o=o[k]??={};if(next===undefined)delete o[keys.at(-1)];else o[keys.at(-1)]=clone(next);}
export function reconcile(base,local,remote,previous={}){
  const conflicts={};
  function merge(b,l,r,path){
    if(path==='/id'||path==='/version')return clone(r);
    if(equal(l,b))return clone(r);
    if(equal(r,b)||equal(l,r))return clone(l);
    if(object(b)&&object(l)&&object(r)){
      const result={};for(const k of new Set([...Object.keys(b),...Object.keys(l),...Object.keys(r)])){
        const value=merge(b[k],l[k],r[k],path+'/'+escape(k));if(value!==undefined)result[k]=value;
      }return result;
    }
    conflicts[path]={local:clone(l),remote:clone(r)};return clone(l);
  }
  const draft=merge(base,local,remote,'');
  if(base?.received_date&&base.version!==remote.version&&!equal(local,base))conflicts['/version']={local:base.version,remote:remote.version,recordChanged:true};
  for(const path of Object.keys(previous))if(!equal(at(draft,path),at(remote,path)))conflicts[path]={local:clone(at(draft,path)),remote:clone(at(remote,path))};
  if(previous['/version']?.recordChanged)conflicts['/version']=previous['/version'];
  return {base:clone(remote),local:draft,conflicts};
}
export function resolve(entry,useServer){
  const result=clone(entry);
  if(useServer&&result.conflicts['/version']?.recordChanged)result.local=clone(result.base);
  if(useServer)for(const path of Object.keys(result.conflicts))put(result.local,path,at(result.base,path));
  result.conflicts={};return result;
}
export function components(snapshot){return snapshot.messages.find(m=>m.updateComponents).updateComponents.components;}
export function model(snapshot){return snapshot.messages.find(m=>m.updateDataModel).updateDataModel.value;}
export function incrementalMessages(previous,next,currentData){
  const prior=new Map(components(previous).map(c=>[c.id,c]));
  const changed=components(next).filter(c=>!equal(c,prior.get(c.id)));
  const messages=[];
  if(changed.length)messages.push({version:'v0.9',updateComponents:{surfaceId:'transaction-desk',components:changed}});
  const data=model(next);
  for(const key of new Set([...Object.keys(currentData),...Object.keys(data)]))if(!equal(currentData[key],data[key]))messages.push({version:'v0.9',updateDataModel:{surfaceId:'transaction-desk',path:'/'+escape(key),value:data[key]??null}});
  return messages;
}
export function conflictErrors(snapshot,entry){
  const errors={};
  for(const c of components(snapshot)){
    const path=c.value?.path;
    if(!path?.startsWith('/draft/'))continue;
    const key=path.slice(6),conflict=entry?.conflicts[key];
    if(entry?.conflicts['/version']?.recordChanged)errors[c.id]='Another receipt changed this order. Review the remaining quantities and resolve above.';
    else if(conflict)errors[c.id]=`Changed elsewhere. Saved: ${JSON.stringify(conflict.remote)??'(removed)'}. Your edit: ${JSON.stringify(conflict.local)??'(removed)'}. Resolve the conflict above.`;
  }return errors;
}
export const consumesDraft=new Set(['save_deal','counter_save','counter_add','counter_remove','counter_visit','receiving_post','dispatch_confirm','field_queue']);
export const navigation=new Set(['set_view','select_deal','hvac_select','counter_select','receiving_select','open_record','close_record','back_record','refresh','filter','hvac_filter','hvac_chart_select','hvac_chart_clear','receiving_filter','receiving_page','dispatch_filter','dispatch_select','dispatch_preview','field_select','adaptive_select','case_select','case_tool','case_save','case_pin','case_history','case_preferences','case_receipt_preview','case_receive_confirm','case_schedule_preview','case_schedule_confirm']);
export function applyIncremental(processor,surface,previous,next){
  if(!surface.componentsModel)throw Error('This A2UI host does not support component reconciliation.');
  processor.processMessages(incrementalMessages(previous,next,surface.dataModel.get('/')));
  const ids=new Set(components(next).map(c=>c.id));
  for(const id of surface.componentsModel.keys)if(!ids.has(id))surface.componentsModel.removeComponent(id);
}
