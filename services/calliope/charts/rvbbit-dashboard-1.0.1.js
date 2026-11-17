/* Calliope Dashboard 1.0.1 — immutable hosted widget runtime. */
(() => {
  'use strict';
  if (window.rvbbitDashboard) return;
  const instances = new Map();
  const failures = new Map();
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
  function validate(kind,p) {
    const common=['el','id','state','error','note','label'];
    const fields={kpi:['value','format','context'],table:['rows','columns','sort','page','filter','metadata','viewportHeight'],chart:['data','type','x','y','color','series','query','height','key'],filter:['field','value','choices','onChange']};
    const fail=message=>{throw Error(`${kind} #${p.id||p.el}: ${message}`);};
    const unknown=Object.keys(p).filter(k=>!common.includes(k)&&!fields[kind].includes(k));
    if(unknown.length)fail(`unsupported option ${unknown.join(', ')}. ${kind==='kpi'?'Mount one kpi({el,id,label,value,note}) per value; there is no items API.':'See dashboard_template.widget_api for supported options.'}`);
    if(kind==='kpi'){
      if(!Object.hasOwn(p,'value'))fail('provide value (null means unavailable), not an items array.');
      if(p.value!=null && !['number','string'].includes(typeof p.value))fail('value must be a number, string, or null.');
      if(typeof p.value==='number'&&!Number.isFinite(p.value))fail('value must be finite, or null when unavailable.');
    }
    if(kind==='chart'){
      if(!Array.isArray(p.data))fail('data must be an array of row objects.');
      for(const key of ['x','y'])if(typeof p[key]!=='string'||!p[key])fail(`${key} must name one data field, not an array or series definition.`);
      for(const key of ['series','color','key'])if(p[key]!=null&&typeof p[key]!=='string')fail(`${key} must name a data field.`);
      for(const [i,row] of p.data.entries()){
        if(!row||typeof row!=='object'||Array.isArray(row))fail(`data row ${i+1} must be an object.`);
        for(const field of [p.x,p.y,p.series,p.color,p.key].filter(Boolean))if(!Object.hasOwn(row,field))fail(`data row ${i+1} is missing field "${field}".`);
        if(row[p.y]!=null && (row[p.y]===''||!Number.isFinite(Number(row[p.y]))))fail(`field "${p.y}" must contain numeric values or null gaps.`);
      }
      if(p.height!=null&&(!Number.isFinite(Number(p.height))||Number(p.height)<=0))fail('height must be a positive number.');
    }
    if(kind==='table'){
      if(!Array.isArray(p.rows))fail('rows must be an array of objects.');
      if(p.columns && (!Array.isArray(p.columns)||p.columns.some(c=>!c||typeof c.key!=='string')))fail('columns must be objects with key and label.');
    }
    if(kind==='filter' && (!Array.isArray(p.choices)||typeof p.onChange!=='function'))fail('provide choices and an explicit onChange callback.');
  }
  function base(kind, options, render) {
    let opts={...options}; const el=typeof opts.el==='string'?document.querySelector(opts.el):opts.el;
    if(!el)throw Error(`${kind}: destination element is missing`);
    const id=opts.id || el.id; if(!id)throw Error(`${kind}: provide a stable id`);
    instances.get(id)?.destroy();
    el.classList.add('rvd-box'); el.dataset.dashboardWidget=kind; el.dataset.dashboardId=id;
    let cleanup=()=>{}, destroyed=false;
    const handle={el,id,kind,options:()=>opts,state:{},update(patch={}){
      if(destroyed)return;opts={...opts,...patch};
      try{validate(kind,opts);cleanup();el.replaceChildren();cleanup=render(el,opts,handle)||(()=>{});failures.delete(id);}
      catch(error){failures.set(id,{id,kind,severity:'error',message:error.message});el.dataset.state='error';el.querySelector('.rvd-validation-error')?.remove();const message=node('p',error.message,'rvd-status rvd-validation-error');message.setAttribute('role','alert');el.append(message);throw error;}
    },destroy(){if(destroyed)return;destroyed=true;cleanup();el.replaceChildren();delete el.dataset.dashboardWidget;delete el.dataset.dashboardId;instances.delete(id);failures.delete(id);}};
    instances.set(id,handle);
    handle.update();
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
    const series=p.series || (p.color && p.data.some(r=>typeof r[p.color]==='string')?p.color:null);
    const rows=p.data.map((r,i)=>({...r,__rvd_key:JSON.stringify([r[p.key||p.x]??i,series?r[series]:null]),[p.y]:r[p.y]==null?null:Number(r[p.y])}));
    if(!rows.length){el.append(node('p','No data for this selection.','rvd-note'));status(el,p);return;}
    const type=p.type||'bar',make={bar:C.barY,line:C.lineY,area:C.areaY,scatter:C.dot}[type];
    if(!make)throw Error(`chart #${h.id}: ${type} is unavailable. Use bar, line, area or scatter, or switch to HTML mode.`);
    const groups=series?[...new Set(rows.map(r=>r[series]))]:[null];
    const numericColor=p.color && !series;
    const domain=numericColor?rows.map(r=>Number(r[p.color])).filter(Number.isFinite):[];
    const paintScale=domain.length?C.scales.linear().domain([Math.min(...domain),Math.max(...domain)]).range([palette[0],palette[1]]):null;
    const groupedBar=type==='bar' && Boolean(series);
    const marks=groupedBar?[C.barY(rows,{id:`${h.id}-0`,key:'__rvd_key',x:p.x,y:p.y,z:series,layout:{type:'group',padding:.15},inset:1,fill:d=>palette[groups.indexOf(d[series])%palette.length]})]:groups.flatMap((g,i)=>{
      const data=g==null?rows:rows.filter(r=>r[series]===g);
      const options={id:`${h.id}-${i}`,key:'__rvd_key',x:p.x,y:p.y,fill:paintScale?d=>paintScale(Number(d[p.color])):palette[i%palette.length],stroke:palette[i%palette.length],...(type==='line'?{points:true}:{}),...(type==='bar'?{inset:3}:{})};
      // Areas are a shared path; keyed points keep each observation available to Lens.
      return type==='area'?[make(data,{...options,id:options.id+'-area',fillOpacity:.35}),C.dot(data,{...options,r:3})]:[make(data,options)];
    });
    const numeric=type==='scatter';const definition=C.defineChart({marks,theme,scales:{x:{scale:numeric?C.scales.linear:type==='bar'?C.scales.band:C.scales.point,axis:{label:p.x||''}},y:{scale:C.scales.linear,nice:true,axis:{label:p.y||''}}},tooltip:{use:C.tooltip}});
    if(series){const legend=node('div',null,'rvd-controls');groups.forEach((g,i)=>{const item=node('span',g);item.style.color=palette[i%palette.length];legend.append(item);});el.append(legend);}
    const canvas=node('div',null,'rvd-chart');el.append(canvas);const height=Math.max(160,Number(p.height||280));canvas.style.height=`${height}px`;
    const markIds=groupedBar?[`${h.id}-0`]:groups.flatMap((_,i)=>type==='area'?[`${h.id}-${i}-area`,`${h.id}-${i}`]:[`${h.id}-${i}`]);
    const metadata={id:h.id,query:p.query,marks:Object.fromEntries(markIds.map(id=>[id,{x:p.x,y:p.y,value:p.y,query:p.query,context:series?[series]:[]}]))};
    let restored=false;
    const restore=()=>{if(h.state.selection && !restored){const n=canvas.querySelector(`[data-rvbbit-key="${CSS.escape(h.state.selection)}"]`);if(n){restored=true;requestAnimationFrame(()=>{if(n.isConnected)n.dispatchEvent(new MouseEvent('click',{bubbles:true}));});}}};
    const host=C.mountRvbbitChart(canvas,{definition,height,initialWidth:Math.max(100,el.clientWidth),ariaLabel:p.label||`${p.y} by ${p.x}`,onSelect:point=>{h.state.selection=point?.key||null;},onRender:restore},metadata);
    status(el,p);return()=>host.destroy();
  });}
  function filter(o){return base('filter',o,(el,p)=>{const label=node('label',p.label||p.field);const select=node('select');select.id=p.id+'-control';label.htmlFor=select.id;for(const choice of p.choices||[]){const c=typeof choice==='object'?choice:{value:choice,label:choice};const option=node('option',c.label??c.value);option.value=c.value;select.append(option);}select.value=p.value??'';select.onchange=()=>p.onChange?.(select.value,p.field);el.append(label,select);status(el,p);});}
  function check(){
    const issues=[],connected=[...instances.values()].filter(h=>h.el.isConnected);
    if(!connected.length)issues.push({severity:'error',message:'No Dashboard widgets mounted. Check query and render errors.'});
    const state=window.__rvbbitDashboardState;
    if(state?.error||state?.loading)issues.push({severity:'error',message:state.error||'Dashboard is still updating. Wait for its queries and rendering to settle.'});
    for(const h of connected){
      const el=h.el;
      if(failures.has(h.id))issues.push(failures.get(h.id));
      for(const n of [el,...el.querySelectorAll('.rvd-scroll')]){
        const s=getComputedStyle(n),clippedY=n.scrollHeight>n.clientHeight+3&&['hidden','clip'].includes(s.overflowY),clippedX=n.scrollWidth>n.clientWidth+3&&['hidden','clip','visible'].includes(s.overflowX);
        if(clippedX||clippedY)issues.push({id:h.id,kind:h.kind,severity:'error',message:`${h.kind} #${h.id} clips useful content. Remove the fixed size or give the box a scrolling viewport.`});
      }
      const svg=el.querySelector('.rvd-chart svg');
      if(svg){
        const rect=svg.getBoundingClientRect();
        for(let parent=svg.parentElement;parent&&parent!==document.body;parent=parent.parentElement){
          const style=getComputedStyle(parent),box=parent.getBoundingClientRect();
          if(['auto','scroll'].includes(style.overflowY)||['auto','scroll'].includes(style.overflowX))break;
          if(style.display==='contents'||box.height===0)continue;
          if(rect.bottom>box.bottom+3||rect.right>box.right+3||rect.left<box.left-3||rect.top<box.top-3){issues.push({id:h.id,kind:h.kind,severity:'error',message:`chart #${h.id} extends outside its container. Let the card grow around the chart viewport.`});break;}
        }
      }
      if(el.dataset.state==='error'&&!failures.has(h.id))issues.push({id:h.id,severity:'error',message:el.querySelector('.rvd-status')?.textContent||'Widget failed'});
    }
    return {widgets:connected.length,issues,healthy:issues.length===0};
  }
  let themeFrame=0;
  const repaint=()=>{cancelAnimationFrame(themeFrame);themeFrame=requestAnimationFrame(()=>{for(const h of [...instances.values()])if(h.kind==='chart' && h.el.isConnected)h.update();});};
  window.addEventListener('rvbbit:adaptive-theme',repaint);
  // The existing adaptive host also applies tokens directly to the root element.
  const themeObserver=new MutationObserver(repaint);
  themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['style','class','data-theme']});
  window.addEventListener('pagehide',()=>{themeObserver.disconnect();cancelAnimationFrame(themeFrame);for(const h of [...instances.values()])h.destroy();},{once:true});
  window.rvbbitDashboard=Object.freeze({version:'1.0.1',kpi,table,chart,filter,check});
})();
