import React,{useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {MessageProcessor} from '@a2ui/web_core/v0_9';
import {A2uiSurface,MarkdownContext} from '@a2ui/react/v0_9';
import {renderMarkdown} from '@a2ui/markdown-it';
import {workspaceCatalog} from './catalog.js';
import {fieldError,FieldResetContext} from './fields.js';
import {at,clone,equal,recordKey,reconcile,resolve,components,model,applyIncremental,conflictErrors,consumesDraft,navigation,acknowledge} from './continuity.js';
import './node_modules/@a2ui/react/v0_9/index.css';
import './style.css';
import * as device from './outbox.js';
import {Reactive,compose} from './reactive.js';
import {LiveFilters} from './live-filters.js';
const STORAGE='calliope-business-drafts-v2';
async function request(url,body){
  const result=await fetch(url,{signal:AbortSignal.timeout(10000),...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
  const data=await result.json();if(!result.ok){const error=new Error(data.error||`Request failed (${result.status})`);error.fields=data.fields||{};error.status=result.status;throw error;}return data;
}
function readDrafts(){try{const d=JSON.parse(localStorage.getItem(STORAGE)||'{}');return d&&typeof d==='object'&&!Array.isArray(d)&&Object.keys(d).length<=100?d:{};}catch{return {};}}
function Desk(){
  const [cases,setCases]=useState([]),[contextPreview,setContextPreview]=useState('');
  const [offlineReady,setOfflineReady]=useState(false);
  const [outbox,setOutbox]=useState([]);const sending=useRef(false);
  async function refreshQueue(){setOutbox((await device.all('queue')).sort((a,b)=>b.created.localeCompare(a.created)));}
  async function flushQueue(){if(sending.current)return;sending.current=true;try{for(const item of await device.all('queue'))if(item.status==='pending')await device.send(item);await refreshQueue();}catch(e){setError(true);setNotice('Device queue: '+e.message);}finally{sending.current=false;}}
  const [formEpoch,setFormEpoch]=useState(0);
  const [view,setView]=useState(null),[busy,setBusy]=useState(false),[dirty,setDirty]=useState(false),[notice,setNotice]=useState('Opening the business desk…'),[error,setError]=useState(false),[draftKeys,setDraftKeys]=useState([]),[conflictCount,setConflictCount]=useState(0);
  const syncFailed=useRef(false);
  const reactive=useRef(new Reactive());
  const liveFilters=useRef(new LiveFilters()),filterFlight=useRef(null);
  const [filterUpdating,setFilterUpdating]=useState(false);
  const current=useRef(null),pending=useRef(false),polling=useRef(false),generation=useRef(0),mounted=useRef(true),installing=useRef(false),drafts=useRef(readDrafts());
  const isDirty=()=>{const v=current.current;return !!v&&!equal(v.surface.dataModel.get('/draft'),v.snapshot.draft);};
  function persist(){
    const v=current.current;if(v&&!installing.current){
      const local=clone(v.surface.dataModel.get('/draft'));
      v.entry.local=local;
      if(!equal(local,v.snapshot.draft)||Object.keys(v.entry.conflicts).length)drafts.current[recordKey(v.snapshot)]={...v.entry,view:v.snapshot.view,selected:v.snapshot.selected};
      else delete drafts.current[recordKey(v.snapshot)];
    }
    try{localStorage.setItem(STORAGE,JSON.stringify(drafts.current));}
    catch{setError(true);setNotice('Local draft storage is unavailable. Keep this window open until your edits are saved.');}
    setDraftKeys(Object.keys(drafts.current));setDirty(isDirty());setConflictCount(Object.keys(v?.entry.conflicts||{}).length);
  }
  function install(snapshot,mode='merge'){
    persist();const previous=current.current,discardFiles=mode==='discard'&&previous?.snapshot.view==='field'?[...(previous.entry.local.attachments||[])]:[],key=recordKey(snapshot),same=previous&&recordKey(previous.snapshot)===key;
    if(mode==='ack'||mode==='discard')delete drafts.current[key];
    if(!same||mode==='ack'||mode==='discard')setFormEpoch(n=>n+1);
    let cached=drafts.current[key];
    if(cached&&snapshot.view==='case')cached=acknowledge(cached,snapshot.draft,snapshot.acknowledgedDraftPaths);
    const entry=cached?reconcile(cached.base,cached.local,snapshot.draft,cached.conflicts):{base:clone(snapshot.draft),local:clone(snapshot.draft),conflicts:{}};
    const nextSnapshot=clone(snapshot),data=model(nextSnapshot);data.draft=entry.local;data.errors=conflictErrors(snapshot,entry);
    // Preserve unsaved filter/picker values on background and inspector updates.
    if(same&&mode==='merge')for(const k of ['controls','receiving_filter','hvac_filter','filter','dispatch_filter','planning','field_controls','preferences']){
      const old=previous.surface.dataModel.get('/'+k),base=model(previous.snapshot)[k];
      if(old!==undefined&&!equal(old,base))data[k]=clone(old);
    }
    reactive.current.install(snapshot);
    if(snapshot.reactive?.length){data.resources=reactive.current.states();nextSnapshot.messages.find(m=>m.updateComponents).updateComponents.components=compose(snapshot.baseComponents,snapshot.reactive,data.resources);}
    let processor,surface,cleanup;
    installing.current=true;
    try {
    if(same){
      ({processor,surface,cleanup}=previous);
      applyIncremental(processor,surface,previous.rendered||previous.snapshot,nextSnapshot);
    }else{
      processor=new MessageProcessor([workspaceCatalog],action=>act(action));processor.processMessages(nextSnapshot.messages);
      surface=processor.getSurfaces().get('transaction-desk');if(!surface)throw Error('A2UI did not create the business surface.');
      const draftSub=surface.dataModel.subscribe('/draft',()=>{if(!installing.current)persist();});
      const errorSub=surface.onError.subscribe(e=>{setError(true);setNotice(e.message);});
      cleanup=()=>{draftSub.unsubscribe();errorSub.unsubscribe();processor.dispose();};
    }
    } finally {installing.current=false;}
    liveFilters.current.install(components(nextSnapshot),surface.dataModel.get('/'));
    const next={processor,surface,snapshot,entry,cleanup,rendered:nextSnapshot};current.current=next;setView(next);installing.current=false;
    syncFailed.current=false;setError(false);setNotice(snapshot.notice||(Object.keys(entry.conflicts).length?'Another view edited the same fields. Resolve the highlighted conflicts.':cached?'Local draft restored. Live data continues to update around your edits.':'Changes sync between desktop and web.'));
    if(mode==='discard'&&same&&snapshot.view==='field')for(const id of discardFiles)device.remove('files',id).catch(()=>{});
    persist();if(snapshot.view==='field')device.put('cache',{id:'field',snapshot}).catch(()=>{});
  }
  function paintReactive(){
    const v=current.current;if(!v?.snapshot.reactive?.length)return;
    const states=reactive.current.states(),next=clone(v.snapshot);
    next.messages.find(m=>m.updateComponents).updateComponents.components=compose(v.snapshot.baseComponents,v.snapshot.reactive,states);
    next.messages.find(m=>m.updateDataModel).updateDataModel.value={...clone(v.surface.dataModel.get('/')),resources:states};
    applyIncremental(v.processor,v.surface,v.rendered||v.snapshot,next);v.rendered=next;
  }
  function tickFilters(){
    const v=current.current;if(!v)return;
    const runtime=liveFilters.current;
    runtime.observe(v.surface.dataModel.get('/'),performance.now());
    if(pending.current||polling.current||filterFlight.current)return;
    const action=runtime.poll(performance.now());if(!action)return;
    const seq=++generation.current;filterFlight.current=seq;setFilterUpdating(true);setError(false);setNotice('Updating results…');persist();
    request('/api/action',bodyFor(action)).then(next=>{
      if(!mounted.current||seq!==generation.current)return;
      if(runtime.matches(action.context,current.current.surface.dataModel.get('/')))install(next);
      else setNotice('Filters changed; showing previous results until the next update.');
    }).catch(e=>{
      if(mounted.current&&seq===generation.current&&runtime.matches(action.context,current.current.surface.dataModel.get('/'))){setError(true);setNotice(e.message+' Your filters and draft are kept. Change a filter to retry.');}
    }).finally(()=>{if(filterFlight.current===seq){filterFlight.current=null;if(mounted.current)setFilterUpdating(false);}});
  }
  function tickReactive(){
    tickFilters();
    const v=current.current;if(!v||pending.current)return;
    const runtime=reactive.current;
    let changed=runtime.observe(v.surface.dataModel.get('/'));
    const jobs=runtime.poll();changed=runtime.pending()||changed||jobs.length>0;
    if(changed)paintReactive();
    for(const job of jobs){
      const finish=(result,error)=>{if(!mounted.current)return;const v=current.current;if(!v)return;
        runtime.observe(v.surface.dataModel.get('/'));
        if(runtime.receive(job.id,job.ticket,result,error))paintReactive();
      };
      request('/api/reactive',job.body).then(result=>finish(result,null)).catch(error=>finish(null,error));
    }
  }
  const bodyFor=action=>{const s=current.current?.snapshot;return {...action,selected:s?.selected||'cedar',owner:s?.owner||'Everyone',view:s?.view||'dossier',state:s?.state,panes:s?.panes||[],source:'Web',requestId:crypto.randomUUID()};};
  async function act(action){
    if(pending.current)return;
    liveFilters.current.cancel();filterFlight.current=null;setFilterUpdating(false);generation.current++;
    if(reactive.current.action(action.name,action.context?.id)){paintReactive();return;}
    if(action.name==='open_attachment') {window.open('/api/attachments/'+encodeURIComponent(action.context.attachmentId),'_blank','noopener');return;}
    persist();const v=current.current;
    if(action.name==='case_context'&&v?.snapshot.view==='case'){
      const data=v.surface.dataModel.get('/');
      const text=JSON.stringify({...data.context,revision:v.snapshot.revision,unsubmittedDraft:clone(data.draft),semanticSupport:clone(data.resources?.support||{source:'user-preference',label:data.case.focus})},null,2);
      setContextPreview(text);
      try{await navigator.clipboard.writeText(text);setNotice('Case context copied. Review it with Callie; no command was submitted.');}catch{setNotice('Select and copy the case context below.');}
      return;
    }
    if(['case_save','case_receipt_preview','case_receive_confirm','case_schedule_preview','case_schedule_confirm'].includes(action.name)&&Object.keys(v?.entry.conflicts||{}).length){setError(true);setNotice('Resolve conflicting case fields before reviewing or saving.');return;}
    if(v?.surface.dataModel.get('/evidence_busy')){setError(true);setNotice('Wait for evidence to finish saving on this device.');return;}
    if(consumesDraft.has(action.name)){
      if(Object.keys(v?.entry.conflicts||{}).length){setError(true);setNotice('Resolve the conflicting fields before saving.');return;}
      const errors={};
      for(const c of components(v.snapshot))if(c.component==='CField'){
        const props={...c};for(const k of ['value','required','visible'])if(props[k]?.path)props[k]=v.surface.dataModel.get(props[k].path);
        const message=fieldError(props.value,props);if(message)errors[c.id]=message;
      }
      if(Object.keys(errors).length){v.surface.dataModel.set('/errors',errors);setError(true);setNotice('Check the highlighted fields before continuing.');return;}
    }
    if(isDirty()&&!consumesDraft.has(action.name)&&!navigation.has(action.name)&&action.name!=='discard'){
      setError(true);setNotice('Save your draft before changing this record’s workflow state.');return;
    }
    if(action.name==='field_queue'){
      pending.current=true;generation.current++;setBusy(true);
      try{await device.enqueue(clone(v.surface.dataModel.get('/draft')));install(v.snapshot,'ack');setNotice('Report and evidence saved on this device. Sending when connected.');await refreshQueue();void flushQueue();}catch(e){setError(true);setNotice(e.message);}finally{pending.current=false;setBusy(false);}return;
    }
    pending.current=true;generation.current++;syncFailed.current=false;setBusy(true);setError(false);
    try{
      const next=await request('/api/action',bodyFor(action));
      if(mounted.current)install(next,action.name==='discard'?'discard':consumesDraft.has(action.name)?'ack':'merge');
    }catch(e){if(mounted.current){
      if(e.status===409){try{install(await request('/api/action',bodyFor({name:'refresh',context:{}})));}catch{}}
      if(Object.keys(e.fields||{}).length)current.current?.surface.dataModel.set('/errors',{...e.fields,...conflictErrors(current.current.snapshot,current.current.entry)});
      setError(true);setNotice(`${e.message} Your draft is kept.`);
      if(!current.current&&action.name==='set_view'){const cached=await device.get('cache','field').catch(()=>null);if(cached){install(cached.snapshot);setNotice('Offline field notebook. Reports will remain in the device queue.');}}
    }}finally{pending.current=false;if(mounted.current)setBusy(false);}
  }
  function resolveConflicts(useServer){
    const v=current.current;if(!v)return;
    v.entry=resolve(v.entry,useServer);v.surface.dataModel.set('/draft',v.entry.local);v.surface.dataModel.set('/errors',{});persist();
    syncFailed.current=false;setError(false);setNotice(useServer?'Conflicting fields now use the server values. Other local edits are kept.':'Your conflicting edits are retained against the latest version. Review and save when ready.');
  }
  useEffect(()=>()=>view?.cleanup(),[view?.processor]);
  useEffect(()=>{
    mounted.current=true;
    const requested=new URLSearchParams(location.search).get('view');
    act({name:'set_view',context:{view:['dossier','pipeline','briefing','hvac','counter','receiving','dispatch','field','adaptive','case'].includes(requested)?requested:'dossier',...(requested==='case'?{id:new URLSearchParams(location.search).get('case')||'CASE-HARBOR'}:{})}});
    const loadCases=()=>request('/api/cases').then(v=>{if(mounted.current)setCases(v.cases);}).catch(()=>{});
    void loadCases();const caseTimer=setInterval(loadCases,3000);
    request('/api/theme').then(t=>{const style=document.documentElement.style;for(const key of ['bg','panel','card','text','muted','accent','on_accent','line','warm','cool','success'])if(t[key])style.setProperty(`--${key}`,t[key]);if(t.font)style.setProperty('--font',`"${t.font}",system-ui,sans-serif`);if(t.font_display)style.setProperty('--font-display',`"${t.font_display}",Georgia,serif`);if(t.font_mono)style.setProperty('--font-mono',`"${t.font_mono}",monospace`);style.colorScheme=t.dark===false?'light':'dark';}).catch(()=>{});
    void refreshQueue().catch(()=>{});void flushQueue();const outboxTimer=setInterval(flushQueue,15000);window.addEventListener('online',flushQueue);
    if('serviceWorker' in navigator)navigator.serviceWorker.register('/service-worker.js').then(()=>navigator.serviceWorker.ready).then(()=>setOfflineReady(true)).catch(()=>{});
    const reactiveTimer=setInterval(tickReactive,100);
    const timer=setInterval(async()=>{
      if(pending.current||polling.current||filterFlight.current||liveFilters.current.pending()||!current.current)return;
      polling.current=true;const seq=generation.current;
      try{const health=await request('/api/health');liveFilters.current.observe(current.current.surface.dataModel.get('/'),performance.now());
        if(pending.current||seq!==generation.current||liveFilters.current.pending())return;
        if(syncFailed.current){syncFailed.current=false;setError(false);setNotice('Live connection restored. Your draft is kept.');}
        if(health.revision===current.current.snapshot.revision)return;
        const next=await request('/api/action',bodyFor({name:'refresh',context:{}}));
        liveFilters.current.observe(current.current.surface.dataModel.get('/'),performance.now());
        if(mounted.current&&!pending.current&&seq===generation.current&&!liveFilters.current.pending())install(next);
      }catch(e){if(mounted.current){syncFailed.current=true;setError(true);setNotice(`Sync unavailable: ${e.message}. Local draft retained.`);}}finally{polling.current=false;}
    },4000);
    const saveDraft=()=>persist();window.addEventListener('pagehide',saveDraft);
    return()=>{persist();mounted.current=false;clearInterval(caseTimer);clearInterval(timer);clearInterval(reactiveTimer);clearInterval(outboxTimer);window.removeEventListener('online',flushQueue);window.removeEventListener('pagehide',saveDraft);};
  },[]);
  const workspace=view?.snapshot.view;
  return <>
    <nav><span className="mark">c</span><span>Calliope <span className="muted">/ Business desk</span></span><span className="grow"/><span className="badge">WEB VIEW</span></nav>
    <header><div><p className="eyebrow">{['counter','receiving'].includes(workspace)?'NORTHSTAR SUPPLY / FICTIONAL OPERATIONS':['hvac','dispatch','field','adaptive','case'].includes(workspace)?'NORTHSTAR SERVICE / FICTIONAL HVAC WORKSPACE':'MAPLE & CO. / FICTIONAL REAL ESTATE WORKSPACE'}</p></div><div className="toolbar"><span className="status">● {dirty?'Local draft saved':'Local SQL'}</span><button disabled={busy} onClick={()=>act({name:'refresh',context:{}})}>Reload</button></div></header>
    {cases.some(c=>c.pinned)&&<nav className="case-pins" aria-label="Pinned cases">{cases.filter(c=>c.pinned).map(c=><button key={c.id} disabled={busy} onClick={()=>act({name:'set_view',context:{view:'case',id:c.id}})}><span>{c.title}</span><small>{c.label}</small></button>)}</nav>}
    {contextPreview&&<section className="case-context"><button onClick={()=>setContextPreview('')}>Close context</button><p>Context for Callie · local fixture facts and unsubmitted drafts are labeled separately.</p><textarea aria-label="Case context for Callie" readOnly value={contextPreview}/></section>}
    <p role="status" className={`notice ${error?'error':''}`}>{notice}</p>
    {!!draftKeys.length&&<div className="draft-tabs" aria-label="Saved local drafts"><span className="field-help">Drafts</span>{draftKeys.map(k=><button key={k} onClick={()=>act({name:'set_view',context:{view:drafts.current[k].view,id:drafts.current[k].selected}})}>{drafts.current[k].selected} · {drafts.current[k].view}</button>)}</div>}
    {!!conflictCount&&<div className="conflict-actions" role="alert"><span>{conflictCount} conflicting field{conflictCount===1?'':'s'}. Review the highlighted values.</span><button onClick={()=>resolveConflicts(true)}>Use server values</button><button onClick={()=>resolveConflicts(false)}>Keep my edits</button></div>}
    {!!conflictCount&&<div className="conflict-details">{Object.entries(view.entry.conflicts).map(([path,conflict])=><p key={path}>{conflict.recordChanged?'Another receipt changed this purchase order. Check quantities still due before retaining this delivery.':`${path}: saved ${JSON.stringify(conflict.remote)??'(removed)'} · your edit ${JSON.stringify(conflict.local)??'(removed)'}`}</p>)}</div>}
    {!!outbox.length&&<section className="outbox" aria-label="Device report queue"><strong>Device report queue</strong>{outbox.slice(0,10).map(item=><div className="outbox-item" key={item.id}><span>{item.body.draft.id} · {item.status==='delivered'?`Delivered · ${item.receipt}`:item.status==='review'?'Needs review':'Waiting to send'}<small>{item.error||item.body.draft.notes.slice(0,100)}</small></span>{item.status!=='delivered'&&<button onClick={async()=>{if(sending.current)return;await device.put('queue',{...item,status:'pending'});await flushQueue();}}>Retry</button>}<button onClick={async()=>{if(sending.current)return;if(item.status!=='delivered'&&!confirm('Delete this queued report and its device evidence?'))return;for(const id of item.body.draft.attachments||[])await device.remove('files',id);await device.remove('queue',item.id);await refreshQueue();}}>{item.status==='delivered'?'Dismiss':'Delete queued report'}</button></div>)}</section>}
    <main aria-busy={busy||filterUpdating} inert={busy?true:undefined} className={busy?'pending':''}>{view&&<MarkdownContext.Provider value={renderMarkdown}><FieldResetContext.Provider value={formEpoch}><A2uiSurface key={recordKey(view.snapshot)} surface={view.surface}/></FieldResetContext.Provider></MarkdownContext.Provider>}</main>
    <footer>{workspace==='field'&&offlineReady?<span>Offline field notebook ready · </span>:null}Shared A2UI + SQL · local drafts stay on this device <a href={`/api/definition?view=${workspace||'dossier'}`} target="_blank" rel="noreferrer">Inspect A2UI ↗</a></footer>
  </>;
}
class ErrorBoundary extends React.Component{
  state={error:null};static getDerivedStateFromError(error){return {error};}
  render(){return this.state.error?<main><h1>Renderer error</h1><p>{this.state.error.message}</p><button onClick={()=>location.reload()}>Reload</button></main>:this.props.children;}
}
createRoot(document.getElementById('root')).render(<ErrorBoundary><Desk/></ErrorBoundary>);
