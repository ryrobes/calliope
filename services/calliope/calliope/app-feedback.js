// Shared by authored HTML dashboards and the published native-app web renderer.
// Opening this dialog performs no work. Sending shares only this request/context.
export function open({slug,version,context={}}){
 if(window.RVBBIT_ACTIONS_DISABLED)return;
 if(!/^[a-zA-Z0-9_-]{1,160}$/.test(slug)||!Number.isInteger(version))throw Error('A saved app version is required');
 const packet={kind:'app_workspace',schema:'calliope.app-context.v1',intent:'improve',context_id:crypto.randomUUID(),slug,version,
  location:String(context.location||location.pathname).slice(0,8000),viewport:{width:innerWidth,height:innerHeight}};
 for(const key of ['visible','fields','actions','records','record_links'])packet[key]=structuredClone(context[key]||[]);
 if(new TextEncoder().encode(JSON.stringify(packet)).length>48000)throw Error('Screen context is too large; provide a smaller selection');
 const dialog=document.createElement('dialog');dialog.setAttribute('aria-label','Suggest an app improvement');
 dialog.style.cssText='color:var(--text,#eee);background:var(--panel,#171923);border:1px solid var(--line,#454854);border-radius:16px;padding:24px;width:min(560px,90vw);max-height:85vh;overflow:auto;font:inherit;box-sizing:border-box';
 dialog.innerHTML='<h2 style="margin-top:0">Suggest an improvement</h2><p>Send this request and captured screen context to the app owner. Other conversation turns stay private. This does not change the app or submit a record.</p><textarea aria-label="Requested improvement" rows="5" maxlength="12000" style="width:100%;box-sizing:border-box;padding:12px;font:inherit;color:inherit;background:transparent;border:1px solid var(--line,#454854)"></textarea><details><summary>Screen context being shared</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px"></pre></details><p role="status"></p><div style="display:flex;gap:12px"><button type="button" data-send>Send request</button><button type="button" data-close>Cancel</button></div>';
 dialog.querySelector('pre').textContent=JSON.stringify(packet,null,2);
 const send=dialog.querySelector('[data-send]'),input=dialog.querySelector('textarea'),status=dialog.querySelector('[role=status]');
 let session=null,submitted=false,payload=null;
 dialog.querySelector('[data-close]').onclick=()=>dialog.close();
 dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();input.focus();
 send.onclick=async()=>{
  if(!input.value.trim()||submitted)return;send.disabled=true;input.disabled=true;status.textContent='Sending…';
  payload||={message:input.value.trim(),client_turn_id:packet.context_id,origin_context:packet};
  try{
   if(!session){const response=await fetch('/api/calliope/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'App improvement · '+slug})});const data=await response.json();if(!response.ok)throw Error(data.error?.message||'Sign in to Calliope to send a request');session=data.id||data.session?.id;if(!session)throw Error('Could not open a Calliope conversation');}
   // One client turn key survives uncertain transport. Never silently create a duplicate.
   const response=await fetch('/api/calliope/sessions/'+encodeURIComponent(session)+'/turn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
   if(!response.ok){const data=await response.json();throw Error(data.error?.message||'Request unavailable');}
   submitted=true;input.disabled=true;status.textContent='Request sent to the app owner. Callie is responding in your conversation.';
   const link=document.createElement('a');link.href='/calliope?session='+encodeURIComponent(session);link.textContent=' Open conversation';status.append(link);
   const changes=document.createElement('a');changes.href='/apps/'+slug+'/changes';changes.textContent=' · Track updates';status.append(changes);
   dialog.querySelector('[data-close]').textContent='Close';
   // Drain the normal event stream so the agent run remains connected.
   response.text().catch(()=>{});
  }catch(error){status.textContent=error.message+' Retry keeps the same request to avoid duplicates.';send.disabled=false;send.textContent='Retry request';}
 };
}
