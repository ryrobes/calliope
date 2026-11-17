/* Reviewed email is a native Stage surface. Local edit state survives Stage repacks. */
(() => {
  'use strict';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const views = new Map(), batches = new Map();
  let options = {}, connected = false, polling = false, recipientFrame = 0;
  const editable = new Set(['draft','held','failed','preparing']);
  const labels = {preparing:'Preparing',draft:'Ready to review',held:'Held',queued:'Queued',sending:'Sending',sent:'Sent',failed:'Needs attention',uncertain:'Check send status'};
  function view(id) {
    if (!views.has(id)) views.set(id, {index:0,edit:null,about:null,contextDrafts:{},busy:false,showReplies:false});
    return views.get(id);
  }
  async function request(path, body) {
    const response = await fetch('/api/calliope/mail/' + path, {method:body === undefined ? 'GET':'POST',
      credentials:'same-origin',headers:{'Content-Type':'application/json','X-Calliope-Mail':'1'},body:body === undefined ? undefined : JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || 'This email action could not be completed.');
    return result;
  }
  function apply(batch) {
    batches.set(batch.id,batch);
    options.update?.(batch);
  }
  function button(action, text, extra='') {return `<button type="button" data-mail-action="${action}" ${extra}>${text}</button>`;}
  function attachments(items, kind, id) {
    return items.map((item,i)=>`<a class="mail-attachment" target="_blank" rel="noopener" href="/api/calliope/mail/attachments/${kind}/${esc(id)}/${i}"><span aria-hidden="true">↗</span> ${esc(item.name)} <small>${Math.ceil(item.size/1024)} KB</small></a>`).join('');
  }
  function revealSelectedRecipients() {
    if (recipientFrame) return;
    recipientFrame = requestAnimationFrame(() => {
      recipientFrame = 0;
      document.querySelectorAll('.mail-recipients').forEach(rail => {
        const selected = rail.querySelector('[aria-current="true"]');
        if (!selected || !rail.clientWidth) return;
        const left = selected.offsetLeft, right = left + selected.offsetWidth;
        if (left < rail.scrollLeft) rail.scrollLeft = left;
        else if (right > rail.scrollLeft + rail.clientWidth) rail.scrollLeft = right - rail.clientWidth;
      });
    });
  }
  function render(surface) {
    const fresh = surface.payload || {}, cached = batches.get(fresh.id);
    // The server is authoritative. Polls write through to the normal surface payload.
    const b = fresh.drafts ? fresh : cached;
    if (!b?.id) return '<div class="chart-empty">Preparing correspondence…</div>';
    batches.set(b.id,b);
    const v=view(b.id), drafts=b.drafts || [], own=options.owner?.() === b.owner_email;
    v.index=Math.max(0,Math.min(v.index,drafts.length-1));
    const d=drafts[v.index], ready=drafts.filter(d=>d.status==='draft'), sent=drafts.filter(d=>d.status==='sent').length;
    const incoming=(b.messages || []).filter(m=>m.direction==='incoming');
    const person=d?.context?.person || {}, editing=v.edit?.id === d?.id, canEdit=own && d && editable.has(d.status);
    const disabled=v.busy?'disabled':'';
    let body='';
    if (v.showReplies) {
      body=`<div class="mail-replies">${incoming.length ? incoming.map(m=>`<section><header><strong>${esc(m.sender)}</strong><time>${esc(new Date(m.received_at).toLocaleString())}</time></header><h3>${esc(m.subject)}</h3><div class="mail-copy">${esc(m.body)}</div><div class="mail-attachments">${attachments(m.attachments || [],'message',m.id)}</div>${own ? button('reply','Ask Callie to draft a reply',`data-message="${esc(m.id)}"`) : ''}</section>`).join(''):'<p class="mail-muted">Replies to these emails will appear here.</p>'}</div>`;
    } else if (d) {
      body=`<div class="mail-stack"><nav class="mail-recipients" aria-label="Draft recipients">${drafts.map((x,i)=>button('select',`<span>${esc(x.context?.person?.name || x.recipient)}</span><i class="mail-state-${esc(x.status)}" title="${esc(labels[x.status])}"></i>`,`data-index="${i}" aria-current="${i===v.index ? 'true':'false'}" title="${esc(x.context?.person?.name || x.recipient)}"`)).join('')}</nav>
      <article class="mail-paper" aria-label="Email preview">
        <header class="mail-paper-heading"><span class="mail-paper-kind"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/></svg>${editable.has(d.status)?'Draft email':'Email'}</span><span class="mail-paper-status">${esc(labels[d.status])}</span></header>
        <div class="mail-envelope">
          <div class="mail-envelope-row"><span class="mail-field-name">From</span><span class="mail-address">${esc(b.mailbox?.address || 'Sender mailbox not connected')}</span></div>
          <div class="mail-envelope-row"><span class="mail-field-name">To</span><div class="mail-addressee">${button('about',esc(person.name || d.recipient),`aria-expanded="${!!v.about}" title="View recipient context"`)}${person.name ? `<span class="mail-address">&lt;${esc(d.recipient)}&gt;</span>` : ''}</div></div>
          ${editing ? `<label class="mail-envelope-row mail-edit-label"><span class="mail-field-name">Subject</span><input data-mail-subject value="${esc(v.edit.subject)}" maxlength="300"></label>` : `<div class="mail-envelope-row"><span class="mail-field-name">Subject</span><h3 class="mail-subject">${esc(d.subject || 'Callie is preparing this email')}</h3></div>`}
        </div>
        <div class="mail-message">
          ${editing ? `<label class="mail-edit-label"><span class="mail-field-name">Message</span><textarea class="mail-editor" data-mail-body>${esc(v.edit.body)}</textarea></label>` : `<div class="mail-copy ${d.status==='preparing'?'mail-preparing':''}">${esc(d.body || 'The recipient context is ready. The draft will appear here as Callie finishes composing.')}</div>`}
          ${(d.attachments || []).length ? `<div class="mail-attachments">${attachments(d.attachments,'draft',d.id)}</div>` : ''}
        </div>
      </article></div>
      ${v.about ? `<aside class="mail-about"><strong>${esc([person.role,person.organization].filter(Boolean).join(' · ') || 'Recipient context')}</strong><p>${esc(person.summary || 'No relationship summary yet. Context grows from sent and received correspondence.')}</p><p>${esc(person.guidance || '')}</p><div>${(person.channels || []).map(c=>`<span>${esc(c.channel)} · ${esc(c.address)}</span>`).join('')}</div>${(d.context.notes || []).slice(0,3).map(n=>`<p>${esc(n.body)}</p>`).join('')}${own ? `<label>Correct or add context<textarea data-mail-context placeholder="For example: Joe now handles returns for Lakeside.">${esc(v.contextDrafts[d.contact_id] || '')}</textarea></label>${button('note','Save context',disabled)}`:''}</aside>`:''}
      ${(d.evidence_refs || []).length ? `<div class="mail-sources"><span>Context for review · not included in email</span>${d.evidence_refs.map(r=>button('source',esc(r.title),`data-source="${esc(r.surface_id)}"`)).join('')}</div>`:''}
      ${d.last_error ? `<p class="mail-error" role="status">${esc(d.last_error)}</p>`:''}
      <div class="mail-review-actions">${editing ? `${button('cancel-edit','Cancel')}${button('save','Save changes',disabled)}`:
        `${canEdit ? button('edit','Edit',disabled)+button('revise','Ask Callie to revise',disabled):''}${canEdit && d.status!=='preparing' ? button('hold',d.status==='held'?'Resume':'Hold',disabled):''}${own && ['draft','failed'].includes(d.status) ? button('send','Send email',`${disabled} class="mail-primary" ${!b.mailbox?.enabled?'disabled':''}`):''}`}</div>`;
    }
    revealSelectedRecipients();
    return `<section class="mail-review" data-mail-batch="${esc(b.id)}"><header class="mail-summary"><span>${drafts.length} email${drafts.length===1?'':'s'} · ${sent} sent${drafts.some(x=>x.status==='held')?' · '+drafts.filter(x=>x.status==='held').length+' held':''}</span><div>${button('drafts','Drafts',`aria-pressed="${!v.showReplies}"`)}${button('replies',`Replies${incoming.length?' · '+incoming.length:''}`,`aria-pressed="${v.showReplies}"`)}</div></header>${body}
      <footer class="mail-footer"><nav aria-label="Email pagination">${button('prev','‹',`aria-label="Previous email" ${v.index===0||editing?'disabled':''}`)}<span>${drafts.length?v.index+1:0} / ${drafts.length}</span>${button('next','›',`aria-label="Next email" ${v.index>=drafts.length-1||editing?'disabled':''}`)}</nav>
      ${own ? !b.mailbox?.enabled ? button('connect','Connect sender mailbox') : `${button('connect','Sender settings',`class="mail-sender" title="Manage ${esc(b.mailbox.address)}"`)}${ready.length>1 ? button('all',`Send all ${ready.length} drafts`,`${disabled} ${editing?'disabled':''} class="mail-primary"`):''}`:''}</footer>${own && b.mailbox?.error ? `<p class="mail-error" role="status">${esc(b.mailbox.error)} Use the sender button to reconnect.</p>`:''}</section>`;
  }
  function redraw() {options.repaint?.();}
  function configure(address='') {
    let dialog=document.querySelector('#mail-connection-dialog');
    if (!dialog) {
      dialog=document.createElement('dialog');dialog.id='mail-connection-dialog';dialog.className='mail-connection-dialog';
      dialog.innerHTML=`<form><header><h2>Connect your sender mailbox</h2><button type="button" data-close aria-label="Close">×</button></header><p>Callie prepares drafts. You choose when to send. New incoming mail is collected from the time you connect.</p><label>Email address<input name="address" type="email" autocomplete="username" required placeholder="you@example.com"></label><label>App password<input name="password" type="password" autocomplete="new-password" required></label><div class="mail-server-fields"><label>IMAP server<input name="imap_host" value="imap.gmail.com" required></label><label>SMTP server<input name="smtp_host" value="smtp.gmail.com" required></label></div><p class="mail-muted">For Gmail, use an app password. The password is saved in Calliope’s credential store and is never sent to the model.</p><p data-status role="status"></p><footer><button type="button" data-close>Cancel</button><button type="submit" class="mail-primary">Test and connect</button></footer></form>`;
      document.body.append(dialog);
      dialog.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>{dialog.querySelector('[name=password]').value='';dialog.close();});
      dialog.addEventListener('cancel',()=>dialog.querySelector('[name=password]').value='');
      dialog.addEventListener('close',()=>dialog.querySelector('[name=password]').value='');
      dialog.querySelector('form').onsubmit=async event=>{
        event.preventDefault();const form=event.target,submit=form.querySelector('[type=submit]'),status=form.querySelector('[data-status]');
        submit.disabled=true;status.textContent='Checking IMAP and SMTP…';
        try {await request('connection',Object.fromEntries(new FormData(form)));dialog.close();options.toast?.('Sender mailbox connected');await poll();}
        catch(error){status.textContent=error.message;}
        finally {submit.disabled=false;form.querySelector('[name=password]').value='';}
      };
    }
    if(address)dialog.querySelector('[name=address]').value=address;
    dialog.showModal();
  }
  async function click(event) {
    const button=event.target.closest('[data-mail-action]');if(!button)return;
    event.stopPropagation();
    const root=button.closest('[data-mail-batch]'),b=batches.get(root?.dataset.mailBatch);if(!b)return;
    const v=view(b.id),d=b.drafts[v.index],action=button.dataset.mailAction;
    if (v.busy)return;
    if (['next','prev','select','drafts','replies'].includes(action) && v.edit) {options.toast?.('Save or cancel your edits before switching emails.');return;}
    if(action==='connect'){configure(b.mailbox?.address);return;}
    if(action==='next'||action==='prev'){v.index+=action==='next'?1:-1;redraw();return;}
    if(action==='select'){v.index=Number(button.dataset.index);v.about=null;redraw();return;}
    if(action==='about'){v.about=!v.about;redraw();return;}
    if(action==='drafts'||action==='replies'){v.showReplies=action==='replies';redraw();return;}
    if(action==='edit'){v.edit={id:d.id,revision:d.revision,subject:d.subject,body:d.body};redraw();return;}
    if(action==='cancel-edit'){v.edit=null;redraw();return;}
    if(action==='source'){options.source?.(button.dataset.source);return;}
    if(action==='revise'||action==='reply'){
      options.ask?.(action==='reply'?`Read incoming email ${button.dataset.message} and draft a response for my review, using the recipient context and relevant current data.`:
        `Please revise draft ${d.id} in email batch ${b.id}. Read the current batch and recipient context first. My changes: `);
      return;
    }
    v.busy=true;button.disabled=true;
    try {
      if(action==='save') {
        const result=await request(`batches/${b.id}/drafts/${d.id}`,{revision:v.edit.revision,subject:v.edit.subject,body:v.edit.body});
        v.edit=null;apply(result);options.toast?.('Email changes saved');
      } else if(action==='hold') {
        apply(await request(`batches/${b.id}/drafts/${d.id}`,{revision:d.revision,held:d.status!=='held'}));
      } else if(action==='send'||action==='all') {
        const drafts=(action==='send'?[d]:b.drafts.filter(x=>x.status==='draft')).map(x=>({id:x.id,revision:x.revision}));
        apply(await request(`batches/${b.id}/send`,{drafts}));options.toast?.(`${drafts.length===1?'Email':drafts.length+' emails'} queued to send`);
      } else if(action==='note') {
        const note=root.querySelector('[data-mail-context]').value;
        if(!note.trim())return;
        await request('contacts',{id:d.contact_id,note});
        delete v.contextDrafts[d.contact_id];
        options.toast?.('Recipient context saved. Ask Callie to revise this draft to use the update.');v.about=null;
      }
    }catch(error){options.toast?.(error.message,true);}
    finally{v.busy=false;redraw();}
  }
  async function poll() {
    if(polling||document.hidden)return;
    const ids=[...new Set([...document.querySelectorAll('[data-mail-batch]')].map(n=>n.dataset.mailBatch))];
    if(!ids.length)return;
    polling=true;
    try {
      await Promise.all(ids.map(async id=>{
        const v=view(id);if(v.busy||v.edit||v.about)return;
        try {const fresh=await request('batches/'+id);if(JSON.stringify(fresh)!==JSON.stringify(batches.get(id)))apply(fresh);}catch{}
      }));
    }finally{polling=false;}
  }
  function init(nextOptions) {
    options=nextOptions;
    if(connected)return;connected=true;
    document.addEventListener('click',click);
    document.addEventListener('input',event=>{
      const root=event.target.closest('[data-mail-batch]');if(!root)return;
      const v=view(root.dataset.mailBatch);
      if(event.target.matches('[data-mail-context]')){
        const d=batches.get(root.dataset.mailBatch)?.drafts[v.index];
        if(d)v.contextDrafts[d.contact_id]=event.target.value;
      }
      if(!v.edit)return;
      if(event.target.matches('[data-mail-subject]'))v.edit.subject=event.target.value;
      if(event.target.matches('[data-mail-body]'))v.edit.body=event.target.value;
    });
    setInterval(poll,4000);
  }
  window.CalliopeCorrespondence={render,init,configure,poll};
})();
