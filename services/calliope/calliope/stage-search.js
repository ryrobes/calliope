/* One read-only search/filter shared by the chronological and spatial Stage. */
(() => {
  'use strict';
  const clean = (v, n=600) => String(v ?? '').replace(/\s+/g, ' ').trim().slice(0,n);
  const fold = v => clean(v,10000).normalize('NFKD').replace(/\p{M}/gu,'').toLocaleLowerCase();
  const labels = {metric:'Metrics',cube:'Cubes',query:'Queries',artifact:'Artifacts',image:'Images',sketch:'Sketches',answer:'Answers',
    evidence:'Evidence',chart:'Charts',map:'Maps',document:'Documents',board:'Boards',action:'Actions',
    workflow:'Workflows',decision:'Decisions',input:'Inputs',playbook:'Playbooks'};
  function searchable(s) {
    const src=s.source||{},p=s.payload||{},area=src.meaning_area||{};
    return fold([s.title,s.kind,s.artifact_slug,s.artifact_version,s.created_at,src.actor,src.specialist_key,
      src.question,src.exploration?.intent,area.name,area.key,p.summary,p.description,p.subtitle,
      clean(p.answer,1000),src.sql,src.args?.sql,...(s.spatial?.tables||[]),...(s.spatial?.columns||[]),
      ...(Array.isArray(p.columns)?p.columns:[]).map(c=>typeof c==='object'?c.name:c)].filter(v=>v!=null).join(' '));
  }
  function create({rail,onChange,search}) {
    const input=rail.querySelector('input'),form=rail.querySelector('form'),kindSelect=rail.querySelector('select'),
      clearButton=rail.querySelector('[data-stage-search-clear]'),submit=rail.querySelector('[type=submit]'),
      status=rail.querySelector('#stage-search-status');
    let session='',surfaces=[],query='',kind='',matches=new Set(),texts=new Map(),scores=new Map(),
      judged=new Map(),phase='idle',partial=false,checked=0,total=0,request=null,generation=0,revision=0;
    const active=()=>Boolean(query.trim()||kind);
    function invalidate(){generation++;request?.abort();request=null;phase='idle';scores.clear();judged.clear();}
    function compute(){
      const words=fold(query).split(/\s+/).filter(Boolean);
      const next=new Set(surfaces.filter(s=>(!kind||s.kind===kind)&&(!words.length
        || words.every(word=>texts.get(String(s.id)).includes(word))
        || (scores.has(String(s.id))&&judged.get(String(s.id))===texts.get(String(s.id)))))
        .map(s=>String(s.id)));
      if(next.size!==matches.size||[...next].some(id=>!matches.has(id)))revision++;
      matches=next;paint();
    }
    function paint(){
      const busy=phase==='loading',hasQuery=Boolean(query.trim());
      submit.disabled=busy||!hasQuery;submit.title=busy?'Finding related work…':'Find related work (Enter)';
      form.setAttribute('aria-busy',String(busy));clearButton.hidden=!active();
      const filter=kindSelect.closest('.stage-kind-filter');if(filter){filter.dataset.active=String(Boolean(kind));filter.querySelector('summary').setAttribute('aria-label',kind?`Filter Stage by type: ${labels[kind]||kind}`:'Filter Stage by type');}
      status.hidden=!active();
      const stale=phase==='done'&&surfaces.some(s=>judged.get(String(s.id))!==texts.get(String(s.id)));
      const detail=busy?'Finding related work…':phase==='error'?'Related search unavailable · text matches only'
        :phase==='done'?(partial?`Text + related · checked ${checked} of ${total}`:stale?'New work · Enter to search again':'Text + related matches')
        :hasQuery?'Text matches · Enter to find related work':'All saved versions';
      status.textContent=`${matches.size} of ${surfaces.length} · ${detail}`;
    }
    function changed(){revision++;compute();onChange();}
    function clear(){invalidate();input.value=query='';kindSelect.value=kind='';changed();}
    input.addEventListener('input',()=>{invalidate();query=input.value;changed();});
    input.addEventListener('keydown',e=>{if(e.key==='Escape'&&active()){e.preventDefault();clear();}});
    kindSelect.addEventListener('change',()=>{kind=kindSelect.value;kindSelect.closest('details')?.removeAttribute('open');changed();});
    document.addEventListener('pointerdown',e=>{const filter=kindSelect.closest('details');if(filter&&!filter.contains(e.target))filter.removeAttribute('open');});
    kindSelect.addEventListener('keydown',e=>{if(e.key==='Escape'){e.stopPropagation();const filter=kindSelect.closest('details');filter?.removeAttribute('open');filter?.querySelector('summary')?.focus();}});
    clearButton.addEventListener('click',()=>{clear();input.focus();});
    form.addEventListener('submit',async e=>{
      e.preventDefault();if(!query.trim()||phase==='loading'||!session)return;
      invalidate();const token=generation,sid=session,q=query.trim(),snapshot=new Map(texts);
      request=new AbortController();phase='loading';compute();onChange();
      try {
        const result=await search(sid,q,request.signal);
        if(token!==generation||sid!==session)return;
        if(!Array.isArray(result.matches))throw new Error('Invalid search response');
        scores=new Map(result.matches.filter(m=>snapshot.has(String(m.id))&&Number.isFinite(m.score)).map(m=>[String(m.id),m.score]));
        judged=snapshot;partial=Boolean(result.partial);checked=result.checked;total=result.total;
        phase=checked===0&&total>0?'error':'done';
      } catch(error) {if(token!==generation||sid!==session)return;phase='error';}
      request=null;changed();
    });
    return {
      update(sid,items){
        sid=String(sid||'');
        if(sid!==session){invalidate();session=sid;query=kind=input.value=kindSelect.value='';revision++;}
        rail.hidden=!session;surfaces=items;texts=new Map(items.map(s=>[String(s.id),searchable(s)]));
        const kinds=[...new Set(items.map(s=>s.kind))].sort((a,b)=>(labels[a]||a).localeCompare(labels[b]||b));
        const key=kinds.join('|');
        if(kindSelect.dataset.kinds!==key){
          kindSelect.replaceChildren(new Option('All types',''),...kinds.map(k=>new Option(labels[k]||k.replaceAll('_',' '),k)));
          kindSelect.dataset.kinds=key;if(!kinds.includes(kind))kind='';kindSelect.value=kind;
        }
        compute();
      },
      clear,
      get active(){return active();},
      get ids(){return matches;},
      get key(){return active()?`${session}:${revision}`:'';},
      results(){return surfaces.filter(s=>matches.has(String(s.id))).sort((a,b)=>
        (scores.get(String(b.id))||0)-(scores.get(String(a.id))||0)
        || (Date.parse(b.created_at)||0)-(Date.parse(a.created_at)||0));},
    };
  }
  globalThis.CalliopeStageSearch={create,searchable};
})();
