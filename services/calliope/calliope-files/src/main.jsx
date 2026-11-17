import React,{useCallback,useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {popupContainer} from '@svar-ui/react-core';
import {Filemanager,Willow,getMenuOptions} from '@svar-ui/react-filemanager';
import '@svar-ui/react-filemanager/all.css';
import './icons.css';
import './theme.css';

const relative=id=>id.replace(/^\//,'');
const join=(parent,name)=>(parent==='/'?'':parent)+'/'+name;
const topLevel=ids=>ids.filter(id=>!ids.some(parent=>parent!==id&&id.startsWith(parent+'/')));
function entries(items){
  const result=new Map();
  for(const f of items){
    const parts=f.name.split('/');
    for(let i=1;i<parts.length;i++){
      const id='/'+parts.slice(0,i).join('/');
      if(!result.has(id))result.set(id,{id,type:'folder'});
    }
    const id='/'+f.name;
    result.set(id,{id,type:f.type||'file',size:f.bytes,date:f.modified_at||f.created_at?new Date(f.modified_at?f.modified_at*1000:f.created_at):undefined,...f});
  }
  return [...result.values()];
}
function App({sid,usePath,showDesktop,close}){
  const base='/api/calliope/sessions/'+encodeURIComponent(sid)+'/files';
  const [listing,setListing]=useState(null),[data,setData]=useState([]),[panels,setPanels]=useState([{path:'/'}]);
  const [selected,setSelected]=useState([]),[busy,setBusy]=useState(false),[status,setStatus]=useState('Loading files…');
  const [originals,setOriginals]=useState(false),[mode,setMode]=useState('table');
  const api=useRef(),live=useRef({}),pending=useRef(false),alive=useRef(true),signature=useRef(''),files=useRef(),folders=useRef();
  live.current={listing,originals};
  const writable=!!listing?.writable&&!originals&&listing?.delivery?.status!=='unavailable';
  const currentPath=()=>{const s=api.current?.getState();return s?.panels[s.activePanel]?.path||'/';};
  async function json(url,init){
    const response=await fetch(url,init);const body=await response.json();
    if(!response.ok)throw new Error(body.error?.message||'The file operation failed.');
    return body;
  }
  const refresh=useCallback(async(force=false)=>{
    const result=await json(base);
    if(!alive.current)return;
    const key=JSON.stringify(result);
    if(!force&&signature.current===key)return;
    signature.current=key;setListing(result);
    const raw=live.current.originals?result.originals:result.files;
    const items=entries(raw),ids=new Set(items.map(f=>f.id));
    const state=api.current?.getState();
    setPanels((state?.panels||[{path:'/'}]).map(p=>({path:ids.has(p.path)?p.path:'/',selected:(p.selected||[]).filter(id=>ids.has(id))})));
    setData(items);setSelected((state?.panels[state.activePanel]?.selected||[]).filter(id=>ids.has(id)));
    setStatus(result.delivery?.status==='unavailable'?'Desktop unavailable. Uploaded originals are still available.':result.delivery?.pending?`${result.delivery.pending} upload(s) waiting for the desktop.`:result.truncated?'Showing the first 5,000 desktop entries.':'Shared with Callie’s desktop · updates automatically');
  },[base]);
  useEffect(()=>{
    alive.current=true;refresh().catch(e=>setStatus(e.message));
    const timer=setInterval(()=>{if(!pending.current&&!document.hidden)refresh().catch(e=>setStatus(e.message));},5000);
    return()=>{alive.current=false;clearInterval(timer);};
  },[refresh]);
  async function work(fn){
    if(pending.current)return false;
    pending.current=true;setBusy(true);setStatus('Updating files…');
    try{await fn();await refresh(true);}
    catch(e){await refresh(true).catch(()=>{});if(alive.current)setStatus(e.message);}
    finally{pending.current=false;if(alive.current)setBusy(false);}
    return false;
  }
  const action=(operation,path,target)=>json(base+'/action',{method:'POST',headers:{'Content-Type':'application/json','X-Calliope-Files':'1'},body:JSON.stringify({operation,path:relative(path),...(target?{target:relative(target)}:{})})});
  async function uploadOne(file,path){
    if(file.size>50*1024*1024)throw new Error(`${file.name} exceeds 50 MB.`);
    return json(base,{method:'POST',headers:{'X-Calliope-Files':'1','X-File-Name':encodeURIComponent(relative(path)),'Content-Type':file.type||'application/octet-stream'},body:file});
  }
  const upload=items=>{
    const parent=currentPath();
    return work(async()=>{const errors=[];for(const file of items){try{await uploadOne(file,join(parent,file.webkitRelativePath||file.name));}catch(e){errors.push(e.message);}}if(errors.length)throw new Error(errors.join('; '));});
  };
  function download(id,original=false){
    const state=live.current,entry=state.listing?.files.find(f=>'/'+f.name===id);
    const saved=state.listing?.originals.find(f=>original&&!state.originals?f.shared_file_id===entry?.original_id:'/'+f.name===id);
    const url=original||state.originals?saved?.url:base+'/download?path='+encodeURIComponent(relative(id));
    if(!url){setStatus('No uploaded original is associated with this file.');return;}
    const link=document.createElement('a');link.href=url;link.download='';link.click();
  }
  const init=useCallback(instance=>{
    api.current=instance;
    const syncSelection=()=>{const s=instance.getState();setSelected([...s.panels[s.activePanel].selected]);};
    instance.on('select-file',syncSelection);instance.on('set-path',syncSelection);
    instance.on('set-mode',e=>setMode(e.mode));
    instance.intercept('set-mode',e=>e.mode==='panels'?false:undefined);
    instance.intercept('request-data',()=>{refresh(true).catch(e=>setStatus(e.message));return false;});
    instance.intercept('download-file',e=>{download(e.id);return false;});
    instance.intercept('open-file',e=>{download(e.id);return false;});
    for(const name of ['create-file','rename-file','move-files','copy-files','delete-files'])instance.intercept(name,e=>{
      const s=live.current;if(!s.listing?.writable||s.originals||s.listing?.delivery?.status==='unavailable')return false;
      return work(async()=>{
        if(name==='create-file'){
          if(e.file.file)await uploadOne(e.file.file,join(e.parent,e.file.name));
          else if(e.file.type==='folder')await action('mkdir',join(e.parent,e.file.name));
          else throw new Error('Use Add files to upload a document.');
        }else if(name==='rename-file'){
          if(!e.name||e.name.includes('/'))throw new Error('Enter a filename without slashes.');
          await action('rename',e.id,join(e.id.slice(0,e.id.lastIndexOf('/'))||'/',e.name));
        }else{
          const errors=[];
          for(const id of topLevel(e.ids))try{await action(name==='delete-files'?'trash':name==='move-files'?'move':'copy',id,e.target?join(e.target,id.split('/').pop()):null);}catch(error){errors.push(error.message);}
          if(errors.length)throw new Error(errors.join('; '));
        }
      });
    });
  },[]);
  const menu=useCallback((type)=>getMenuOptions(type).filter(item=>item.id!=='add-file').map(item=>({...item,icon:item.id==='add-folder'?'wxi-plus':item.id==='upload'?'wxi-upload':item.icon})),[]);
  const selectedOriginal=selected.length===1&&listing?.files.find(f=>'/'+f.name===selected[0])?.original_id;
  function useSelection(){
    const paths=(selected.length?selected:[currentPath()]).map(id=>listing.desktop_path+(id==='/'?'':id));
    usePath(paths.join('\n'),sid);close();
  }
  async function openDesktop(){
    const id=selected[0]||currentPath();
    if(id==='/'){setStatus('Select a file or folder to open on the desktop.');return;}
    await work(async()=>{await action('open',id);showDesktop?.();close();});
  }
  function toggleOriginals(){
    live.current.originals=!originals;setOriginals(!originals);signature.current='';setSelected([]);api.current?.exec('set-path',{id:'/'});refresh(true).catch(e=>setStatus(e.message));
  }
  return <div className="calliope-files">
    <header className="files-heading"><div><strong>Files</strong><span>Shared with Callie</span></div><button type="button" onClick={close} aria-label="Close files">×</button></header>
    <div className="files-actions">
      <button disabled={!listing?.writable||originals||busy} onClick={()=>files.current.click()}>Add files</button>
      <button disabled={!listing?.writable||originals||busy} onClick={()=>folders.current.click()}>Add folder</button>
      <input ref={files} aria-label="Upload files" type="file" multiple hidden onChange={e=>{upload([...e.target.files]);e.target.value='';}}/>
      <input ref={folders} aria-label="Upload folder" type="file" multiple webkitdirectory="" hidden onChange={e=>{upload([...e.target.files]);e.target.value='';}}/>
      <button disabled={busy||!listing||originals} onClick={useSelection}>{selected.length?'Use selected in chat':'Use folder in chat'}</button>
      <button disabled={!writable||busy||selected.length>1} onClick={openDesktop}>Open on desktop</button>
      <button disabled={busy||(!originals&&!selectedOriginal)||selected.length!==1} onClick={()=>download(selected[0],true)}>Download original</button>
      <button disabled={busy} aria-pressed={originals} onClick={toggleOriginals}>{originals?'Working files':'Uploaded originals'}</button>
      <button disabled={busy} onClick={()=>refresh(true).catch(e=>setStatus(e.message))}>Refresh</button>
    </div>
    <p className="files-status" role="status">{originals?'Uploaded originals · read only. ':''}{status}</p>
    <div className="files-manager" aria-busy={busy} onDragOver={e=>{e.stopPropagation();}} onDrop={e=>{e.stopPropagation();}}>
      <Willow fonts={false}><Filemanager data={data} panels={panels} mode={mode} preview={false} readonly={!writable||busy} init={init} menuOptions={menu} icons="simple"/></Willow>
    </div>
    <footer>Files stay on the desktop across restarts. Deleted working files go to trash; uploaded originals are retained.</footer>
  </div>;
}
export function mount(host,options){popupContainer(host);const root=createRoot(host);root.render(<App {...options}/>);return root;}
