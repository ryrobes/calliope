const STORAGE_KEY='calliope.logic-workbench-layout.v1';
const clamp=(n,min,max)=>Math.max(min,Math.min(max,n));
export function normalizeLayout(value){
 const fraction=(v,fallback)=>typeof v==='number'&&Number.isFinite(v)?clamp(v,.15,.8):fallback;
 return {dock:value?.dock==='right'?'right':'bottom',bottom:fraction(value?.bottom,.32),right:fraction(value?.right,.42)};
}
export function panelFraction(value,extent,dock){
 if(!extent)return value;
 const panelMin=Math.min(dock==='right'?300:160,extent*.45),canvasMin=Math.min(dock==='right'?240:180,extent*.35);
 return clamp(value,Math.max(.15,panelMin/extent),Math.min(.8,1-canvasMin/extent));
}
export function mountWorkspace({root,panel,divider,dockButton,toggleButton,onResize=()=>{}}){
 let initial;try{initial=JSON.parse(localStorage.getItem(STORAGE_KEY));}catch{}
 let prefs=normalizeLayout(initial),collapsed=false,drag=null;
 const save=()=>{try{localStorage.setItem(STORAGE_KEY,JSON.stringify(prefs));}catch{}};
 function apply(){
  root.dataset.dock=prefs.dock;root.classList.toggle('panel-collapsed',collapsed);panel.classList.toggle('collapsed',collapsed);
  const extent=prefs.dock==='right'?root.clientWidth:root.clientHeight,share=panelFraction(prefs[prefs.dock],extent,prefs.dock);
  root.style.setProperty('--sql-panel-size',share*100+'%');
  divider.hidden=collapsed;divider.setAttribute('aria-orientation',prefs.dock==='right'?'vertical':'horizontal');
  divider.setAttribute('aria-valuenow',Math.round(share*100));divider.setAttribute('aria-valuetext',`SQL panel ${Math.round(share*100)} percent ${prefs.dock==='right'?'width':'height'}`);
  dockButton.textContent=prefs.dock==='right'?'Dock bottom ↓':'Dock right →';dockButton.setAttribute('aria-label',prefs.dock==='right'?'Dock SQL panel at bottom':'Dock SQL panel on right');
  toggleButton.textContent=collapsed?(prefs.dock==='right'?'‹':'Show panel ↑'):(prefs.dock==='right'?'Hide panel →':'Hide panel ↓');
  toggleButton.setAttribute('aria-label',collapsed?'Show SQL panel':'Hide SQL panel');toggleButton.setAttribute('aria-expanded',String(!collapsed));toggleButton.title=collapsed?'Show SQL panel':'Hide SQL panel';
  onResize();
 }
 const expand=()=>{collapsed=false;apply();};
 dockButton.onclick=()=>{prefs.dock=prefs.dock==='right'?'bottom':'right';apply();save();};
 toggleButton.onclick=()=>{collapsed=!collapsed;apply();};
 divider.addEventListener('pointerdown',e=>{
  if(e.button!==0)return;e.preventDefault();divider.focus({preventScroll:true});
  drag={pointer:e.pointerId,dock:prefs.dock};divider.setPointerCapture(e.pointerId);
  document.documentElement.dataset.logicResize=prefs.dock;
 });
 divider.addEventListener('pointermove',e=>{
  if(!drag||drag.pointer!==e.pointerId)return;e.preventDefault();const r=root.getBoundingClientRect();
  const extent=drag.dock==='right'?r.width:r.height,remaining=drag.dock==='right'?r.right-e.clientX:r.bottom-e.clientY;
  prefs[drag.dock]=panelFraction(remaining/extent,extent,drag.dock);apply();
 });
 function finish(){if(!drag)return;const pointer=drag.pointer;drag=null;delete document.documentElement.dataset.logicResize;if(divider.hasPointerCapture(pointer))divider.releasePointerCapture(pointer);save();}
 for(const event of ['pointerup','pointercancel','lostpointercapture'])divider.addEventListener(event,finish);
 window.addEventListener('blur',finish);
 divider.addEventListener('keydown',e=>{
  const grow=prefs.dock==='right'?'ArrowLeft':'ArrowUp',shrink=prefs.dock==='right'?'ArrowRight':'ArrowDown';
  if(![grow,shrink,'Home','End'].includes(e.key))return;e.preventDefault();
  prefs[prefs.dock]=e.key==='Home'?.15:e.key==='End'?.8:prefs[prefs.dock]+(e.key===grow?1:-1)*(e.shiftKey?.1:.02);
  prefs[prefs.dock]=panelFraction(prefs[prefs.dock],prefs.dock==='right'?root.clientWidth:root.clientHeight,prefs.dock);apply();save();
 });
 new ResizeObserver(apply).observe(root);apply();
 return {expand};
}

export function mountCanvasPan(viewport){
 let drag=null;
 viewport.addEventListener('pointerdown',e=>{
  if(e.button!==0||e.target.closest('.logic-node'))return;
  // Cancel native selection only for a gesture that starts on empty canvas.
  e.preventDefault();drag={pointer:e.pointerId,x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};
  viewport.setPointerCapture(e.pointerId);viewport.classList.add('panning');document.documentElement.classList.add('logic-canvas-drag');
 });
 viewport.addEventListener('pointermove',e=>{if(!drag||drag.pointer!==e.pointerId)return;e.preventDefault();viewport.scrollLeft=drag.left+drag.x-e.clientX;viewport.scrollTop=drag.top+drag.y-e.clientY;});
 function finish(){if(!drag)return;const pointer=drag.pointer;drag=null;viewport.classList.remove('panning');document.documentElement.classList.remove('logic-canvas-drag');if(viewport.hasPointerCapture(pointer))viewport.releasePointerCapture(pointer);}
 for(const event of ['pointerup','pointercancel','lostpointercapture'])viewport.addEventListener(event,finish);
 window.addEventListener('blur',finish);
 document.addEventListener('selectstart',e=>{if(drag)e.preventDefault();});
}
