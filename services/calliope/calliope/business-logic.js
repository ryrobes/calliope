import {usageTracker,costLabel,cacheLabel,money,usageDetail} from './business-logic-usage.mjs';
import {blank, example, classifierExample, actionExample, namedSql, leaf, binary, choice, escapeHtml as esc, nodeAt, replaceNode, routes, layout, tracedPaths, validateTree, sourceSql, rowContext, replaceRoute, pointer, stepPackage} from './business-logic-model.mjs';
import {mountWorkspace,mountCanvasPan} from './business-logic-workspace.mjs';
import {mountCodeEditor} from './business-logic-code-editor.mjs';
import {mountCapability} from './business-logic-capability.mjs';
import {actionHtml,mountActionEditors} from './business-logic-actions.mjs';
const $ = id => document.getElementById(id);
const endpoint = '/api/calliope/business-logic';
let batchUsage=null,usageFocus=null;
const costs=usageTracker({fetchUsage:id=>api('/usage/'+encodeURIComponent(id)),updated:()=>paintUsage(),visible:()=>!document.hidden});
let doc, id=null, version=0, dirty=false, items=[], undo=[], rows=[], selectedRow=0;
let preview=null, override=null, cohortStale=true, generation=0, running=false, saving=false;
let armed=false, popover=null, zoom=1, editing=null, liveTimer, sqlTimer, noticeTimer, queryGeneration=0, compiledSql='', inlineSql='', sourceClauseSql='', sqlError='';
let graphLayout=null, reflowFrame=0, actionEditors=[], executing=false, executionRequest=null, lastExecution=null;
let publishedVersion=null,savedKey=null,lastReceipt=null,viewedVersion=null;
const sourceEditor=mountCodeEditor($('source-sql'),{label:'Source SQL',onChange:text=>{doc.source_sql=text;armed=false;invalidate({tree:true});$('rows-hint').textContent='Source query changed. Run SQL to refresh these rows.';},onRun:()=>runSql()});
const namedEditor=mountCodeEditor($('named-sql'),{label:'Named SQL, read only',readOnly:true});
const inlineEditor=mountCodeEditor($('inline-sql'),{label:'Inline SQL, read only',readOnly:true});
const queryEditor=mountCodeEditor($('composed-sql'),{label:'Executable SQL, read only',readOnly:true});
for(const n of [1,12,100,500,1000,5000,10000].filter(n=>n<=Number($('row-limit').max))){const o=document.createElement('option');o.value=n;$('row-limit-presets').append(o);}
const definitionEditor=mountCodeEditor($('tree-json'),{language:'json',label:'Decision tree JSON',onRun:()=>applyDefinition()});
const workspace=mountWorkspace({root:$('logic-workspace'),panel:$('sql-workbench'),divider:$('sql-divider'),dockButton:$('dock-sql'),toggleButton:$('toggle-data'),onResize:()=>{sourceEditor.measure();queryEditor.measure();namedEditor.measure();inlineEditor.measure();}});
mountCanvasPan($('graph-viewport'));
const capability=mountCapability({get:()=>({doc,id,version,dirty,input:input(),limit:Number($('row-limit').value)}),api,notice,changed:()=>{dirty=true;status();scheduleSql();},save:saveDocument,published:record=>{publishedVersion=record.published_version;savedKey=record.rule_key;$('sql-view').value='latest';status();scheduleSql();refreshLibrary();}});

