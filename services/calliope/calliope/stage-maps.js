/* Native Mapbox layers over viewer-scoped SQL. The Stage owns every JS effect. */
(() => {
  'use strict';
  let runtimePromise;
  function runtime(cfg) {
    if (!cfg?.enabled) return Promise.reject(new Error('Maps are not configured on this appliance'));
    if (window.mapboxgl) { window.mapboxgl.accessToken = cfg.token; return Promise.resolve(window.mapboxgl); }
    if (!runtimePromise) runtimePromise = new Promise((resolve, reject) => {
      const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = cfg.gl_css_url; document.head.append(link);
      const script = document.createElement('script'); script.src = cfg.gl_js_url;
      const timer = setTimeout(() => { script.remove(); reject(new Error('Mapbox runtime timed out')); }, 20000);
      script.onload = () => { clearTimeout(timer); window.mapboxgl.accessToken = cfg.token; resolve(window.mapboxgl); };
      script.onerror = () => { clearTimeout(timer); script.remove(); reject(new Error('Mapbox runtime could not load')); };
      document.head.append(script);
    }).catch(error => { runtimePromise = null; throw error; });
    return runtimePromise;
  }
  function color(variable, fallback) {
    const probe = document.createElement('span');
    probe.style.cssText = `position:absolute;visibility:hidden;color:var(${variable},${fallback})`;
    document.body.append(probe); const css = getComputedStyle(probe).color; probe.remove();
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d', {willReadFrequently:true}); ctx.fillStyle = fallback; ctx.fillStyle = css; ctx.fillRect(0,0,1,1);
    const [r,g,b,a] = ctx.getImageData(0,0,1,1).data;
    return `rgba(${r},${g},${b},${a/255})`;
  }
  function paintColors(value) {
    const tokens = {'@accent':['--amber','#68c7b2'],'@positive':['--jade','#22c55e'],
      '@warning':['--amber','#f59e0b'],'@danger':['--danger','#ef4444'],'@text':['--bone','#eeeeee']};
    if (typeof value === 'string' && tokens[value]) return color(...tokens[value]);
    if (Array.isArray(value)) return value.map(paintColors);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,paintColors(v)]));
    return value;
  }
  function bounds(sources) {
    const xs=[]; let south=90,north=-90;
    function visit(c) {
      if (!Array.isArray(c)) return;
      if (typeof c[0] === 'number') { xs.push((c[0]+360)%360); south=Math.min(south,c[1]); north=Math.max(north,c[1]); }
      else c.forEach(visit);
    }
    Object.values(sources).forEach(fc=>fc.features.forEach(f=>visit(f.geometry.coordinates)));
    if (!xs.length) return null;
    xs.sort((a,b)=>a-b);
    let gap=-1,start=0;
    for (let i=0;i<xs.length;i++) { const d=(i===xs.length-1?xs[0]+360:xs[i+1])-xs[i]; if(d>gap){gap=d;start=(i+1)%xs.length;} }
    let west=xs[start],east=west+360-gap;
    if(west>180){west-=360;east-=360;}
    return [[west,south],[east,north]];
  }
  function create({root, scroll, escapeHtml, toast}) {
    const entries = new Map();
    let themeSignature = '';
    const mode = () => /light/i.test(document.documentElement.dataset.theme || '') || document.documentElement.style.colorScheme === 'light' ? 'light' : 'dark';
    function render(surface, revisions=[]) {
      const p=surface.payload||{};
      const key=p.content_sha256||JSON.stringify([p.sources,p.layers,p.queries,p.viewport,p.height]);
      let e=entries.get(surface.id);
      if (!e || e.key!==key) { if(e) {if(e.host){resize.unobserve(e.host);intersection.unobserve(e.host);}dispose(e);} e={id:surface.id,key,p,title:surface.title,generation:0}; entries.set(surface.id,e); }
      const history=revisions.length>1 ? `<details class="map-history"><summary>Earlier versions · ${revisions.length-1}</summary>${revisions.slice(1).map(s=>`<div>${escapeHtml(new Date(s.created_at).toLocaleString())} · ${escapeHtml(s.title)}</div>`).join('')}</details>` : '';
      return `<section class="stage-map" data-map-surface="${escapeHtml(surface.id)}" data-map-key="${escapeHtml(key)}">
        <div class="stage-map-canvas" style="height:${Math.max(240,Math.min(900,Number(p.height)||420))}px" aria-label="${escapeHtml(surface.title||'Map')}"></div>
        <div class="stage-map-status" role="status">Map loads when visible</div>
        <footer class="stage-map-controls"><span class="stage-map-freshness">Live SQL</span><button type="button" data-map-action="refresh">Refresh</button><button type="button" data-map-action="fit">Fit data</button><button type="button" data-map-action="resume" hidden>Resume map</button><button type="button" data-map-action="error" hidden>Copy error</button>${history}</footer></section>`;
    }
    function status(e,text,error=false) {
      if(!e.host) return;
      e.host.querySelector('.stage-map-status').textContent=text;
      e.host.classList.toggle('is-error',error);
      e.host.querySelector('[data-map-action=error]').hidden=!error;
    }
    async function report(e,g,phase,error) {
      const attempt=e.data?.render_attempt;
      if(!attempt || g!==e.generation) return;
      const signature=JSON.stringify([attempt,phase,error||'']); if(e.reported===signature)return; e.reported=signature;
      try {
        const r=await fetch(`/api/calliope/surfaces/${encodeURIComponent(e.id)}/map-render`,{method:'POST',headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(8000),
          body:JSON.stringify({attempt,sequence:e.sequence=(e.sequence||0)+1,status:error?'error':'ready',phase,message:error||'',width:e.host?.clientWidth||0})});
        if(!r.ok)throw new Error('Diagnostic could not be saved');
      } catch (_) { if(e.reported===signature)e.reported=null; }
    }
    function fail(e,g,error,phase='render') {
      if(g!==e.generation || !e.host?.isConnected)return;
      // Mapbox fetch errors include the style URL and its appliance token.
      // Keep credentials out of visible errors, copied diagnostics and receipts.
      let message=String(error?.message||error);
      const token=e.data?.runtime?.token;
      if(token)message=message.split(token).join('[redacted]');
      e.error=message.replace(/([?&]access_token=)[^\s&#]+/gi,'$1[redacted]').slice(0,2000); e.failed=true;
      status(e,`Map could not render: ${e.error.slice(0,400)}`,true);
      void report(e,g,phase,e.error);
    }
    function camera(e) {
      if(e.map) { const c=e.map.getCenter();e.camera={center:[c.lng,c.lat],zoom:e.map.getZoom(),bearing:e.map.getBearing(),pitch:e.map.getPitch()}; }
    }
    function dispose(e) {
      camera(e); e.generation++;e.loading=false;e.abort?.abort();clearTimeout(e.timeout);
      e.popup?.remove();e.popup=null;e.map?.remove();e.map=null;
      if(e.host){e.host.classList.remove('is-ready');const resume=e.host.querySelector('[data-map-action=resume]');if(resume)resume.hidden=false;status(e,'Map paused while offscreen');}
    }
    function fit(e) {
      const b=e.data&&bounds(e.data.sources);
      if(e.map&&b)e.map.fitBounds(b,{padding:40,maxZoom:14,duration:0});
    }
    function addLayers(e,g) {
      const map=e.map,gl=window.mapboxgl;
      if(!map || g!==e.generation)return;
      try {
        for(const [name,fc] of Object.entries(e.data.sources)) {
          const s=e.p.sources[name], options={type:'geojson',data:fc};
          for(const k of ['cluster','clusterRadius','clusterMaxZoom'])if(s[k]!==undefined)options[k]=s[k];
          map.addSource('rvbbit-'+name,options);
        }
        for(const original of e.p.layers) {
          const layer=structuredClone(original);layer.id='rvbbit-'+original.id;layer.source='rvbbit-'+original.source;
          layer.paint=paintColors(layer.paint||{});
          const defaults={circle:{'circle-color':'@accent','circle-radius':6},line:{'line-color':'@accent','line-width':2},fill:{'fill-color':'@accent','fill-opacity':0.45},'fill-extrusion':{'fill-extrusion-color':'@accent'},heatmap:{'heatmap-color':['interpolate',['linear'],['heatmap-density'],0,'rgba(0,0,0,0)',0.4,'@accent',1,'@danger']},symbol:{'text-color':'@text'}};
          layer.paint={...paintColors(defaults[layer.type]||{}),...layer.paint};
          map.addLayer(layer);
          const fields=e.p.sources[original.source].tooltip||[];
          if(fields.length) {
            const click=event=>{
              const feature=event.features?.[0];if(!feature)return;
              const content=document.createElement('dl');content.className='stage-map-tooltip';
              for(const field of fields){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=field;dd.textContent=String(feature.properties?.[field]??'—');content.append(dt,dd);}
              e.popup?.remove();e.popup=new gl.Popup({maxWidth:'300px'}).setLngLat(event.lngLat).setDOMContent(content).addTo(map);
            };
            // Style reloads replace layers but not listeners. Remove old handlers first.
            const old=e.clicks?.get(layer.id);if(old)map.off('click',layer.id,old);
            if(!e.clicks)e.clicks=new Map();e.clicks.set(layer.id,click);map.on('click',layer.id,click);
          }
        }
        if(!e.camera && e.p.viewport?.fit==='data')fit(e);
      } catch(error){fail(e,g,error);}
    }
    async function mount(e,refresh=false) {
      if(!e.host?.isConnected||e.loading)return;
      if(e.map&&!refresh){e.map.resize();return;}
      if(refresh)dispose(e);
      const g=++e.generation; e.loading=true;e.failed=false;e.error=null;
      e.host.querySelector('[data-map-action=resume]').hidden=true;
      e.host.querySelector('[data-map-action=refresh]').disabled=true;
      status(e,'Loading map data…');let phase='query';
      try {
        if(!e.data||refresh){
          e.abort=new AbortController();
          const r=await fetch(`/api/calliope/surfaces/${encodeURIComponent(e.id)}/map-data`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh}),signal:e.abort.signal});
          const data=await r.json();if(!r.ok||data.error)throw new Error(data.error?.message||`Query failed (${r.status})`);
          if(g!==e.generation)return;e.data=data;e.loadedAt=new Date();
        }
        phase='runtime';const gl=await runtime(e.data.runtime);
        if(g!==e.generation||!e.host.isConnected)return;
        // Bound GPU contexts when a very busy Stage contains many small maps.
        const active=[...entries.values()].filter(other=>other!==e&&other.map).sort((a,b)=>a.lastActive-b.lastActive);
        while(active.length>=4)dispose(active.shift());
        e.lastActive=Date.now();e.clicks=new Map();phase='render';status(e,'Loading map layers…');
        const viewport=e.camera||e.p.viewport||{};
        const options={container:e.host.querySelector('.stage-map-canvas'),style:e.data.runtime.styles[mode()],preserveDrawingBuffer:true,cooperativeGestures:true,attributionControl:true};
        for(const k of ['center','zoom','bearing','pitch'])if(viewport[k]!==undefined)options[k]=viewport[k];
        const map=e.map=new gl.Map(options);
        map.addControl(new gl.NavigationControl({showCompass:true}),'top-right');
        map.on('style.load',()=>addLayers(e,g));
        map.on('error',event=>fail(e,g,event.error||'Mapbox rendering error'));
        e.timeout=setTimeout(()=>fail(e,g,'Map tiles or layers did not settle within 30 seconds'),30000);
        map.on('idle',()=>{
          if(g!==e.generation||e.failed)return;
          clearTimeout(e.timeout);e.host.classList.add('is-ready');
          const stats=Object.values(e.data.source_stats||{}),count=stats.reduce((n,s)=>n+s.features,0),invalid=stats.reduce((n,s)=>n+s.invalid_geometry_rows,0);
          const truncated=Object.entries(e.data.results||{}).filter(([,r])=>r.truncated).map(([name])=>name);
          status(e, count ? '' : 'No locations matched this query');
          e.host.querySelector('.stage-map-freshness').textContent=`${count.toLocaleString()} features · ${e.loadedAt.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}${invalid?` · ${invalid} invalid geometry rows skipped`:''}${truncated.length?` · Row limit reached: ${truncated.join(', ')}`:''}`;
          void report(e,g,'render');
        });
      } catch(error){if(error.name!=='AbortError')fail(e,g,error,phase);}
      finally{if(g===e.generation){e.loading=false;if(e.host)e.host.querySelector('[data-map-action=refresh]').disabled=false;}}
    }
    const resize=new ResizeObserver(items=>{for(const {target} of items){const e=entries.get(target.dataset.mapSurface);e?.map?.resize();}});
    const intersection=new IntersectionObserver(items=>{for(const {target,isIntersecting}of items){const e=entries.get(target.dataset.mapSurface);if(!e||e.host!==target)continue;if(isIntersecting)void mount(e);else if(e.map||e.loading)dispose(e);}}, {root:scroll,rootMargin:'240px'});
    function hydrate() {
      const hosts=new Map([...root.querySelectorAll('[data-map-surface]')].map(h=>[h.dataset.mapSurface,h]));
      for(const [id,e]of entries){
        const h=hosts.get(id);
        if(e.host&&e.host!==h){resize.unobserve(e.host);intersection.unobserve(e.host);dispose(e);e.host=null;}
        if(!h){entries.delete(id);continue;}
        if(!e.host){e.host=h;resize.observe(h);intersection.observe(h);}
      }
    }
    root.addEventListener('click',event=>{
      const button=event.target.closest('[data-map-action]');if(!button)return;
      event.preventDefault();event.stopPropagation();const e=entries.get(button.closest('[data-map-surface]').dataset.mapSurface);if(!e)return;
      const action=button.dataset.mapAction;
      if(action==='refresh'||action==='resume')void mount(e,action==='refresh');
      else if(action==='fit')fit(e);
      else if(action==='error')navigator.clipboard.writeText(`Map: ${e.title}\nSurface: ${e.id}\nError: ${e.error}\nUse read_map and update_map to inspect and repair.`).then(()=>toast('Map error copied'),()=>toast('Could not copy map error',true));
    });
    // Theme changes keep camera/data, replacing only the basemap and native layers.
    const themeObserver=new MutationObserver(()=>{
      const next=mode(),signature=next+color('--amber','#68c7b2')+color('--bone','#eeeeee');
      if(signature===themeSignature)return;themeSignature=signature;
      for(const e of entries.values())if(e.map){camera(e);e.failed=false;clearTimeout(e.timeout);e.timeout=setTimeout(()=>fail(e,e.generation,'Map theme did not settle within 30 seconds'),30000);e.map.setStyle(e.data.runtime.styles[next],{diff:false});}
    });
    themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','style','class']});
    return {render,hydrate};
  }
  window.CalliopeStageMaps={create};
})();
