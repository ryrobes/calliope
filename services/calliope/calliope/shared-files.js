/* The file manager is loaded only when Files opens. */
(() => {
  const version=new URL(document.currentScript.src).searchParams.get('v')||'';
  let loading;
  function load(){
    return loading ||= Promise.all([
      new Promise((resolve,reject)=>{
        const css=document.createElement('link');css.rel='stylesheet';css.href='/calliope/files-runtime.css?v='+encodeURIComponent(version);
        css.onload=resolve;css.onerror=()=>{css.remove();reject(new Error('Could not load Files styles.'));};document.head.append(css);
      }),
      new Promise((resolve,reject)=>{
        const script=document.createElement('script');script.src='/calliope/files-runtime.js?v='+encodeURIComponent(version);
        script.onload=resolve;script.onerror=()=>{script.remove();reject(new Error('Could not load Files.'));};document.head.append(script);
      })
    ]).catch(e=>{loading=null;throw e;});
  }
  window.CalliopeSharedFiles={init(options){
    const dialog=document.createElement('dialog');dialog.className='shared-files-dialog';dialog.setAttribute('aria-label','Conversation files');
    const host=document.createElement('div');host.className='shared-files-host';dialog.append(host);document.body.append(dialog);
    let mounted,generation=0;
    dialog.addEventListener('close',()=>{generation++;mounted?.unmount();mounted=null;host.textContent='';});
    document.querySelector('#shared-files-open').onclick=async()=>{
      const session=options.getSession();
      if(!session){options.toast('Open a conversation first. You can attach files to a new message with Attach.');return;}
      if(dialog.open)return;
      const token=++generation;dialog.showModal();host.textContent='Loading Files…';
      try{
        await load();if(token!==generation||!dialog.open)return;
        host.textContent='';mounted=window.CalliopeFilesRuntime.mount(host,{...options,sid:session.id,close:()=>dialog.close()});
      }catch(e){host.textContent=e.message+' Press Escape to close and try again.';}
    };
  }};
})();
