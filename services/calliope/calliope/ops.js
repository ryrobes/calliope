(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let data = null, generation = 0, opener = null;
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const date = value => value ? new Date(value).toLocaleDateString(undefined, {month:'short', day:'numeric'}) : 'Date unavailable';
  const summaryNote = item => !item.synopsis ? 'No summary has been recorded yet.' : item.summary_is_current ? 'Recorded conversation summary' : 'Summary covers earlier work; open the conversation for the latest.';

  function detail(item, button) {
    opener = button;
    $('ops-detail-title').textContent = item.title;
    const body = $('ops-detail-body'); body.replaceChildren();
    body.append(el('p', item.synopsis || 'Open the conversation to inspect the work so far.'));
    body.append(el('p', `${summaryNote(item)}${item.synopsis_at ? ' · Updated ' + date(item.synopsis_at) : ''}`, 'ops-summary-note'));
    for (const section of [{title:`Saved results · ${item.result_count}`, items:item.results, total:item.result_count}, {title:`Open questions · ${item.open_requests}`, items:item.requests, total:item.open_requests}]) {
      body.append(el('h3', section.title));
      const list = el('ul');
      section.items.forEach(record => {
        const li=el('li'); li.append(el('span',record.title || 'Request for input'),el('small',record.kind || 'Input needed')); list.append(li);
      });
      body.append(list);
      if (!section.total) body.append(el('p','None recorded.','ops-summary-note'));
      if (section.total > section.items.length) body.append(el('p',`Showing the latest ${section.items.length}. The full record is in the conversation.`, 'ops-summary-note'));
    }
    body.append(el('p','Saved results count each result’s latest version, including drafts and images. They do not establish a completed business outcome.','ops-summary-note'));
    const link=el('a','Open the conversation and evidence →','ops-open');
    link.href='/calliope?session='+encodeURIComponent(item.id)+'&view=zen'; body.append(link);
    $('ops-detail').showModal(); $('ops-close').focus();
  }

  function render() {
    if (!data) return;
    const words = $('ops-search').value.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    const attention = $('ops-attention').checked;
    const items=data.observations.filter(item => (!attention || item.open_requests > 0) && words.every(word => `${item.title} ${item.synopsis || ''} ${item.owner_email}`.toLocaleLowerCase().includes(word)));
    const cards=$('ops-cards');cards.replaceChildren();
    items.forEach(item => {
      const card=el('article',null,'ops-card'),head=el('header');
      head.append(el('span',item.active ? 'In progress' : 'Work observed'),el('span',date(item.updated_at))); card.append(head);
      card.append(el('h2',item.title),el('p',item.synopsis || 'The work has a record; its conversation summary is not available yet.','ops-summary'));
      card.append(el('p',summaryNote(item),'ops-note'));
      const footer=el('footer'); footer.append(el('span',`${item.result_count} saved results`));
      if (item.open_requests) footer.append(el('span',`${item.open_requests} open questions`,'ops-wait'));
      const button=el('button','See evidence');button.type='button';button.setAttribute('aria-label',`See evidence: ${item.title}`);button.addEventListener('click',()=>detail(item,button));footer.append(button);card.append(footer);cards.append(card);
    });
    $('ops-status').textContent=items.length ? `${items.length} ${items.length===1?'observation':'observations'}${items.length!==data.observations.length?' match these filters':''}.` : data.observations.length ? 'No work matches these filters.' : 'No conversations with work recorded in this period. Ordinary conversations will appear here as well as assigned work.';
  }

  async function load() {
    const current=++generation;
    // Clear prior-period counts as well as cards so failures cannot look like zero work.
    data=null;$('ops-cards').replaceChildren();$('ops-cards').setAttribute('aria-busy','true');$('ops-totals').hidden=true;
    $('ops-status').textContent='Loading the work record…';$('ops-refresh').disabled=true;
    try {
      const response=await fetch('/api/calliope/ops?days='+encodeURIComponent($('ops-period').value), {headers:{accept:'application/json'},cache:'no-store'});
      if(!response.ok) throw new Error(response.status===403?'Ops is available to administrators.':response.status===401?'Your session has ended. Sign in again to view Ops.':'The work record could not be loaded. Please try Refresh.');
      const result=await response.json();if(current!==generation)return;data=result;
      const totals=$('ops-totals');totals.replaceChildren();
      for(const [key,label] of [['observations','conversations'],['results','saved results'],['open_requests','open questions']]){const metric=el('div',null,'ops-total');metric.append(el('strong',data.totals[key].toLocaleString()),el('span',label));totals.append(metric);}
      totals.hidden=false;
      $('ops-scope').textContent=`${data.scope} · Active in the last ${data.period_days} days${data.has_more?` · Showing the latest ${data.limit}`:''}. Counts cover the listed conversations’ full histories. Refreshed ${new Date(data.as_of).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}.`;
      render();
    } catch(error) { if(current===generation){$('ops-status').textContent=error.message;$('ops-scope').textContent='The record is unavailable; no counts are being reported.';} }
    finally {if(current===generation){$('ops-refresh').disabled=false;$('ops-cards').setAttribute('aria-busy','false');}}
  }
  $('ops-refresh').addEventListener('click',load);$('ops-period').addEventListener('change',load);
  $('ops-search').addEventListener('input',render);$('ops-attention').addEventListener('change',render);
  $('ops-close').addEventListener('click',()=>$('ops-detail').close());
  $('ops-detail').addEventListener('click',event=>{if(event.target===$('ops-detail')){const rect=event.target.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)event.target.close();}});
  $('ops-detail').addEventListener('close',()=>opener?.focus());
  load();
})();
