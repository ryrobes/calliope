// Version-scoped browser drafts. Business data and action receipts remain on the server.
import {at,clone,equal} from './continuity.js';
export const snapshotModel=s=>s.messages.find(m=>m.updateDataModel).updateDataModel.value;
export const draftKey=(version,path,id)=>JSON.stringify([version,path,id]);
export function remember(snapshot,model,previous=[]){
 const records=new Map(previous.map(r=>[draftKey(r.version,r.path,r.id),clone(r)]));
 for(const form of snapshot.forms||[]){
  const value=at(model,form.path),id=value?.[form.key||'id'];if(id==null||id==='')continue;
  const key=draftKey(snapshot.version,form.path,id),old=records.get(key);
  const base=old?.base??at(snapshotModel(snapshot),form.source)??at(snapshotModel(snapshot),form.path);
  if(equal(value,base))records.delete(key);
  else records.set(key,{version:snapshot.version,path:form.path,id,base:clone(base),value:clone(value)});
 }
 return [...records.values()];
}
export const hints=(records,version)=>records.filter(r=>r.version===version).map(({path,value})=>({path,value}));
export function acknowledged(records,previous,next,model){
 return records.filter(r=>!(r.version===previous.version&&(next.resets||[]).includes(r.path)&&
  (previous.forms||[]).some(f=>f.path===r.path&&at(model,f.path)?.[f.key||'id']===r.id)));
}
export function updateProblem(records,pending){
 return pending?'Resolve the pending submission before changing versions.':records.length?'Finish or discard your drafts before changing versions. Your work stays on its original version.':null;
}
export const definitivelyRejected=code=>['INVALID_INPUT','INVALID_REQUEST_KEY','ACTION_NOT_FOUND','CONFLICT','ACTION_TIMEOUT','ACTION_FAILED','RESULT_TOO_LARGE','TOO_LARGE'].includes(code);
// Serialized commits prevent a slow autosave from overwriting a pending command.
export class Journal {
 constructor(read,write){this.read=read;this.write=write;this.tail=Promise.resolve();this.revisions=new Map();}
 key(viewer,slug){if(!viewer)throw Error('The server must identify the signed-in user before saving a draft.');return 'published:'+JSON.stringify([viewer,slug]);}
 async get(viewer,slug){const id=this.key(viewer,slug),value=await this.read(id);this.revisions.set(id,value?.revision||0);return value;}
 save(viewer,slug,value){const entry={...clone(value),id:this.key(viewer,slug)};this.tail=this.tail.catch(()=>{}).then(async()=>{const revision=this.revisions.get(entry.id)||0;entry.revision=revision+1;await this.write(entry,revision);this.revisions.set(entry.id,entry.revision);});return this.tail;}
}
