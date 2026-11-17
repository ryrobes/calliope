(() => {
  'use strict';
  const API='/api/calliope/sms/simulator', $=id=>document.getElementById(id);
  const state={binding:null,files:[],sending:false,loading:false,version:'',attempt:null,maxBytes:8*1024*1024};
  const notice=message=>{ $('notice').textContent=message||''; $('notice').hidden=!message; };
  async function api(path='',body){
    const response=await fetch(API+path,{credentials:'same-origin',cache:'no-store',...(body!==undefined?{method:'POST',headers:{'content-type':'application/json','x-calliope-simulator':'1'},body:JSON.stringify(body)}:{})});
    if(response.status===401){location.href='/login?next='+encodeURIComponent(location.pathname+location.search);throw new Error('Sign in to continue.');}
    const result=await response.json();
    if(!response.ok){const error=new Error(result.error?.message||'Could not load this conversation.');error.status=response.status;throw error;}
    return result;
  }
  function controls(){
    $('send').disabled=state.sending||(!state.files.length&&!$('message').value.trim());
    $('message').disabled=state.sending; $('photo').disabled=state.sending; $('new-conversation').disabled=state.sending;
    $('conversations').disabled=state.sending;
    $('send').textContent=state.sending?'·':'↑';
  }
  function pick(id){
    state.binding=id||null;state.version='';state.attempt=null;state.files=[];renderFiles();
    $('messages').replaceChildren();$('activity').textContent='';$('floor-link').hidden=true;
    const url=new URL(location.href);if(id)url.searchParams.set('conversation',id);else url.searchParams.delete('conversation');history.replaceState(null,'',url);
    $('conversations').value=id||'';notice('');poll();
  }
  async function list(){
    const data=await api();$('owner').textContent=data.owner;state.maxBytes=data.max_image_bytes;
    $('conversations').replaceChildren(new Option('New conversation',''));
    data.conversations.forEach((item,i)=>$('conversations').add(new Option('Conversation '+(data.conversations.length-i)+' · '+new Date(item.created_at).toLocaleDateString(undefined,{month:'short',day:'numeric'}),item.id)));
    return data;
  }
  async function start(){const result=await api('/conversations',{});await list();pick(result.id);return result.id;}
  function render(data){
    $('floor-link').hidden=!data.session_id;
    if(data.session_id)$('floor-link').href='/calliope?session='+encodeURIComponent(data.session_id)+'&view=zen';
    $('activity').textContent=data.busy?'Callie is working…':data.queued?'Message queued…':'';
    $('activity').classList.toggle('busy',data.busy);
    const version=JSON.stringify(data.messages);if(version===state.version)return;state.version=version;
    const log=$('messages'),nearBottom=log.scrollHeight-log.scrollTop-log.clientHeight<85;
    const existing=new Map([...log.querySelectorAll('.message')].map(el=>[el.dataset.id,el]));
    if(data.messages.length)log.querySelector('.empty')?.remove();
    for(const m of data.messages){
      let row=existing.get(m.id);
      if(!row){
        row=document.createElement('article');row.className='message '+m.direction;row.dataset.id=m.id;
        const bubble=document.createElement('div');bubble.className='bubble';
        for(const url of m.images){const img=document.createElement('img');img.src=url;img.alt='Photo sent to Callie';img.addEventListener('load',()=>{if(nearBottom)log.scrollTop=log.scrollHeight;});img.addEventListener('click',()=>window.open(url,'_blank','noopener'));bubble.append(img);}
        if(m.body){const text=document.createElement('p');text.textContent=m.body;bubble.append(text);}
        const stamp=document.createElement('div');stamp.className='stamp';row.append(bubble,stamp);log.append(row);
      }
      row.classList.toggle('failed',m.state==='failed');row.classList.toggle('cancelled',m.state==='cancelled');
      const time=new Date(m.created_at).toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'});
      const status={queued:'Queued',delivering:'Delivering',delivered:'Delivered',failed:'Failed',cancelled:'Cancelled'}[m.state];
      row.querySelector('.stamp').textContent=time+(m.direction==='outgoing'?' · '+(m.last_error||status||m.state):'');
      existing.delete(m.id);
    }
    existing.forEach(el=>el.remove());
    if(!data.messages.length&&!log.children.length){const el=document.createElement('div');el.className='empty';const heading=document.createElement('h3');heading.textContent='Say hello to Callie.';const p=document.createElement('p');p.textContent='Send a text or attach a photo to begin.';el.append(heading,p);log.append(el);}
    if(nearBottom)log.scrollTop=log.scrollHeight;
  }
  async function poll(){
    if(!state.binding||state.loading)return;state.loading=true;const id=state.binding;
    try{const data=await api('/conversations/'+id);if(state.binding===id)render(data);}
    catch(error){if(state.binding===id)notice(error.message);}
    finally{state.loading=false;}
  }
  function renderFiles(){
    $('attachments').replaceChildren();$('attachments').hidden=!state.files.length;
    state.files.forEach((f,i)=>{const wrap=document.createElement('div');wrap.className='attachment';const img=document.createElement('img');img.src=f.data_url;img.alt=f.name;
      const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','Remove '+f.name);remove.addEventListener('click',()=>{if(!state.sending){state.files.splice(i,1);renderFiles();}});wrap.append(img,remove);$('attachments').append(wrap);});controls();
  }
  async function attach(files){
    if(state.sending)return;
    try{
      const additions=Array.from(files);if(additions.length+state.files.length>4)throw new Error('Attach up to four photos.');
      if(additions.some(f=>!['image/png','image/jpeg','image/webp','image/gif'].includes(f.type)))throw new Error('Choose JPEG, PNG, WebP or GIF photos.');
      if(additions.reduce((n,f)=>n+f.size,0)+state.files.reduce((n,f)=>n+f.size,0)>state.maxBytes)throw new Error('Photos can total up to '+Math.round(state.maxBytes/1024/1024)+' MB.');
      const loaded=await Promise.all(additions.map(f=>new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve({name:f.name,size:f.size,data_url:r.result});r.onerror=()=>reject(new Error('Could not read '+f.name));r.readAsDataURL(f);})));state.files.push(...loaded);renderFiles();notice('');
    }catch(error){notice(error.message);}
  }
  async function send(event){
    event.preventDefault();if(state.sending||(!$('message').value.trim()&&!state.files.length))return;
    state.sending=true;controls();notice('');
    try{
      // Start directly here: pick() intentionally clears attachments when the
      // operator switches conversations, so it is not used on first send.
      if(!state.binding){const created=await api('/conversations',{});state.binding=created.id;await list();$('conversations').value=state.binding;const url=new URL(location.href);url.searchParams.set('conversation',state.binding);history.replaceState(null,'',url);}
      const payload={message:$('message').value.trim(),attachments:state.files.map(({name,data_url})=>({name,data_url}))};
      const signature=JSON.stringify([state.binding,payload]);
      if(state.attempt?.signature!==signature)state.attempt={signature,id:crypto.randomUUID()};
      await api('/conversations/'+state.binding+'/messages',{...payload,client_id:state.attempt.id});
      state.attempt=null;$('message').value='';$('message').style.height='auto';state.files=[];renderFiles();await poll();$('messages').scrollTop=$('messages').scrollHeight;
    }catch(error){notice(error.message+(error.status?'':' Send again to retry this same message.'));}
    finally{state.sending=false;controls();$('message').focus();}
  }
  $('composer').addEventListener('submit',send);
  $('message').addEventListener('input',()=>{$('message').style.height='auto';$('message').style.height=Math.min(130,$('message').scrollHeight)+'px';controls();});
  $('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('composer').requestSubmit();}});
  $('photo').addEventListener('click',()=>$('files').click());$('files').addEventListener('change',()=>{attach($('files').files);$('files').value='';});
  $('new-conversation').addEventListener('click',()=>start().catch(error=>notice(error.message)));
  $('conversations').addEventListener('change',()=>pick($('conversations').value));
  $('record').addEventListener('click',()=>{$('record').setAttribute('aria-pressed',String(document.body.classList.toggle('recording')));});
  document.addEventListener('paste',event=>{const files=event.clipboardData?.files;if(files?.length){event.preventDefault();attach(files);}});
  const phone=document.querySelector('.phone');phone.addEventListener('dragover',event=>{event.preventDefault();phone.classList.add('dragging');});phone.addEventListener('dragleave',()=>phone.classList.remove('dragging'));phone.addEventListener('drop',event=>{event.preventDefault();phone.classList.remove('dragging');attach(event.dataTransfer.files);});
  function clock(){ $('clock').textContent=new Date().toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}); }clock();setInterval(clock,30000);
  controls();
  list().then(data=>{const requested=new URL(location.href).searchParams.get('conversation');const id=requested||data.conversations[0]?.id;if(id)pick(id);}).catch(error=>notice(error.message));
  setInterval(poll,2000);
})();
