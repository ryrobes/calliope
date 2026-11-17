/* Calliope Dashboard 1.0.0 — immutable hosted widget runtime. */
(() => {
  'use strict';
  if (window.rvbbitDashboard) return;
  const instances = new Map();
  const css = document.createElement('style');
  css.textContent = `
  .rvd-box{min-width:0;max-width:100%;color:var(--artifact-text,#edf2f4);font-family:var(--artifact-font-body,inherit)}
  .rvd-box *{box-sizing:border-box}.rvd-box button,.rvd-box select,.rvd-box input{font:inherit;color:inherit;background:var(--artifact-panel,#17232b);border:1px solid var(--artifact-border,#35434a);padding:6px 9px;border-radius:4px}
  .rvd-box button{cursor:pointer}.rvd-box button:disabled{opacity:.45;cursor:default}.rvd-box :focus-visible{outline:2px solid var(--artifact-accent,#64bac3);outline-offset:2px}
  .rvd-value{font-size:clamp(24px,3vw,40px);font-weight:700;font-variant-numeric:tabular-nums}.rvd-note,.rvd-status{color:var(--artifact-muted,#a0afb6);font-size:.85em;margin:8px 0}.rvd-status:empty{display:none}
  .rvd-scroll{max-width:100%;overflow:auto;position:relative;overscroll-behavior:contain}.rvd-table{width:100%;border-collapse:collapse;font-size:14px;text-align:left;font-variant-numeric:tabular-nums}.rvd-table th,.rvd-table td{padding:9px 12px;border-bottom:1px solid var(--artifact-border,#35434a);overflow-wrap:anywhere}.rvd-table th{position:sticky;top:0;z-index:1;background:var(--artifact-panel,#17232b)}.rvd-table th button{border:0;background:transparent;padding:0;text-align:left;font-weight:700}.rvd-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}.rvd-chart{width:100%;min-width:0}.rvd-box[data-state="stale"]{opacity:.75}.rvd-box[data-state="error"] .rvd-status{font-weight:700}
  `;
  document.head.append(css);
  const node = (tag, text, cls) => { const n=document.createElement(tag); if(text!=null)n.textContent=String(text); if(cls)n.className=cls; return n; };
  const fmt=(v,f)=>v==null?'—':typeof f==='function'?String(f(v)):f==='percent'?new Intl.NumberFormat(undefined,{style:'percent',maximumFractionDigits:1}).format(v):f==='currency'?new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:0}).format(v):f==='number'?new Intl.NumberFormat(undefined,{maximumFractionDigits:2}).format(v):String(v);
  function base(kind, options, render) {
    let opts={...options}; const el=typeof opts.el==='string'?document.querySelector(opts.el):opts.el;
    if(!el)throw Error(`${kind}: destination element is missing`);
    const id=opts.id || el.id; if(!id)throw Error(`${kind}: provide a stable id`);
    instances.get(id)?.destroy();
    el.classList.add('rvd-box'); el.dataset.dashboardWidget=kind; el.dataset.dashboardId=id;
    let cleanup=()=>{}, destroyed=false;
    const handle={el,id,kind,options:()=>opts,state:{},update(patch={}){if(destroyed)return;opts={...opts,...patch};cleanup();el.replaceChildren();cleanup=render(el,opts,handle)||(()=>{});},destroy(){if(destroyed)return;destroyed=true;cleanup();el.replaceChildren();delete el.dataset.dashboardWidget;delete el.dataset.dashboardId;instances.delete(id);}};
    instances.set(id,handle);
    try{handle.update();}catch(e){handle.destroy();throw e;}
    return handle;
  }
  function status(el,o) {el.dataset.state=o.state||'ready';el.setAttribute('aria-busy',String(o.state==='loading'));const text=o.error||o.note||(o.state==='loading'?'Updating…':o.state==='stale'?'Showing previous results.':'');el.append(node('p',text,'rvd-status'));}
  function kpi(o){return base('kpi',o,(el,p)=>{el.append(node('div',p.label),node('div',fmt(p.value,p.format),'rvd-value'));status(el,p);window.RVBBIT_DASHBOARD?.bindSemanticObject?.(p.id,el,{value:p.value,context:p.context||{}});});}
  function table(o){return base('table',o,(el,p,h)=>{
    const T=window.RVBBIT_TABLE;if(!T)throw Error(`table #${h.id}: hosted table runtime is missing`);
    let rows=p.rows||[];const columns=p.columns||Object.keys(rows[0]||{}).map(key=>({key,label:key}));
    if(p.filter)rows=rows.filter(r=>Object.values(r).some(v=>String(v??'').toLowerCase().includes(String(p.filter).toLowerCase())));
    h.state.sort=p.sort===false?[]:h.state.sort??p.sort??[];
    h.state.page=h.state.page??0;const size=Math.max(1,Number(p.page?.size||p.page||25));h.state.page=Math.min(h.state.page,Math.max(0,Math.ceil(rows.length/size)-1));
    const features={coreReactivityFeature:T.storeReactivityBindings(),rowSortingFeature:T.rowSortingFeature,columnVisibilityFeature:T.columnVisibilityFeature,coreRowModel:T.createCoreRowModel(),sortedRowModel:T.createSortedRowModel()};
    if(p.page){features.rowPaginationFeature=T.rowPaginationFeature;features.paginatedRowModel=T.createPaginatedRowModel();}
    const engine=T.constructTable({features:T.tableFeatures(features),data:rows,columns:columns.map(c=>({id:c.key,accessorKey:c.key,header:c.label||c.key})),initialState:{sorting:h.state.sort,pagination:{pageIndex:h.state.page,pageSize:size}}});
    const model=p.page?engine.getRowModel():engine.getSortedRowModel();
    const scroll=node('div',null,'rvd-scroll');scroll.tabIndex=0;scroll.setAttribute('aria-label',p.label||'Table rows');
    const tbl=node('table',null,'rvd-table'),head=node('thead'),hr=node('tr'),body=node('tbody');
    for(const c of columns){const th=node('th');th.scope='col';const s=h.state.sort.find(s=>s.id===c.key);th.setAttribute('aria-sort',s?s.desc?'descending':'ascending':'none');const b=node('button',(c.label||c.key)+(s?s.desc?' ↓':' ↑':''));b.type='button';b.disabled=p.sort===false;b.onclick=()=>{h.state.sort=[{id:c.key,desc:s?!s.desc:false}];h.state.page=0;h.update();};th.append(b);hr.append(th);}head.append(hr);tbl.append(head,body);scroll.append(tbl);el.append(scroll);
    function paintRow(row){const tr=node('tr');tr.dataset.rowId=row.id;for(const cell of row.getVisibleCells()){const c=columns.find(c=>c.key===cell.column.id);const td=node('td',fmt(cell.getValue(),c?.format));td.title=td.textContent;tr.append(td);}if(p.id)window.RVBBIT_DASHBOARD?.bindSemanticObject?.(p.id,tr,{context:row.original});return tr;}
    let dispose=()=>{};
    if(!p.page && model.rows.length>200 && p.viewportHeight){
      const V=window.RVBBIT_VIRTUAL;if(!V)throw Error('Virtual table runtime is missing');
      scroll.style.height=`${Math.max(160,Number(p.viewportHeight))}px`;
      function spacer(height){const tr=node('tr'),td=node('td');td.colSpan=columns.length;td.style.cssText=`height:${height}px;padding:0;border:0`;tr.append(td);return tr;}
      let virtual;
      const paint=()=>{if(!virtual)return;const items=virtual.getVirtualItems();body.replaceChildren();if(!items.length)return;body.append(spacer(items[0].start));for(const item of items)body.append(paintRow(model.rows[item.index]));body.append(spacer(Math.max(0,virtual.getTotalSize()-items.at(-1).end)));};
      // Fixed-height rows are deliberate only in the opted-in virtual viewport.
      tbl.style.tableLayout='fixed';
      const virtualStyle=node('style');virtualStyle.textContent=`[data-dashboard-id="${CSS.escape(h.id)}"] tbody td{height:40px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}`;el.append(virtualStyle);
      virtual=new V.Virtualizer({count:model.rows.length,getScrollElement:()=>scroll,estimateSize:()=>40,overscan:8,observeElementRect:V.observeElementRect,observeElementOffset:V.observeElementOffset,scrollToFn:V.elementScroll,onChange:paint});dispose=virtual._didMount();virtual._willUpdate();paint();
    }else{for(const r of model.rows)body.append(paintRow(r));}
    if(!model.rows.length)el.append(node('p','No matching rows.','rvd-note'));
    if(p.page){const controls=node('div',null,'rvd-controls');for(const [label,delta] of [['Previous',-1],['Next',1]]){const b=node('button',label);b.type='button';b.disabled=delta<0?h.state.page===0:(h.state.page+1)*size>=rows.length;b.onclick=()=>{h.state.page+=delta;h.update();};controls.append(b);}controls.append(node('span',`Page ${h.state.page+1} · ${rows.length} loaded rows`));el.append(controls);}
    if(p.metadata?.truncated)el.append(node('p',`Partial results: ${p.rows.length} loaded rows (limit ${p.metadata.applied_limit??p.rows.length}). Sorting and filtering apply only to these rows.`,'rvd-note'));
    status(el,p);return()=>{dispose?.();};
  });}
  function chart(o){return base('chart',o,(el,p,h)=>{
    const C=window.RVBBIT_CHARTS;if(!C)throw Error(`chart #${h.id}: hosted chart runtime is missing`);
    const color=token=>window.rvbbitTheme?.color(token)||getComputedStyle(document.documentElement).getPropertyValue(token).trim();
    const resolve=(token,fallback)=>{const value=color(token);if(!value)return fallback;const probe=node('span');probe.style.color=value;document.body.append(probe);const resolved=getComputedStyle(probe).color;probe.remove();const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.fillStyle=resolved;ctx.fillRect(0,0,1,1);const [r,g,b,a]=ctx.getImageData(0,0,1,1).data;return `rgba(${r},${g},${b},${a/255})`;};
    const palette=['--artifact-chart-1','--artifact-chart-2','--artifact-chart-3','--artifact-chart-4','--artifact-chart-5','--artifact-chart-6'].map((t,i)=>resolve(t,resolve(i?'--artifact-accent-alt':'--artifact-accent',['#64bac3','#e8b87e','#ba95d0'][i%3])));
    const theme={foreground:resolve('--artifact-text','#edf2f4'),muted:resolve('--artifact-muted','#a0afb6'),grid:resolve('--artifact-border','#35434a'),background:resolve('--artifact-bg','#0b1218'),palette};
    const rows=(p.data||[]).map((r,i)=>({...r,__rvd_key:String(r[p.key||p.x]??i)+(p.series?':'+r[p.series]:''),[p.y]:r[p.y]==null?null:Number(r[p.y])}));
    if(!rows.length){el.append(node('p','No data for this selection.','rvd-note'));status(el,p);return;}
    const type=p.type||'bar',make={bar:C.barY,line:C.lineY,area:C.areaY,scatter:C.dot}[type];
    if(!make)throw Error(`chart #${h.id}: ${type} is unavailable. Use bar, line, area or scatter, or switch to HTML mode.`);
    const series=p.series || (p.color && rows.some(r=>typeof r[p.color]==='string')?p.color:null);
    const groups=series?[...new Set(rows.map(r=>r[series]))]:[null];
    const numericColor=p.color && !series;
    const domain=numericColor?rows.map(r=>Number(r[p.color])).filter(Number.isFinite):[];
    const paintScale=domain.length?C.scales.linear().domain([Math.min(...domain),Math.max(...domain)]).range([palette[0],palette[1]]):null;
    const marks=groups.flatMap((g,i)=>{
      const data=g==null?rows:rows.filter(r=>r[series]===g);
      const options={id:`${h.id}-${i}`,key:'__rvd_key',x:p.x,y:p.y,fill:paintScale?d=>paintScale(Number(d[p.color])):palette[i%palette.length],stroke:palette[i%palette.length],...(type==='line'?{points:true}:{}),...(type==='bar'?{inset:3}:{})};
      // Areas are a shared path; keyed points keep each observation available to Lens.
      return type==='area'?[make(data,{...options,id:options.id+'-area',fillOpacity:.35}),C.dot(data,{...options,r:3})]:[make(data,options)];
    });
    const numeric=type==='scatter';const definition=C.defineChart({marks,theme,scales:{x:{scale:numeric?C.scales.linear:type==='bar'?C.scales.band:C.scales.point,axis:{label:p.x||''}},y:{scale:C.scales.linear,nice:true,axis:{label:p.y||''}}},tooltip:{use:C.tooltip}});
    if(series){const legend=node('div',null,'rvd-controls');groups.forEach((g,i)=>{const item=node('span',g);item.style.color=palette[i%palette.length];legend.append(item);});el.append(legend);}
    const canvas=node('div',null,'rvd-chart');el.append(canvas);const height=Math.max(160,Number(p.height||280));
    const metadata={id:h.id,query:p.query,marks:Object.fromEntries(groups.map((_,i)=>[`${h.id}-${i}`,{x:p.x,y:p.y,value:p.y,query:p.query,context:p.series?[p.series]:[]}]))};
    let restored=false;
    const restore=()=>{if(h.state.selection && !restored){const n=canvas.querySelector(`[data-rvbbit-key="${CSS.escape(h.state.selection)}"]`);if(n){restored=true;requestAnimationFrame(()=>{if(n.isConnected)n.dispatchEvent(new MouseEvent('click',{bubbles:true}));});}}};
    const host=C.mountRvbbitChart(canvas,{definition,height,initialWidth:Math.max(100,el.clientWidth),ariaLabel:p.label||`${p.y} by ${p.x}`,onSelect:point=>{h.state.selection=point?.key||null;},onRender:restore},metadata);
    status(el,p);return()=>host.destroy();
  });}
  function filter(o){return base('filter',o,(el,p)=>{const label=node('label',p.label||p.field);const select=node('select');select.id=p.id+'-control';label.htmlFor=select.id;for(const choice of p.choices||[]){const c=typeof choice==='object'?choice:{value:choice,label:choice};const option=node('option',c.label??c.value);option.value=c.value;select.append(option);}select.value=p.value??'';select.onchange=()=>p.onChange?.(select.value,p.field);el.append(label,select);status(el,p);});}
  function check(){const issues=[];const state=window.__rvbbitDashboardState;if(state?.error || state?.loading)issues.push({severity:'error',message:state.error||'Dashboard is still updating. Wait for its queries and rendering to settle.'});for(const h of instances.values()){const el=h.el;if(!el.isConnected)continue;for(const n of [el,...el.querySelectorAll('.rvd-scroll')]){const s=getComputedStyle(n);const clippedY=n.scrollHeight>n.clientHeight+3 && ['hidden','clip'].includes(s.overflowY);const clippedX=n.scrollWidth>n.clientWidth+3 && ['hidden','clip','visible'].includes(s.overflowX);if(clippedX||clippedY)issues.push({id:h.id,kind:h.kind,severity:'error',message:`${h.kind} #${h.id} clips useful content. Remove the fixed size or give the box a scrolling viewport.`});}if(el.dataset.state==='error')issues.push({id:h.id,severity:'error',message:el.querySelector('.rvd-status')?.textContent||'Widget failed'});}return {widgets:instances.size,issues,healthy:issues.length===0};}
  let themeFrame=0;
  const repaint=()=>{cancelAnimationFrame(themeFrame);themeFrame=requestAnimationFrame(()=>{for(const h of [...instances.values()])if(h.kind==='chart' && h.el.isConnected)h.update();});};
  window.addEventListener('rvbbit:adaptive-theme',repaint);
  // The existing adaptive host also applies tokens directly to the root element.
  const themeObserver=new MutationObserver(repaint);
  themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['style','class','data-theme']});
  window.addEventListener('pagehide',()=>{themeObserver.disconnect();cancelAnimationFrame(themeFrame);for(const h of [...instances.values()])h.destroy();},{once:true});
  window.rvbbitDashboard=Object.freeze({version:'1.0.0',kpi,table,chart,filter,check});
})();
