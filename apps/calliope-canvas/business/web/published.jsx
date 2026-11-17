// Shared catalog and Calliope transport; no fixture server or local database.
import React,{useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {MessageProcessor} from '@a2ui/web_core/v0_9';
import {A2uiSurface,MarkdownContext} from '@a2ui/react/v0_9';
import {renderMarkdown} from '@a2ui/markdown-it';
import {workspaceCatalog} from './catalog.js';
import {AttachmentHost} from './fields.js';
import {ReactiveContext} from './regions.js';
import {LiveFilters} from './live-filters.js';
import {at,equal,clone} from './continuity.js';
import {get,comparePut} from './outbox.js';
import {Journal,remember,hints,acknowledged,updateProblem,definitivelyRejected} from './published-recovery.js';
import {readRecord,recordHref} from './record-links.js';
import {publishedRoute} from './published-route.js';
import './node_modules/@a2ui/react/v0_9/index.css';
import './style.css';
const watched=v=>(v?.snapshot.watch||[]).map(p=>clone(v.surface.dataModel.get(p)));

const route=publishedRoute(location.pathname),slug=route?.[1];
const journalSlug=route?.[2]?`${slug}@${route[2]}`:slug;
const previewRoute=/^\/api\/calliope\/sessions\/[^/]+\/artifact-work\/[^/]+\/preview$/.test(location.pathname);
const embedded=document.getElementById('calliope-preview-snapshot');
const preview=previewRoute||Boolean(embedded);
const embeddedSnapshot=embedded?JSON.parse(embedded.textContent):null;
function App(){
 const recordLink=useRef(readRecord(location.hash,slug));
 const uploading=useRef(false),recovered=useRef(false),records=useRef([]),stored=useRef(null);
 const journal=useRef(new Journal(id=>get('cache',id),(entry,revision)=>comparePut('cache',entry,revision)));
 const [discarding,setDiscarding]=useState(false);
 const [draftCount,setDraftCount]=useState(0),[storageError,setStorageError]=useState(null);
 async function openRecord(reference){
  if(preview||flight.current||uploading.current)return;
  if(retry.current){setFailed(true);setNotice('Resolve the pending submission before opening another record.');return;}
  const href=recordHref(reference);if(!href)return;
  try{await persist();}catch{return;}
  const samePage=new URL(href,location.href).pathname===location.pathname;
  location.assign(href);if(samePage)location.reload();
 }
 async function persist(v=current.current){
  if(preview||!v||!v.snapshot.viewer)return;
  const model=clone(v.surface.dataModel.get('/'));
  records.current=remember(v.snapshot,model,records.current);setDraftCount(records.current.length);
  const value={version:v.snapshot.version,state:model.state,records:records.current,pending:retry.current};
  if(equal(value,stored.current))return;
  try{await journal.current.save(v.snapshot.viewer,journalSlug,value);stored.current=clone(value);setStorageError(null);}
  catch(e){setStorageError(e.message.includes('another tab')?e.message:'Cannot save this browser draft. Keep this tab open and free device storage before submitting or updating.');throw e;}
 }
 async function useLatest(){
  const v=current.current;if(!v||flight.current)return;
  try{await persist(v);}catch{return;}
  const problem=updateProblem(records.current,retry.current);if(problem){setFailed(true);setNotice(problem);return;}
  await load(null,{version:v.snapshot.latest_version},true);
 }
 async function discardDrafts(){
  if(flight.current||retry.current)return;
  setDiscarding(false);
  await load(null,{version:current.current.snapshot.version},true);
 }
 const current=useRef(null),flight=useRef(false),alive=useRef(true),filters=useRef(new LiveFilters()),retry=useRef(null),queued=useRef(null),command=useRef(false),watch=useRef({seen:[],due:null});
 const [view,setView]=useState(null),[notice,setNotice]=useState('Opening app…'),[busy,setBusy]=useState(false),[failed,setFailed]=useState(false),[locked,setLocked]=useState(false),[reactivePending,setReactivePending]=useState(false);
 async function load(action=null,repeat=null,switching=false){
  if((!slug&&!preview)||uploading.current||(!repeat&&retry.current))return;
  if(!current.current && recordLink.current.error){setFailed(true);setNotice(recordLink.current.error);return;}
  if(flight.current){if(action&&!command.current&&!queued.current){queued.current=action;setLocked(true);}return;}
  const v=current.current,state=v?clone(v.surface.dataModel.get('/state')):undefined;
  const body=repeat||{version:v?.snapshot.version??(route?.[2]?Number(route[2]):undefined),...(state?{state}:{}),...(action?{event:action.name,context:action.context||{},request_key:crypto.randomUUID()}: {})};
  if(!v && recordLink.current.reference)body.record=recordLink.current.reference;
  if(v&&!switching&&!preview){records.current=remember(v.snapshot,clone(v.surface.dataModel.get('/')),records.current);if(!repeat)body.drafts=hints(records.current,body.version);}
  const isCommand=!preview&&(v?.snapshot.commands||[]).includes(body.event);
  if(preview){delete body.version;delete body.request_key;body.expected_hash=v?.snapshot.preview?.definition_sha256;}
  if(action)setReactivePending(true);
  flight.current=true;command.current=Boolean(action)||switching;setBusy(true);setLocked(Boolean(action)||switching);setNotice(action?'Working…':'Refreshing…');setFailed(false);
  try{
   if(isCommand){retry.current=body;await persist(v);} // Journal the exact key/payload before sending.
   const response=embeddedSnapshot?{ok:true,json:async()=>clone(embeddedSnapshot)}:await fetch(previewRoute?location.pathname+'/ui':`/api/apps/${slug}/ui`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(45000)});
   let next=await response.json();
   if(!response.ok){const error=new Error(next.error?.message||next.error?.code||`HTTP ${response.status}`);error.definitive=definitivelyRejected(next.error?.code);throw error;}
   if(!v&&!preview&&!recovered.current){
    recovered.current=true;
    const saved=await journal.current.get(next.viewer,journalSlug);
    if(saved&&(!route?.[2]||saved.version===Number(route[2]))){
     records.current=saved.records||[];retry.current=saved.pending||null;
     // Authorization comes first. Restore original version via a read, never replay a command.
     const target=recordLink.current.reference&&!saved.pending?{record:recordLink.current.reference}:{state:saved.state};
     const restored=await fetch(`/api/apps/${slug}/ui`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:saved.version,...target,drafts:hints(records.current,saved.version)}),signal:AbortSignal.timeout(45000)});
     const result=await restored.json();if(!restored.ok)throw Error(result.error?.message||'Cannot restore the saved app version. The browser draft is retained.');next=result;
    }
   }else if(isCommand)retry.current=null;
   if(!alive.current||uploading.current)return;
   if(!switching&&!action&&v&&!equal(state,v.surface.dataModel.get('/state')))return;
   if(action&&v&&!equal(state,v.surface.dataModel.get('/state')))next.messages[2].updateDataModel.value.state=clone(v.surface.dataModel.get('/state'));
   if(switching){records.current=[];v?.subscription.unsubscribe();v?.edits.unsubscribe();v?.processor.dispose();current.current=null;}
   else if(v){records.current=acknowledged(records.current,v.snapshot,next,clone(v.surface.dataModel.get('/')));}
   if(v&&!switching){v.processor.processMessages(next.messages.slice(1));v.snapshot=next;setView({...v});}
   else{
    const processor=new MessageProcessor([workspaceCatalog],a=>load(a));processor.processMessages(next.messages);
    const surface=processor.getSurfaces().get('published-app');
    const subscription=surface.onError.subscribe(e=>{setFailed(true);setNotice(e.message);});
    const edits=surface.dataModel.subscribe('/state',()=>{if(!flight.current&&!preview)persist().catch(()=>{});});
    current.current={processor,surface,snapshot:next,subscription,edits};setView(current.current);
   }
   if(!preview)await persist(current.current);
   filters.current.install(next.messages[1].updateComponents.components,current.current.surface.dataModel.get('/'));
   watch.current={seen:watched(current.current),due:null};setReactivePending(false);
   const errors=Object.entries(next.queries).filter(([,q])=>q.error);
   setFailed(errors.length>0);setNotice(errors.length?errors.map(([k,q])=>`${k}: ${q.error.message||q.error.code}`).join(' · '):preview?(next.preview_event?.action?`Simulated ${next.preview_event.action} · no changes submitted`:`Draft preview · ${next.preview.mode==='server-read'?'authorized server reads':'supplied data'} · actions are simulated`):next.receipt?'Saved in Calliope.':'Connected to Calliope · edits remain unsaved until submitted');
  }catch(e){if(alive.current){if(!v)recovered.current=false;if(isCommand&&e.definitive){retry.current=null;try{await persist(v);}catch{}}setReactivePending(false);setFailed(true);setNotice(e.message+(retry.current?' Outcome uncertain; Retry uses the same request key.':''));}}
  finally{flight.current=false;command.current=false;if(alive.current){setBusy(false);setLocked(uploading.current||Boolean(retry.current));const pending=queued.current;queued.current=null;if(pending)load(pending);}}
 }
 useEffect(()=>{
  alive.current=true;load();
  fetch('/api/calliope/appearance').then(r=>r.json()).then(t=>{
   const dark=t.mode!=='light',p=t.appearance?.palette||{},h=Number(p.baseHue)||250,style=document.documentElement.style;
   const values={bg:dark?`oklch(.13 .035 ${h})`:'oklch(.96 .008 95)',panel:dark?`oklch(.17 .035 ${h})`:'oklch(.99 .004 95)',card:dark?`oklch(.20 .04 ${h})`:'oklch(.94 .012 95)',text:dark?'oklch(.92 0 0)':'oklch(.21 .015 260)',accent:p[dark?'vibrant':'darkVibrant']||(dark?'#92bcae':'#27614e'),muted:dark?'#a6b7bb':'#52626a',line:dark?'#36484d':'#c8d0d0',on_accent:dark?'#14251b':'#ffffff'};
   for(const [k,v] of Object.entries(values))style.setProperty('--'+k,v);
   style.colorScheme=dark?'dark':'light';
  }).catch(()=>{});
  const tick=setInterval(()=>{const v=current.current;if(!v)return;filters.current.observe(v.surface.dataModel.get('/'),performance.now());const values=watched(v);if(!equal(values,watch.current.seen)){watch.current={seen:values,due:performance.now()+450};setReactivePending(true);}if(!flight.current){const a=filters.current.poll(performance.now());if(a||(watch.current.due!=null&&performance.now()>=watch.current.due)){watch.current.due=null;load();}}},100);
  const refresh=setInterval(()=>{if(!filters.current.pending()&&!retry.current)load();},15000);
  return()=>{alive.current=false;clearInterval(tick);clearInterval(refresh);current.current?.subscription.unsubscribe();current.current?.edits.unsubscribe();current.current?.processor.dispose();};
 },[]);
 async function suggest(){
  try{
   const v=current.current;if(!v)return;
   const feedbackModule='/api/apps/feedback.js';
   const {open}=await import(/* @vite-ignore */ feedbackModule);
   const nodes=v.snapshot.messages[1].updateComponents.components,model=v.surface.dataModel.get('/');
   const fields=[...document.querySelectorAll('main [data-field-id]')].filter(el=>el.getClientRects().length&&!el.readOnly&&!el.disabled).slice(0,40).flatMap(el=>{
    const n=nodes.find(n=>n.id===el.dataset.fieldId),path=n?.value?.path;
    const form=(v.snapshot.forms||[]).find(f=>path?.startsWith(f.path+'/'));
    if(!form||/password|secret|token|cookie|authorization|api_?key/i.test([n.id,n.label,path].join(' ')))return [];
    const value=at(model,path),saved=at(v.snapshot.messages[2].updateDataModel.value,form.source+path.slice(form.path.length));
    if(JSON.stringify(value??null).length>6000)return [];
    return [{id:n.id,label:n.label,path,value,saved_available:saved!==undefined,saved_value:saved,edited:saved===undefined?null:!equal(value,saved)}];
   });
   open({slug,version:v.snapshot.version,context:{location:JSON.stringify(v.snapshot.navigation?.paths?.map(path=>[path,v.surface.dataModel.get(path)])||[]),
    record_links:v.snapshot.records.map(r=>({title:r.title,reference:r.reference})),visible:[{title:v.snapshot.title}],fields}});
  }catch(error){setNotice(error.message);}
 }
 return <div className="app"><header><div><div className="eyebrow">CALLIOPE / {preview?'APP PREVIEW':'COMPANY APP'}</div><h1>{view?.snapshot.title||'Opening…'}</h1></div><div className="toolbar"><span>{preview?view?.snapshot.preview?.definition_sha256.slice(0,8):`v${view?.snapshot.version||'—'}`}</span><button disabled={busy} onClick={()=>load()}>Refresh</button>{!preview&&view&&<button onClick={suggest}>Suggest improvement</button>}{!preview&&!route?.[2]&&view?.snapshot.latest_version>view?.snapshot.version&&<button disabled={busy||Boolean(storageError)} onClick={useLatest}>Use v{view.snapshot.latest_version}</button>}{!preview&&draftCount>0&&<button disabled={busy||Boolean(retry.current)} onClick={()=>setDiscarding(true)}>Discard drafts ({draftCount})</button>}{retry.current&&<button disabled={busy} onClick={()=>load({name:retry.current.event},retry.current)}>Retry action</button>}</div></header>{!preview&&view?.snapshot.update&&<details><summary>{view.snapshot.latest_version>view.snapshot.version?`New: v${view.snapshot.latest_version}`:`About v${view.snapshot.version}`}</summary><p>{view.snapshot.update.notes||'No release notes'}</p><a href={view.snapshot.update.compare_url} target="_blank" rel="noopener">Compare revisions</a> · <a href={`/apps/${slug}/changes`} target="_blank" rel="noopener">Improvement requests</a></details>}{discarding&&<div className="toolbar" role="group" aria-label="Discard browser drafts"><span>Discard unsaved drafts for this app on this browser?</span><button onClick={discardDrafts}>Confirm discard</button><button onClick={()=>setDiscarding(false)}>Keep editing</button></div>}<p role="status" className={'notice'+(failed?' error':'')}>{storageError||notice}{retry.current&&!busy?' Pending submission retained; use Retry action.':!preview&&draftCount>0?` · ${draftCount} draft(s) on this browser`:''}</p><main data-preview-ready={Boolean(view&&!busy&&!failed)} aria-busy={busy} inert={locked?true:undefined}>{view&&<MarkdownContext.Provider value={renderMarkdown}><ReactiveContext.Provider value={reactivePending}><AttachmentHost.Provider value={{preview,published:true,openRecord,upload:`/api/apps/${slug}/files`,download:id=>`/api/calliope/app-files/${encodeURIComponent(id)}`,pending:value=>{uploading.current=value;setLocked(value||command.current);}}}><A2uiSurface surface={view.surface}/></AttachmentHost.Provider></ReactiveContext.Provider></MarkdownContext.Provider>}</main></div>;
}
createRoot(document.getElementById('root')).render(<App/>);
