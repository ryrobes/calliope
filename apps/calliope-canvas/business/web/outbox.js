// IndexedDB commits evidence and immutable submissions before any network attempt.
const openDB=()=>new Promise((resolve,reject)=>{const q=indexedDB.open('calliope-field-v1',1);q.onupgradeneeded=()=>{for(const name of ['files','queue','cache'])q.result.createObjectStore(name,{keyPath:'id'});};q.onsuccess=()=>resolve(q.result);q.onerror=()=>reject(q.error);});
async function transact(name,mode,fn){const db=await openDB();return new Promise((resolve,reject)=>{const tx=db.transaction(name,mode),store=tx.objectStore(name);let value;fn(store,v=>value=v);tx.oncomplete=()=>{db.close();resolve(value);};tx.onerror=()=>{db.close();reject(tx.error);};tx.onabort=()=>{db.close();reject(tx.error||Error('Device storage failed'));};});}
export const all=name=>transact(name,'readonly',(s,set)=>{s.getAll().onsuccess=e=>set(e.target.result);});
export const get=(name,id)=>transact(name,'readonly',(s,set)=>{s.get(id).onsuccess=e=>set(e.target.result);});
export const put=(name,value)=>transact(name,'readwrite',s=>s.put(value));
export const remove=(name,id)=>transact(name,'readwrite',s=>s.delete(id));
// One compare-and-write transaction stops two app tabs from overwriting each other's draft.
export async function comparePut(name,value,revision){
 const db=await openDB();return new Promise((resolve,reject)=>{
  const tx=db.transaction(name,'readwrite'),store=tx.objectStore(name);let conflict=false;
  store.get(value.id).onsuccess=e=>{
   if((e.target.result?.revision||0)!==revision){conflict=true;tx.abort();}
   else store.put(value);
  };
  tx.oncomplete=()=>{db.close();resolve();};
  tx.onerror=tx.onabort=()=>{db.close();reject(conflict?Error('This app draft changed in another tab. Keep this tab open to copy any unsaved edits, then reload.'):tx.error||Error('Device storage failed'));};
 });
}

export async function capture(file){
 if(!['image/png','image/jpeg','application/pdf','text/plain'].includes(file.type)||file.size===0||file.size>2*1024*1024)throw Error('Choose PNG, JPEG, PDF or text, up to 2 MB.');
 const files=await all('files');if(files.reduce((n,f)=>n+f.size,0)+file.size>30*1024*1024)throw Error('Device evidence storage is full. Submit or remove saved reports.');
 const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=()=>reject(r.error);r.readAsDataURL(file);});
 const record={id:'LOCAL-'+crypto.randomUUID(),name:file.name,mime:file.type,size:file.size,data};await put('files',record);return record;
}
export async function enqueue(draft){
 const queue=await all('queue');if(queue.filter(q=>q.status!=='delivered').length>=20)throw Error('Send pending reports before adding more.');
 const files=[];for(const id of draft.attachments||[]){const file=await get('files',id);if(!file)throw Error('A local evidence file is missing. Your draft is retained.');files.push({name:file.name,mime:file.mime,data:file.data});}
 const id=crypto.randomUUID(),record={id,status:'pending',created:new Date().toISOString(),body:{requestId:id,draft:structuredClone(draft),files,source:'Web'},error:''};
 if(JSON.stringify(queue.filter(q=>q.status!=='delivered')).length+JSON.stringify(record).length>40*1024*1024)throw Error('Device queue is full. Send saved reports first.');
 await put('queue',record);return record;
}
export async function send(record){
 if(record.status!=='pending')return record;
 try{const response=await fetch('/api/field-reports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(record.body),signal:AbortSignal.timeout(10000)});const result=await response.json();
  if(!response.ok){record.error=result.error||'Submission rejected';record.status=response.status>=400&&response.status<500?'review':'pending';}
  else{record.status='delivered';record.receipt=result.id;record.error='';for(const id of record.body.draft.attachments||[])await remove('files',id);record.body.files=[];}
 }catch{record.error='Waiting for a connection. Saved on this device.';}
 await put('queue',record);return record;
}