async function api(path='',body) {
 const response=await fetch(endpoint+path,{credentials:'same-origin',method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
 let value={};try{value=await response.json();}catch{}
 if(!response.ok)throw Error(value.error?.message||`Request failed (${response.status})`);
 return value;
}
function notice(message){clearTimeout(noticeTimer);$('logic-notice').textContent=message;$('logic-notice').hidden=false;noticeTimer=setTimeout(()=>$('logic-notice').hidden=true,6500);}
function input(){return override||rows[selectedRow]?.input||{};}
function decision(){return preview?.decision||(!cohortStale&&!override?rows[selectedRow]?.decision:null);}
function rowTitle(row,index){return row?.input?.case_name||row?.input?.request_id||`Row ${index+1}`;}
function summary(value){return value===undefined?'Missing':value===null?'null':typeof value==='object'?JSON.stringify(value):String(value);}
function nodeId(path,field){return 'edit-'+encodeURIComponent(path)+'-'+encodeURIComponent(field);}
function status(){
 $('save-status').textContent=viewedVersion&&viewedVersion!==version?`Viewing v${viewedVersion} · Latest v${version}`:dirty?(id?`v${version} · Edited`:'Unsaved'):(id?`v${version}`:'Unsaved example');
 $('save-decision').disabled=saving;$('undo-tree').disabled=!undo.length;
 $('run-sql').disabled=running||!$('row-limit').checkValidity();$('run-row').disabled=running||!rows.length;
 $('execute-row').disabled=executing||running||!rows.length||!layout(doc.tree).nodes.some(n=>n.node.action);
 $('execute-row').textContent=executing?'Executing…':'Execute selected row';
 $('toggle-capability').textContent=publishedVersion?`Callie · v${publishedVersion}`:'Use with Callie';
 const named=publishedVersion&&savedKey;
 $('show-sql').textContent=named?'rvbbit.business_logic( rule, row ) ↗':'rvbbit.decision_tree( row, tree ) ↗';
 renderSqlUsage();
}
function sqlToCopy(){return publishedVersion&&savedKey&&sourceClauseSql?namedSql(savedKey,sourceClauseSql,$('sql-view').value==='pinned'?publishedVersion:null):'';}
function renderSqlUsage(){
 const named=!!(publishedVersion&&savedKey),mode=$('sql-view');
 mode.querySelector('[value=pinned]').textContent=named?`Pin published v${publishedVersion}`:'Pinned version';
 $('named-sql-example').hidden=!named;$('sql-name-prompt').hidden=named;
 const note=$('sql-usage-note');
 if(mode.value==='latest')note.textContent=`Resolves the latest published version (now v${publishedVersion}). Uses the current Source SQL and row limit. ${dirty||version!==publishedVersion?'Draft edits are separate until published.':''}`;
 else note.textContent=`Uses published v${publishedVersion} with the current Source SQL and row limit, even after a newer version is published.`;
 namedEditor.setValue(sqlToCopy()||(sqlError?`-- ${sqlError}`:'-- Preparing named SQL…'));
 inlineEditor.setValue(inlineSql||(sqlError?`-- ${sqlError}`:'-- Preparing inline SQL…'));
 queryEditor.setValue(compiledSql||(sqlError?`-- ${sqlError}`:'-- Preparing full query…'));
 $('copy-sql').disabled=!sqlToCopy();$('copy-inline-sql').disabled=!inlineSql;$('copy-preview-sql').disabled=!compiledSql;
}
function invalidate({tree=false}={}){
 generation++;preview=null;lastExecution=null;
 if(tree){cohortStale=true;dirty=true;scheduleSql();}
 paintReceipt();renderRows();status();scheduleLive();
}
function scheduleLive(){
 clearTimeout(liveTimer);
 if(!armed||!$('live-preview').checked||!rows.length)return;
 liveTimer=setTimeout(()=>{if(running)scheduleLive();else runRow();},500);
}
function remember(){undo.push(structuredClone(doc.tree));if(undo.length>40)undo.shift();}
function editTree(next){validateTree(next);remember();doc.tree=next;popover=null;editing=null;invalidate({tree:true});renderGraph();}
function updateNode(path,fn){const n=structuredClone(nodeAt(doc.tree,path));fn(n);editTree(replaceNode(doc.tree,path,n));}
function focusSnapshot(){const e=document.activeElement;if(!e?.id||!$('decision-graph').contains(e))return null;return {id:e.id,start:e.selectionStart,end:e.selectionEnd,scroll:e.scrollTop};}
function restoreFocus(f){if(!f)return;const e=$(f.id);if(!e)return;e.focus({preventScroll:true});if(f.start!==null&&typeof e.setSelectionRange==='function')e.setSelectionRange(f.start,f.end);e.scrollTop=f.scroll;}

function packageHtml(path){
 const packet=stepPackage(doc.tree,path,input(),decision());
 const explanation={pending:'Run this row to inspect its evaluated package.',unvisited:'This row took a different route. No request was sent to this node.',result:'This leaf returns the final label. It makes no additional model call.',evaluated:'Reconstructed from this row and its evaluated trace. Only earlier judgments enter this request.',stopped:'Evaluation stopped here without a successful answer. No evaluated request is shown.'}[packet.phase];
 return `<p class="package-origin">${override?'What-if preview':'Selected SQL row'} · ${esc(rowTitle(rows[selectedRow],selectedRow))}</p><p>${explanation}</p>
 <section><h4>${packet.phase==='result'?'Source row':'Source evidence'} <code>state.evidence</code></h4><dl class="package-fields">${Object.entries(packet.evidence).map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(summary(value))}</dd>`).join('')}</dl>${packet.missing.length?`<p class="package-warning">Missing: ${esc(packet.missing.join(', '))}</p>`:''}${packet.omitted.length?`<p>Not passed: ${packet.omitted.map(k=>`<code>${esc(k)}</code>`).join(', ')}</p>`:''}</section>
 <section><h4>Earlier answers <code>state.prior_judgments</code></h4>${packet.previous.length?packet.previous.map((step,i)=>`<div class="package-judgment"><button data-action="inspect-step" data-path="${esc(step.node)}" title="Inspect this earlier question">${i+1}. ${esc(step.question)}</button><div><strong>${esc(summary(step.answer))}</strong><span>score ${Number(step.score).toFixed(2)}</span><code>prior_judgments[${i}].answer</code></div></div>`).join(''):`<p>${packet.phase==='unvisited'?'No evaluated package for this route.':packet.phase==='pending'?'No evaluated history yet.':'None. This is the first question.'}</p>`}<p>Earlier answers are bundled separately, in path order. Field bindings select source fields; they do not select earlier answers. These judgments remain interpretations, not new evidence.</p></section>
 ${packet.result?`<section><h4>This step produced</h4><p><strong>${esc(summary(packet.result.answer))}</strong> → ${esc(packet.result.route)} · score ${Number(packet.result.score).toFixed(2)}. This answer is appended for the next step.</p></section>`:''}
 ${packet.request?`<details class="package-json"><summary>Full request to the decision operator</summary><pre>${esc(JSON.stringify(packet.request,null,2))}</pre></details>`:''}`;
}
function nodePopover(path,n){
 if(popover?.path!==path)return '';
 const close='<button data-action="close-popover" aria-label="Close node controls">×</button>';
 if(popover.kind==='package')return `<div class="node-popover package-popover" role="region" aria-label="Data at ${esc(path)}"><header><strong>Data at this step</strong>${close}</header><div class="package-content">${packageHtml(path)}</div></div>`;
 if(popover.kind==='split')return `<div class="node-popover"><header><strong>Add a decision here</strong>${close}</header><p>This outcome becomes a branch. Undo restores it.</p><footer><button data-action="split-binary">Yes / No split</button><button data-action="split-choice">Multiple routes</button></footer></div>`;
 if(popover.kind==='value'){
  const key=popover.key,value=input()[key];
  return `<div class="node-popover"><header><code>${esc(key)}</code>${close}</header><textarea id="${nodeId(path,'value')}" data-value-key="${esc(key)}" aria-label="Test value for ${esc(key)}" ${value===null?'disabled':''}>${esc(value===null?'':summary(value))}</textarea><label><input type="checkbox" data-null-key="${esc(key)}" ${value===null?'checked':''}> Value is null</label><p>What-if for this row. Editing changes the preview, not your database.${typeof value==='object'&&value!==null?' This field contains JSON.':''}</p><footer><button data-action="remove-binding" data-key="${esc(key)}" ${n.inputs?.length===1?'disabled':''}>Unbind from this question</button><button data-action="reset-value">Reset row</button></footer></div>`;
 }
 const keys=[...new Set([...(n.inputs||[]),...Object.keys(input())])];
 return `<div class="node-popover"><header><strong>Read fields from this row</strong>${close}</header><div class="binding-options">${keys.map(k=>`<label><input type="checkbox" data-bind-key="${esc(k)}" ${n.inputs?.includes(k)?'checked':''} ${n.inputs?.length===1&&n.inputs[0]===k?'disabled':''}><code>${esc(k)}</code></label>`).join('')}</div><div class="binding-add"><input id="${nodeId(path,'new-field')}" aria-label="Required field name" maxlength="80" placeholder="Another field name"><button data-action="bind-field">Bind</button></div><footer><button data-action="all-fields">Read whole row</button></footer><p>Only bound fields reach this question. A missing field stops the path; null is a supplied value.</p></div>`;
}
function nodeHtml(item,index){
 const {path,node:n,x,y,width,height}=item,d=decision(),active=tracedPaths(d),step=d?.path?.find(s=>s.node===path);
 const selected=active.has(path),route=step?.route,fields=n.inputs||Object.keys(input()).slice(0,1);
 const title=n.kind==='leaf'?'Return':n.kind==='branch'?'Decide · yes / no':'Decide · choose a route';
 return `<article class="logic-node ${n.kind==='leaf'?'outcome':''} ${selected?'taken':d?'skipped':''} ${popover?.path===path?'popover-open':''}" data-node="${esc(path)}" aria-label="${esc(title)} ${index+1}" style="left:${x}px;top:${y}px;width:${width}px">
  <header class="node-heading"><span>${String(index+1).padStart(2,'0')} · ${title}</span><span class="node-verdict">${step?esc(route):selected?'Reached':''}</span></header>
  ${n.kind==='leaf'?`<textarea class="node-text outcome-text" id="${nodeId(path,'label')}" data-edit="label" aria-label="Outcome ${esc(path)}" maxlength="100">${esc(n.label)}</textarea>${actionHtml(n)}<footer class="node-footer"><span>${selected?'Result for this row':''}</span><button data-action="package">Data at step ↗</button><button data-action="split">+ Split here</button></footer>`:`
  <textarea class="node-text node-question" id="${nodeId(path,'question')}" data-edit="question" aria-label="Question ${esc(path)}" maxlength="1800">${esc(n.question)}</textarea>
  <div class="node-fields">${fields.map(k=>`<button class="field-value ${Object.hasOwn(input(),k)?'':'missing'}" data-action="value" data-key="${esc(k)}" title="${esc(k+': '+summary(input()[k]))}"><code>${esc(k)}</code><span>${esc(summary(input()[k]))}</span></button>`).join('')||'<span class="quiet">No source row selected</span>'}</div>
  <div class="node-binding-tools"><button data-action="bindings">${n.inputs?'+ Bind fields':'Whole row · choose fields'}</button><button data-action="package" aria-label="Data at ${esc(path)}" aria-expanded="${popover?.kind==='package'&&popover.path===path}">Data at step ↗</button><span class="node-score">${step?`score ${Number(step.score).toFixed(2)}`:''}</span></div>
  ${n.kind==='branch'?`<label class="node-threshold">Yes above <input id="${nodeId(path,'threshold')}" data-edit="threshold" aria-label="Threshold ${esc(path)}" type="range" min="0" max="1" step=".01" value="${n.threshold??.5}"><output>${(n.threshold??.5).toFixed(2)}</output></label>`:''}
  <div class="node-ports">${n.kind==='choice'?Object.entries(n.choices).map(([key,desc])=>`<div class="choice-port ${route===key?'taken':''}" data-route="${esc(key)}"><input class="route-key" id="${nodeId(path,'route-'+key)}" data-route-key="${esc(key)}" title="${esc(key)}" aria-label="Route name ${esc(path+'/'+key)}" value="${esc(key)}" maxlength="80"><textarea class="node-text route-description" id="${nodeId(path,'description-'+key)}" data-choice="${esc(key)}" aria-label="Route description ${esc(path+'/'+key)}" maxlength="1000">${esc(desc)}</textarea><button data-action="remove-route" data-key="${esc(key)}" aria-label="Remove route ${esc(key)}" ${Object.keys(n.choices).length<=2?'disabled':''}>×</button></div>`).join(''):['yes','no'].map(key=>`<div class="binary-port ${route===key?'taken':''}" data-route="${key}">${key==='yes'?'Yes':'No'}${route===key?' ← this row':''}</div>`).join('')}</div>
  <footer class="node-footer"><button data-action="prune">Remove split</button>${n.kind==='choice'&&Object.keys(n.choices).length<8?'<button data-action="add-route">+ Route</button>':'<span>SQL decision</span>'}</footer>`}
  ${nodePopover(path,n)}
 </article>`;
}
function renderGraph(){
 actionEditors.forEach(e=>e.destroy());actionEditors=[];
 const focus=focusSnapshot(),graph=layout(doc.tree),active=tracedPaths(decision());
 $('decision-graph').style.width=graph.width+'px';$('decision-graph').style.height=graph.height+'px';
 $('decision-graph').innerHTML=`<svg width="${graph.width}" height="${graph.height}" aria-hidden="true">${graph.edges.map(e=>{const x=(e.x1+e.x2)/2;return `<path data-edge="${esc(e.to)}" class="graph-edge ${active.has(e.to)?'taken':decision()?'dim':''}" d="M ${e.x1} ${e.y1} C ${x} ${e.y1}, ${x} ${e.y2}, ${e.x2} ${e.y2}"/>`;}).join('')}</svg>`+graph.nodes.map(nodeHtml).join('');
 $('tree-count').textContent=`${graph.nodes.filter(n=>n.node.kind!=='leaf').length} decisions · ${graph.nodes.filter(n=>n.node.kind==='leaf').length} outcomes`;
 actionEditors=mountActionEditors($('decision-graph'),path=>nodeAt(doc.tree,path),(path,sql)=>{if(editing!==path+'/action'){remember();editing=path+'/action';}nodeAt(doc.tree,path).action.sql=sql;invalidate({tree:true});});
 reflowGraph(false);restoreFocus(focus);paintReceipt();
}
function reflowGraph(keepFocus=true){
 cancelAnimationFrame(reflowFrame);
 const elements=[...$('decision-graph').querySelectorAll('[data-node]')],anchor=keepFocus?document.activeElement?.closest('[data-node]'):null,oldTop=anchor?.offsetTop;
 // Measure natural text at the actual font/width, including newlines. Reflow
 // positions and edge ports without replacing controls or moving the caret.
 for(const el of $('decision-graph').querySelectorAll('.node-text'))el.style.height='0px';
 const textSizes=[...$('decision-graph').querySelectorAll('.node-text')].map(el=>[el,el.scrollHeight+2]);
 for(const [el,height] of textSizes)el.style.height=height+'px';
 const measured=Object.fromEntries(elements.map(el=>[el.dataset.node,{height:el.offsetHeight,ports:Object.fromEntries([...el.querySelectorAll('[data-route]')].map(p=>[pointer(p.dataset.route),p.offsetTop+p.offsetHeight/2]))}]));
 graphLayout=layout(doc.tree,measured);
 for(const n of graphLayout.nodes){const el=elements.find(e=>e.dataset.node===n.path);el.style.left=n.x+'px';el.style.top=n.y+'px';}
 const svg=$('decision-graph').querySelector('svg');svg.setAttribute('width',graphLayout.width);svg.setAttribute('height',graphLayout.height);
 const edges=[...svg.querySelectorAll('[data-edge]')];
 for(const e of graphLayout.edges){const x=(e.x1+e.x2)/2;edges.find(p=>p.dataset.edge===e.to)?.setAttribute('d',`M ${e.x1} ${e.y1} C ${x} ${e.y1}, ${x} ${e.y2}, ${e.x2} ${e.y2}`);}
 $('decision-graph').style.width=graphLayout.width+'px';$('decision-graph').style.height=graphLayout.height+'px';
 applyZoom();if(anchor)$('graph-viewport').scrollTop+=(anchor.offsetTop-oldTop)*zoom;
}
function scheduleReflow(){cancelAnimationFrame(reflowFrame);reflowFrame=requestAnimationFrame(()=>reflowGraph());}
function paintReceipt(){
 const d=decision(),active=tracedPaths(d);
 for(const el of $('decision-graph').querySelectorAll('[data-node]')){
  const path=el.dataset.node,step=d?.path?.find(s=>s.node===path);el.classList.toggle('taken',active.has(path));el.classList.toggle('skipped',!!d&&!active.has(path));
  const verdict=el.querySelector('.node-verdict');if(verdict)verdict.textContent=step?.route||(active.has(path)?'Reached':'');
  const score=el.querySelector('.node-score');if(score)score.textContent=step?`score ${Number(step.score).toFixed(2)}`:'';
  for(const port of el.querySelectorAll('[data-route]')){const taken=step?.route===port.dataset.route;port.classList.toggle('taken',taken);if(port.classList.contains('binary-port'))port.textContent=(port.dataset.route==='yes'?'Yes':'No')+(taken?' ← this row':'');}
  const outcomeNote=el.classList.contains('outcome')?el.querySelector('.node-footer>span'):null;if(outcomeNote)outcomeNote.textContent=active.has(path)?'Result for this row':'';
  const actionState=el.querySelector('.action-run-state');if(actionState)actionState.textContent=lastExecution?.action?.node===path?`Last execution: ${lastExecution.action.status}`:active.has(path)?'Ready to execute for this row':'';
  for(const field of el.querySelectorAll('.field-value')){field.classList.toggle('missing',!Object.hasOwn(input(),field.dataset.key));field.querySelector('span').textContent=summary(input()[field.dataset.key]);field.title=field.dataset.key+': '+summary(input()[field.dataset.key]);}
 }
 for(const edge of $('decision-graph').querySelectorAll('[data-edge]')){edge.classList.toggle('taken',active.has(edge.dataset.edge));edge.classList.toggle('dim',!!d&&!active.has(edge.dataset.edge));}
 $('row-outcome').textContent=running?'Evaluating in PostgreSQL…':d?.status==='complete'?(override?'What-if → ':'')+d.label:d?.status==='missing_input'?`Missing ${d.missing_inputs.join(', ')} at ${d.stopped_at}`:d?`Stopped: ${d.status}`:armed?'Preview needs evaluation':'Run the source rows to see their paths.';
 $('reset-row').hidden=!override;
 paintUsage();
 const packageBody=$('decision-graph').querySelector('.package-content');if(packageBody){const expanded=packageBody.querySelector('details')?.open;packageBody.innerHTML=packageHtml(popover.path);if(expanded&&packageBody.querySelector('details'))packageBody.querySelector('details').open=true;applyZoom();}
}
function applyZoom(){
 const graph=graphLayout||layout(doc.tree),popup=$('decision-graph').querySelector('.node-popover'),host=popup?.closest('[data-node]');
 const width=Math.max(graph.width,popup?host.offsetLeft+popup.offsetLeft+popup.offsetWidth+28:0),height=Math.max(graph.height,popup?host.offsetTop+popup.offsetTop+popup.offsetHeight+24:0);
 $('decision-graph').style.transform=`scale(${zoom})`;$('graph-size').style.width=width*zoom+'px';$('graph-size').style.height=height*zoom+'px';$('zoom-reset').textContent=Math.round(zoom*100)+'%';
}
function setZoom(next){const viewport=$('graph-viewport'),old=zoom;zoom=Math.max(.55,Math.min(1.4,next));const x=(viewport.scrollLeft+viewport.clientWidth/2)/old,y=(viewport.scrollTop+viewport.clientHeight/2)/old;applyZoom();viewport.scrollLeft=x*zoom-viewport.clientWidth/2;viewport.scrollTop=y*zoom-viewport.clientHeight/2;}
function renderRows(){
 const available=new Set(rows.flatMap(r=>Object.keys(r.input)));
 const fields=[...new Set(['request_id','case_name',...layout(doc.tree).nodes.flatMap(n=>n.node.inputs||[]),...available])].filter(k=>available.has(k)).slice(0,24);
 $('row-select').innerHTML=rows.map((r,i)=>`<option value="${i}">${i+1} · ${esc(rowTitle(r,i))}</option>`).join('');$('row-select').value=String(selectedRow);
 $('row-count').textContent=rows.length?`· ${rows.length}`:'';
 $('previous-row').disabled=selectedRow===0;$('next-row').disabled=selectedRow>=rows.length-1;
 $('row-table').innerHTML=rows.length?`<table><thead><tr><th>Row</th><th>Outcome</th><th>Decision cost</th><th>RVBBIT cache</th>${fields.map(k=>`<th draggable="true" data-field="${esc(k)}" title="Drag ${esc(k)} onto a question">${esc(k)}</th>`).join('')}</tr></thead><tbody>${rows.map((r,i)=>`<tr data-row="${i}" class="${i===selectedRow?'selected':''}"><td><button aria-label="Inspect row ${i+1}">${i+1}</button></td><td class="result-cell ${cohortStale?'stale':''}">${cohortStale?'— run SQL':esc(r.decision?.label||r.decision?.status||'No decision')}</td><td class="usage-cell"><button data-usage-row="${i}" aria-label="Decision costs for row ${i+1}" ${r.usageId?'':'disabled'}>${r.usageId?'Pending':'—'}</button></td><td data-cache-row="${i}">—</td>${fields.map(k=>`<td title="${esc(summary(r.input[k]))}">${esc(summary(r.input[k]))}</td>`).join('')}</tr>`).join('')}</tbody></table>`:'<p class="quiet">Run a source query to bring rows into the graph.</p>';
 paintUsage();
}

function paintUsage(){
 for(const button of $('row-table').querySelectorAll('[data-usage-row]')){
  const i=Number(button.dataset.usageRow),r=rows[i],cost=costs.get(r?.usageId)?.rows[r?.usageRow-1];
  button.textContent=cost?costLabel(cost):'—';button.disabled=!cost;
  $('row-table').querySelector(`[data-cache-row="${i}"]`).textContent=cost?cacheLabel(cost):'—';
 }
 const batch=costs.get(batchUsage);$('batch-cost').hidden=!batch;
 if(batch)$('batch-cost').textContent=`Batch: ${batch.pending?(batch.known_usd?money(batch.known_usd)+' + pending':'cost pending'):money(batch.known_usd)} · ${batch.decisions} decisions · ${batch.cached} cached`;
 const selected=preview?.usage?{id:preview.usage.id,row:1}:!cohortStale&&rows[selectedRow]?.usageId?{id:rows[selectedRow].usageId,row:rows[selectedRow].usageRow}:null;
 const selectedCost=selected&&costs.get(selected.id)?.rows[selected.row-1];
 $('row-cost').hidden=!selectedCost;$('row-cost').textContent=selectedCost?`${preview?'Preview · ':''}${costLabel(selectedCost)} · ${cacheLabel(selectedCost)}`:'';
 $('row-cost').onclick=()=>openUsage(selected,override?'What-if preview':'Selected row');
 if(usageFocus&&$('usage-dialog').open)$('usage-content').innerHTML=usageDetail(costs.get(usageFocus.id)?.rows[usageFocus.row-1]);
}
function openUsage(ref,title){usageFocus=ref;$('usage-title').textContent=title+' · decision costs';$('usage-content').innerHTML=usageDetail(costs.get(ref.id)?.rows[ref.row-1]);$('usage-dialog').showModal();}
$('close-usage').onclick=()=>{$('usage-dialog').close();usageFocus=null;};
$('row-table').addEventListener('click',e=>{const button=e.target.closest('[data-usage-row]');if(!button)return;const i=Number(button.dataset.usageRow),row=rows[i];openUsage({id:row.usageId,row:row.usageRow},'Row '+(i+1));});

function selectRow(index){lastExecution=null;selectedRow=Math.max(0,Math.min(rows.length-1,index));override=null;popover=null;preview=null;generation++;renderRows();renderGraph();if(cohortStale)scheduleLive();else $('decision-graph').querySelector('.outcome.taken')?.scrollIntoView({block:'nearest',inline:'nearest'});}
async function runRow(){
 clearTimeout(liveTimer);if(running||!rows.length)return;
 try{validateTree(doc.tree);}catch(error){$('row-outcome').textContent=error.message;return;}
 const token=++generation;running=true;armed=true;preview=null;status();paintReceipt();
 try{const result=await api('/test',{context:input(),tree:doc.tree});if(token===generation){preview=result;costs.track(result.usage);}}
 catch(error){if(token===generation)notice(error.message);}
 finally{running=false;status();paintReceipt();if(token!==generation)scheduleLive();}
}
function queryBody(run=false){if(!$('row-limit').checkValidity())throw Error(`Choose a whole number from 1 to ${Number($('row-limit').max).toLocaleString()}.`);return {...(!dirty&&publishedVersion===version&&savedKey?{rule:savedKey,version}:{tree:doc.tree}),source_sql:doc.source_sql,limit:Number($('row-limit').value),run};}
function scheduleSql(){compiledSql='';inlineSql='';sourceClauseSql='';sqlError='';renderSqlUsage();queryGeneration++;clearTimeout(sqlTimer);sqlTimer=setTimeout(()=>refreshSql(),450);}
async function refreshSql(){const token=++queryGeneration;try{const data=await api('/sql',queryBody());if(token===queryGeneration){compiledSql=data.sql;inlineSql=data.inline_sql;sourceClauseSql=data.source_clause_sql;sqlError='';renderSqlUsage();}}catch(error){if(token===queryGeneration){sqlError=error.message;renderSqlUsage();}}}
async function runSql(){
 clearTimeout(liveTimer);if(running||!$('row-limit').reportValidity())return;
 try{validateTree(doc.tree);}catch(error){notice(error.message);return;}
 const token=++generation;running=true;preview=null;override=null;status();paintReceipt();$('query-status').textContent='Running…';
 try{
  const result=await api('/sql',queryBody(true));
  if(token!==generation){notice('The definition changed while SQL was running. Run it again to evaluate the new version.');return;}
  const data=result.result;compiledSql=result.sql;inlineSql=result.inline_sql;sourceClauseSql=result.source_clause_sql;sqlError='';renderSqlUsage();
  costs.clear();batchUsage=data.usage?.id||null;
  lastReceipt=data.business_logic_receipt||null;$('inspect-receipt').hidden=!lastReceipt;
  rows=data.rows.map(r=>Array.isArray(r)?Object.fromEntries(data.columns.map((c,i)=>[c.name,r[i]])):r).map((r,i)=>({input:r.input,decision:r.decision,usageId:data.usage?.id,usageRow:i+1}));
  costs.track(data.usage);
  selectedRow=Math.min(selectedRow,Math.max(0,rows.length-1));cohortStale=false;armed=true;
  $('query-status').textContent=`${rows.length} rows · ${data.elapsed_ms} ms`;
  $('rows-hint').textContent='Evaluated by PostgreSQL. Select a row to inspect its path; click a field inside a node to try a what-if.';
  showTab('rows');renderRows();renderGraph();
 }catch(error){notice(error.message);$('query-status').textContent='Query failed';}
 finally{running=false;status();paintReceipt();}
}
function showTab(name){workspace.expand();for(const key of ['rows','source','sql']){$('panel-'+key).hidden=key!==name;$('tab-'+key).setAttribute('aria-selected',String(key===name));}sourceEditor.measure();queryEditor.measure();namedEditor.measure();inlineEditor.measure();if(name==='sql')refreshSql();}
function renderLibrary(){const q=$('find-decision').value.toLowerCase();$('decision-list').innerHTML=items.filter(i=>(i.name+' '+i.topic).toLowerCase().includes(q)).map(i=>`<button data-open="${esc(i.id)}"><strong>${esc(i.name)}</strong><small>${esc(i.topic||'No topic')} · v${i.version}</small></button>`).join('')||'<p class="quiet">No saved decisions yet.</p>';}
async function refreshLibrary(){const r=await api();items=r.items;renderLibrary();}
function loadDocument(next,record=null){
 costs.clear();batchUsage=null;usageFocus=null;$('usage-dialog').close();
 lastExecution=null;executionRequest=null;
 clearTimeout(liveTimer);doc=structuredClone(next);doc.source_sql ||= sourceSql(doc.examples||[]);id=record?.id||null;version=record?.current_version??record?.version??0;dirty=!!record?.current_version&&record.current_version!==record.version;undo=[];popover=null;override=null;preview=null;armed=false;cohortStale=true;generation++;
 rows=(doc.examples||[]).map((e,i)=>({input:rowContext(e,i),expected:e.expected}));selectedRow=0;
 publishedVersion=record?.published_version||null;savedKey=record?.rule_key||null;viewedVersion=record?.version||null;lastReceipt=null;$('inspect-receipt').hidden=true;capability.load(record);
 $('decision-name').value=doc.name;$('decision-topic').value=doc.topic;sourceEditor.setValue(doc.source_sql);
 $('version-select').innerHTML=record?(record.versions||[]).map(v=>`<option value="${v.version}">v${v.version} · ${esc(new Date(v.created_at).toLocaleString())}</option>`).join(''):'<option>Not saved yet</option>';if(record)$('version-select').value=String(record.version);
 $('query-status').textContent='';$('sql-view').value='latest';$('rows-hint').textContent=rows.length?'Saved example values · Run SQL to evaluate the source rows in PostgreSQL.':'Edit Source SQL, then run it to bring rows into the graph.';
 $('logic-library').hidden=true;$('toggle-library').setAttribute('aria-expanded','false');status();renderRows();renderGraph();renderLibrary();scheduleSql();history.replaceState(null,'',id?'?id='+id:location.pathname);
 $('graph-viewport').scrollTop=0;$('graph-viewport').scrollLeft=0;
}
function canLeave(){return !dirty||confirm('Discard unsaved changes to this decision?');}
async function open(logicId,v){try{const record=await api('/'+encodeURIComponent(logicId)+(v?'?version='+v:''));loadDocument(record.definition,record);}catch(error){notice(error.message);}}

$('decision-graph').addEventListener('focusout',()=>{editing=null;});
$('decision-graph').addEventListener('input',e=>{
 const host=e.target.closest('[data-node]');if(!host)return;const path=host.dataset.node;
 if(e.target.hasAttribute('data-value-key')){
  const key=e.target.dataset.valueKey,old=input()[key];let value=e.target.value;
  if(typeof old==='object'&&old!==null){try{value=JSON.parse(value);}catch{return;}}
  else if(typeof old==='number'){value=Number(value);if(!Number.isFinite(value))return;}
  else if(typeof old==='boolean'){if(!['true','false'].includes(value))return;value=value==='true';}
  override={...input(),[key]:value};invalidate();return;
 }
 const n=structuredClone(nodeAt(doc.tree,path));
 if(e.target.dataset.actionEdit){n.action[e.target.dataset.actionEdit]=e.target.value;}
 else if(e.target.dataset.edit){const key=e.target.dataset.edit;n[key]=key==='threshold'?Number(e.target.value):e.target.value;if(key==='threshold')e.target.nextElementSibling.value=n[key].toFixed(2);}
 else if(e.target.hasAttribute('data-choice'))n.choices[e.target.dataset.choice]=e.target.value;
 else return;
 if(editing!==e.target.id){remember();editing=e.target.id;}
 doc.tree=replaceNode(doc.tree,path,n);invalidate({tree:true});scheduleReflow();
});
$('decision-graph').addEventListener('change',e=>{
 const host=e.target.closest('[data-node]');if(!host)return;const path=host.dataset.node;
 try{
  if(e.target.hasAttribute('data-action-effects'))updateNode(path,n=>{n.action.external_effects=e.target.checked?'may_write':'read_only';});
  else if(e.target.hasAttribute('data-bind-key'))updateNode(path,n=>{const key=e.target.dataset.bindKey;n.inputs=e.target.checked?[...(n.inputs||[]),key]:n.inputs.filter(k=>k!==key);});
  else if(e.target.hasAttribute('data-null-key')){const key=e.target.dataset.nullKey;override={...input(),[key]:e.target.checked?null:''};invalidate();renderGraph();}
  else if(e.target.hasAttribute('data-route-key')){const n=nodeAt(doc.tree,path);editTree(replaceNode(doc.tree,path,replaceRoute(n,e.target.dataset.routeKey,e.target.value)));}
 }catch(error){notice(error.message);renderGraph();}
});
$('decision-graph').addEventListener('click',e=>{
 const button=e.target.closest('[data-action]'),host=e.target.closest('[data-node]');if(!button||!host)return;
 const path=host.dataset.node,n=nodeAt(doc.tree,path),action=button.dataset.action,key=button.dataset.key;
 try{
  if(action==='add-sql-action')updateNode(path,n=>{n.action={kind:'sql',label:'',sql:'UPDATE calliope_apps.your_table\nSET status = $1->\'decision\'->>\'label\'\nWHERE id = ($1->\'input\'->>\'id\')::uuid\nRETURNING *;',external_effects:'may_write'};});
  else if(action==='add-callie-action')updateNode(path,n=>{n.action={kind:'callie',label:'Ask Callie',instruction:'Review this request using the supplied source row and decision. Summarize your findings in this conversation.'};});
  else if(action==='remove-action')updateNode(path,n=>{delete n.action;});
  else if(action==='close-popover')popover=null;
  else if(['split','bindings','value','package'].includes(action))popover={kind:action,path,key};
  else if(action==='inspect-step')popover={kind:'package',path:button.dataset.path};
  else if(action==='split-binary')editTree(replaceNode(doc.tree,path,{...binary(),yes:structuredClone(n)}));
  else if(action==='split-choice'){const next=choice();next.routes.first=structuredClone(n);editTree(replaceNode(doc.tree,path,next));}
  else if(action==='prune')editTree(replaceNode(doc.tree,path,leaf()));
  else if(action==='add-route')updateNode(path,n=>{let name='route';for(let i=2;Object.hasOwn(n.routes,name);i++)name='route_'+i;n.choices[name]='Describe this route';n.routes[name]=leaf();});
  else if(action==='remove-route')updateNode(path,n=>{delete n.choices[key];delete n.routes[key];});
  else if(action==='bind-field'){const key=$(nodeId(path,'new-field')).value;updateNode(path,n=>{n.inputs=[...new Set([...(n.inputs||[]),key])];});}
  else if(action==='remove-binding')updateNode(path,n=>{n.inputs=n.inputs.filter(k=>k!==key);});
  else if(action==='all-fields')updateNode(path,n=>{delete n.inputs;});
  else if(action==='reset-value'){override=null;popover=null;invalidate();}
  renderGraph();if(popover?.kind==='package')$('decision-graph').querySelector('.package-popover')?.scrollIntoView({block:'nearest',inline:'nearest'});
 }catch(error){notice(error.message);}
});
$('row-table').addEventListener('dragstart',e=>{const field=e.target.closest('[data-field]');if(field)e.dataTransfer.setData('application/x-calliope-field',field.dataset.field);});
$('decision-graph').addEventListener('dragover',e=>{const node=e.target.closest('[data-node]');if(node&&nodeAt(doc.tree,node.dataset.node).kind!=='leaf'){e.preventDefault();node.classList.add('drop-target');}});
$('decision-graph').addEventListener('dragleave',e=>e.target.closest('[data-node]')?.classList.remove('drop-target'));
$('decision-graph').addEventListener('drop',e=>{const host=e.target.closest('[data-node]'),key=e.dataTransfer.getData('application/x-calliope-field');if(!host||!Object.hasOwn(input(),key))return;e.preventDefault();try{updateNode(host.dataset.node,n=>{n.inputs=[...new Set([...(n.inputs||[]),key])];});}catch(error){notice(error.message);}});
$('zoom-in').onclick=()=>setZoom(zoom+.1);$('zoom-out').onclick=()=>setZoom(zoom-.1);$('zoom-reset').onclick=()=>setZoom(1);$('zoom-fit').onclick=()=>{const g=graphLayout||layout(doc.tree),v=$('graph-viewport');setZoom(Math.min(1,(v.clientWidth-30)/g.width,(v.clientHeight-30)/g.height));};
$('row-select').onchange=e=>selectRow(Number(e.target.value));$('previous-row').onclick=()=>selectRow(selectedRow-1);$('next-row').onclick=()=>selectRow(selectedRow+1);$('row-table').onclick=e=>{if(e.target.closest('[data-usage-row]'))return;const r=e.target.closest('[data-row]');if(r)selectRow(Number(r.dataset.row));};
$('reset-row').onclick=()=>{override=null;popover=null;invalidate();renderGraph();};$('live-preview').onchange=()=>scheduleLive();$('run-row').onclick=runRow;$('run-sql').onclick=runSql;
$('row-limit').oninput=()=>status();
$('row-limit').onchange=()=>{status();if(!$('row-limit').reportValidity())return;generation++;cohortStale=true;scheduleSql();renderRows();};
for(const tab of document.querySelectorAll('[data-tab]'))tab.onclick=()=>showTab(tab.dataset.tab);$('show-sql').onclick=()=>showTab('sql');
$('sql-view').onchange=()=>renderSqlUsage();
$('sql-preview').ontoggle=()=>queryEditor.measure();
$('setup-sql-name').onclick=()=>$('toggle-capability').click();
for(const [button,read,label] of [['copy-sql',sqlToCopy,'Named SQL'],['copy-inline-sql',()=>inlineSql,'Inline SQL'],['copy-preview-sql',()=>compiledSql,'Full query']]){
 $(button).onclick=async()=>{try{const sql=read();if(!sql)throw Error('Finish the definition and source query first.');await navigator.clipboard.writeText(sql);notice(label+' copied.');}catch(error){notice(error.message);}};
}
$('decision-name').oninput=e=>{doc.name=e.target.value;dirty=true;status();};$('decision-topic').oninput=e=>{doc.topic=e.target.value;dirty=true;status();};
$('undo-tree').onclick=()=>{if(!undo.length)return;doc.tree=undo.pop();popover=null;editing=null;invalidate({tree:true});renderGraph();};
$('toggle-library').onclick=()=>{$('logic-library').hidden=!$('logic-library').hidden;$('toggle-library').setAttribute('aria-expanded',String(!$('logic-library').hidden));};$('find-decision').oninput=renderLibrary;
async function loadPreset(next){if(!canLeave())return;try{await refreshLibrary();const saved=items.find(item=>item.rule_key===next.key);if(saved)await open(saved.id);else loadDocument(next);}catch(error){notice(error.message);}}
$('load-example').onclick=()=>loadPreset(example());$('new-decision').onclick=()=>{if(canLeave())loadDocument(blank());};$('decision-list').onclick=e=>{const b=e.target.closest('[data-open]');if(b&&canLeave())open(b.dataset.open);};$('version-select').onchange=e=>{if(id&&canLeave())open(id,Number(e.target.value));};
async function executeSelected(){
 if(executing||running||!rows.length)return;
 executing=true;status();
 try{
  if(dirty||!id)await saveDocument();
  if(dirty)throw Error('The definition changed while saving. Finish editing before execution.');
  const body={id,version:viewedVersion||version,context:structuredClone(input())},fingerprint=JSON.stringify(body);
  if(executionRequest?.fingerprint!==fingerprint)executionRequest={fingerprint,key:crypto.randomUUID()};
  const result=await api('/execute',{...body,request_key:executionRequest.key});
  lastExecution=result;lastReceipt=result.receipt;$('inspect-receipt').hidden=false;
  if(JSON.stringify(input())===JSON.stringify(body.context)){preview={decision:result.decision};paintReceipt();}
  notice(result.receipt.status==='complete'?(result.action?.status==='queued'?'Callie task queued. Open the receipt for its conversation.':result.action?.status==='none'?'This outcome has no action.':'Action completed. Its output is in the receipt.'):(result.receipt.error||`Execution ${result.receipt.status}`));
  await capability.openReceipt(result.receipt.id);
  executionRequest=null;
 }catch(error){notice(error.message);}
 finally{executing=false;status();}
}
async function saveDocument(){if(saving)throw Error('A save is already in progress.');validateTree(doc.tree);saving=true;status();const snapshot=JSON.stringify(doc);try{const record=await api('',{id,version,definition:doc});id=record.id;version=record.version;viewedVersion=version;if(JSON.stringify(doc)===snapshot){dirty=false;doc=structuredClone(record.definition);}publishedVersion=record.published_version;savedKey=record.rule_key;capability.load(record);history.replaceState(null,'','?id='+id);$('version-select').innerHTML=record.versions.map(v=>`<option value="${v.version}">v${v.version} · ${esc(new Date(v.created_at).toLocaleString())}</option>`).join('');$('version-select').value=String(version);await refreshLibrary();scheduleSql();notice(`Saved version ${version}, including its source query.`);return record;}finally{saving=false;status();}}
$('load-action-example').onclick=()=>loadPreset(actionExample());
$('load-classifier-example').onclick=()=>loadPreset(classifierExample());
 $('execute-row').onclick=executeSelected;
 $('save-decision').onclick=()=>saveDocument().catch(error=>notice(error.message));
$('inspect-receipt').onclick=()=>{if(lastReceipt)capability.openReceipt(lastReceipt.id);};
$('toggle-json').onclick=()=>{definitionEditor.setValue(JSON.stringify(doc.tree,null,2));$('definition-dialog').showModal();definitionEditor.measure();};$('close-definition').onclick=()=>$('definition-dialog').close();
function applyDefinition(){try{editTree(JSON.parse(definitionEditor.getValue()));$('definition-dialog').close();}catch(error){notice(error.message);}}
$('apply-json').onclick=applyDefinition;
window.addEventListener('keydown',e=>{if(e.defaultPrevented)return;if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();if($('definition-dialog').open)applyDefinition();else if($('source-sql').contains(document.activeElement))runSql();else runRow();}if(e.key==='Escape'&&popover){popover=null;renderGraph();}});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
const initialParams=new URLSearchParams(location.search),initialId=initialParams.get('id');loadDocument(initialId?blank():example());refreshLibrary().catch(error=>notice(error.message));if(initialId)open(initialId,initialParams.get('version'));if(initialParams.get('run'))capability.openReceipt(initialParams.get('run'));
document.fonts.ready.then(scheduleReflow);document.fonts.addEventListener('loadingdone',scheduleReflow);
new MutationObserver(scheduleReflow).observe(document.documentElement,{attributes:true,attributeFilter:['class','style','data-warehouse-theme']});
window.addEventListener('resize',scheduleReflow);

// Presets are secondary to the current decision, in one compact collection menu.
const exampleMenu=document.querySelector('.studio-examples');
exampleMenu.addEventListener('click',e=>{if(e.target.closest('button'))exampleMenu.open=false;});
document.addEventListener('click',e=>{if(!exampleMenu.contains(e.target))exampleMenu.open=false;});
window.addEventListener('keydown',e=>{if(e.key==='Escape')exampleMenu.open=false;});
