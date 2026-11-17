/* An as-of view of recorded Stage work. Scrubbing never runs a query or a model.
   A host can hand the slider another sequence (an arrangement's revisions) with
   update(..., {source, describe, mark, now}); changing the source returns to Now. */
(() => {
  'use strict';
  const timestamp = (surface, turns=new Map()) => {
    const saved=Date.parse(surface.created_at);
    return Number.isFinite(saved)?saved:Date.parse(turns.get(String(surface.turn_id))?.created_at);
  };
  function stops(surfaces, turns=[]) {
    const byTurn=new Map(turns.map(t=>[String(t.id),t])),groups=new Map();
    for(const s of surfaces){const at=timestamp(s,byTurn);if(!Number.isFinite(at))continue;
      if(!groups.has(at))groups.set(at,{at,items:[],turn:byTurn.get(String(s.turn_id))});
      groups.get(at).items.push(s);
    }
    return [...groups.values()].sort((a,b)=>a.at-b.at);
  }
  function create({root,onChange}) {
    const range=root.querySelector('input'),toggle=root.querySelector('[data-stage-time-toggle]'),
      now=root.querySelector('[data-stage-time-now]'),detail=root.querySelector('[data-stage-time-detail]'),
      ticks=root.querySelector('[data-stage-time-ticks]'),state=root.querySelector('[data-stage-time-state]');
    let session='',items=[],turns=new Map(),events=[],cutoff=null,hover=null,signature='';
    let source='stage',describe=null,mark=e=>e.items.some(s=>s.kind==='artifact'),nowText='Now · all saved work';
    const index=()=>cutoff===null?events.length:Math.max(0,events.findLastIndex(e=>e.at<=cutoff));
    function description(i){
      const event=events[i];if(!event)return nowText;
      if(describe){const text=describe(event,i,events);if(text)return text;}
      const published=event.items.find(s=>s.kind==='artifact');
      return [event.turn?.ordinal!=null?`Turn ${event.turn.ordinal}`:null,
        new Date(event.at).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'}),
        published?`${published.title||'Dashboard'} · v${published.artifact_version}`:event.items.length>1?`${event.items.length} saved items`:event.items[0].title||'Saved work'].filter(Boolean).join(' · ');
    }
    function paint(){
      const i=index(),earlier=cutoff!==null;
      range.max=String(events.length);range.value=String(i);range.disabled=!events.length;
      range.setAttribute('aria-valuetext',earlier?`Viewing earlier work · ${description(i)}`:nowText);
      root.style.setProperty('--stage-time-progress',`${events.length?100*i/events.length:100}%`);
      root.dataset.earlier=String(earlier);state.hidden=false;state.textContent=earlier?'Earlier':'History';now.disabled=!earlier;
      now.textContent='Now';toggle.setAttribute('aria-label',earlier?'History: viewing earlier work':'Browse Stage history');
      const newer=earlier?items.filter(s=>timestamp(s,turns)>cutoff).length:0;
      detail.textContent=[hover===null&&earlier?'Viewing earlier work':null,description(hover??i),hover===null&&newer?`${newer} newer items · return to Now`:null].filter(Boolean).join(' · ');
    }
    function change(value){cutoff=value;hover=null;paint();onChange();}
    range.addEventListener('input',()=>change(events[Number(range.value)]?.at??null));
    range.addEventListener('pointermove',e=>{if(e.buttons)return;const r=range.getBoundingClientRect();hover=Math.round(Math.max(0,Math.min(1,(e.clientX-r.left)/r.width))*events.length);paint();});
    range.addEventListener('pointerleave',()=>{hover=null;paint();});
    range.addEventListener('focus',()=>{hover=null;paint();});
    now.addEventListener('click',()=>change(null));
    toggle.addEventListener('click',()=>{const open=root.classList.toggle('is-open');toggle.setAttribute('aria-expanded',String(open));if(open)range.focus();});
    root.addEventListener('keydown',e=>{if(e.key==='Escape'){root.classList.remove('is-open');toggle.setAttribute('aria-expanded','false');toggle.focus();}});
    document.addEventListener('pointerdown',e=>{if(!root.contains(e.target)){root.classList.remove('is-open');toggle.setAttribute('aria-expanded','false');}});
    return {
      update(sid,surfaces,allTurns,options={}){
        sid=String(sid||'');const nextSource=String(options.source||'stage');
        if(sid!==session||nextSource!==source){session=sid;source=nextSource;cutoff=null;hover=null;signature='';root.classList.remove('is-open');toggle.setAttribute('aria-expanded','false');}
        describe=options.describe||null;nowText=options.now||'Now · all saved work';
        mark=options.mark||(e=>e.items.some(s=>s.kind==='artifact'));
        items=surfaces;turns=new Map(allTurns.map(t=>[String(t.id),t]));events=stops(items,allTurns);
        root.hidden=events.length<2&&cutoff===null;
        const key=source+'|'+events.map(e=>`${e.at}:${Boolean(mark(e))}`).join('|');
        if(key!==signature){signature=key;ticks.replaceChildren(...events.filter((e,i)=>mark(e)||i%Math.max(1,Math.ceil(events.length/60))===0).map(e=>{
          const tick=document.createElement('i');tick.style.left=`${100*events.indexOf(e)/events.length}%`;tick.className=mark(e)?'is-publication':'';return tick;
        }));}
        paint();
      },
      reset(){if(cutoff!==null)change(null);},
      get source(){return source;},
      get cutoff(){return cutoff;},
      get active(){return cutoff!==null;},
      get items(){return cutoff===null?items:items.filter(s=>timestamp(s,turns)<=cutoff);},
    };
  }
  globalThis.CalliopeStageTimeline={create,stops,timestamp};
})();
