import {actionReceiptHtml} from './business-logic-actions.mjs';
import {escapeHtml as esc,routes,stepPackage,namedSql} from './business-logic-model.mjs';
import {mountCodeEditor} from './business-logic-code-editor.mjs';
const $=id=>document.getElementById(id);
const text=v=>typeof v==='object'?JSON.stringify(v,null,2):String(v??'null');

export function contractFields(tree){
 const inputs=new Set(),outcomes=new Set();
 function visit(node){if(node.kind==='leaf')outcomes.add(node.label);else {for(const key of node.inputs||[])inputs.add(key);for(const [,child] of routes(node))visit(child);}}
 visit(tree);return {inputs:[...inputs].sort(),outcomes:[...outcomes].sort()};
}

export function mountCapability({get,api,notice,changed,save,published}){
 let record=null,receipt=null,exampleGeneration=0;
 const sql=mountCodeEditor($('rule-sql-example'),{label:'Published rule SQL example',readOnly:true});
 const tool=mountCodeEditor($('rule-tool-example'),{label:'Published rule tool example',language:'json',readOnly:true});
 async function examples(){
  const token=++exampleGeneration,current=get();let {doc,version}=current;
  sql.setValue('-- Preparing SQL with the current source query…');
  const v=record?.published_version||version||1;
  if(record?.published_version){const saved=await api('/'+encodeURIComponent(record.id)+'?version='+v);if(token!==exampleGeneration)return;doc=saved.definition;}
  const key=doc.key||'your.rule';
  tool.setValue(JSON.stringify({tool:'run_business_logic',arguments:{rule:key,context:Object.fromEntries([...new Set([...contractFields(doc.tree).inputs,...Object.keys(doc.inputs||{})])].map(k=>[k,`<${doc.inputs?.[k]?.description||k}>`]))}},null,2));
  try{
   const compiled=await api('/sql',{tree:doc.tree,source_sql:current.doc.source_sql,limit:current.limit});
   if(token!==exampleGeneration)return;
   sql.setValue(`-- Current published version (now v${v})\n${namedSql(key,compiled.source_clause_sql)}\n\n-- To pin v${v}, add it as the third argument:\n-- rvbbit.business_logic('${key.replaceAll("'","''")}', to_jsonb(r), ${v})`);
  }catch(error){if(token===exampleGeneration)sql.setValue(`-- ${error.message}`);throw error;}
 }
 function paint(){
  const {doc,dirty,version}=get();
  $('rule-key').value=doc.key||'';$('rule-key').readOnly=!!record?.rule_key;
  $('rule-description').value=doc.description||'';
  const fields=contractFields(doc.tree);doc.inputs||={};doc.outcomes||={};fields.inputs=[...new Set([...fields.inputs,...Object.keys(doc.inputs)])].sort();
  $('rule-inputs').innerHTML=fields.inputs.map(key=>{const spec=doc.inputs[key]||{type:'any',description:''};return `<div class="capability-field"><code>${esc(key)}</code><select data-input-type="${esc(key)}" aria-label="${esc(key)} type">${['any','string','number','boolean','object','array'].map(t=>`<option ${t===spec.type?'selected':''}>${t}</option>`).join('')}</select><input data-input-description="${esc(key)}" aria-label="${esc(key)} description" value="${esc(spec.description)}" maxlength="500" placeholder="What this field contains"></div>`;}).join('')||'<p class="quiet">Bind fields on the graph to give this rule an explicit input contract.</p>';
  $('rule-outcomes').innerHTML=fields.outcomes.map(label=>`<label class="capability-outcome"><code>${esc(label)}</code><input data-outcome="${esc(label)}" aria-label="${esc(label)} meaning" value="${esc(doc.outcomes[label]||'')}" maxlength="500" placeholder="What this outcome means (optional)"></label>`).join('');
  $('rule-available').checked=!!record?.published_version;
  $('publication-status').textContent=record?.published_version?`v${record.published_version} is callable${dirty||version!==record.published_version?' · Edits are a separate draft':''}`:'Not in the agent catalog';
  $('publish-revision').disabled=!dirty&&version===record?.published_version;
  $('test-published-rule').disabled=!record?.published_version;
  examples().catch(e=>notice(e.message));
 }
 async function publish(){
  $('publish-revision').disabled=true;$('rule-available').disabled=true;$('publication-status').textContent='Publishing saved revision…';
  try{if(get().dirty||!get().id)record=await save();if(!record)throw Error('Save a definition first.');
   record=await api('/publish',{id:get().id,version:get().version});published(record);paint();notice(`Version ${record.published_version} is available through SQL and Callie’s tools.`);
  }catch(e){notice(e.message);paint();}finally{$('rule-available').disabled=false;$('publish-revision').disabled=!get().dirty&&get().version===record?.published_version;}
 }
 $('toggle-capability').onclick=()=>{paint();$('capability-dialog').showModal();sql.measure();tool.measure();};
 $('close-capability').onclick=()=>$('capability-dialog').close();
 $('rule-key').oninput=e=>{get().doc.key=e.target.value;changed();examples().catch(e=>notice(e.message));};
 $('rule-description').oninput=e=>{get().doc.description=e.target.value;changed();};
 $('capability-dialog').addEventListener('input',e=>{
  const doc=get().doc,key=e.target.dataset.inputType||e.target.dataset.inputDescription;
  if(key){doc.inputs||={};doc.inputs[key]||={type:'any',description:''};doc.inputs[key][e.target.dataset.inputType?'type':'description']=e.target.value;changed();}
  if(e.target.dataset.outcome){doc.outcomes||={};doc.outcomes[e.target.dataset.outcome]=e.target.value;changed();}
 });
 $('save-rule-metadata').onclick=async()=>{try{record=await save();paint();}catch(e){notice(e.message);}};
 $('publish-revision').onclick=publish;
 $('rule-available').onchange=async e=>{if(e.target.checked)await publish();else try{record=await api('/publish',{id:get().id,version:null});published(record);paint();}catch(e){notice(e.message);paint();}};
 $('test-published-rule').onclick=async()=>{const button=$('test-published-rule');button.disabled=true;try{const result=await api('/run',{rule:record.rule_key,version:record.published_version,context:get().input});$('capability-dialog').close();await openReceipt(result.receipt.id);}catch(e){notice(e.message);}finally{button.disabled=false;}};
 async function openReceipt(id){try{receipt=await api('/receipts/'+encodeURIComponent(id));renderReceipt(0);$('receipt-dialog').showModal();history.replaceState(null,'','?'+(get().id?'id='+encodeURIComponent(get().id)+'&':'')+'run='+encodeURIComponent(id));}catch(e){notice(e.message);}}
 function renderReceipt(index){
  const rows=receipt.rows||[],row=rows[index],decision=row?.decision,rule=decision?.rule,definition=receipt.definitions?.[`${rule?.key}@${rule?.version}`]?.definition;
  $('receipt-title').textContent=rule?.name||'Business logic run';
  $('receipt-content').innerHTML=`<div class="receipt-overview"><strong>${esc(Object.entries(receipt.outcomes||{}).map(([label,count])=>`${label} · ${count}`).join(' / '))}</strong><span>${receipt.evaluations} evaluations · ${esc(receipt.status)} · ${receipt.elapsed_ms} ms</span><span>${esc(new Date(receipt.created_at).toLocaleString())} · ${esc(receipt.source)}</span></div>
    ${receipt.error?`<p class="package-warning">${esc(receipt.error)}</p>`:''}
    ${receipt.details_truncated?`<p class="quiet">Showing ${rows.length} captured evaluations. Outcome counts cover all ${receipt.evaluations} evaluations, including rows not returned by the SQL query.</p>`:''}
    <label class="receipt-row-picker">Inspect evaluation <select id="receipt-row">${rows.map((r,i)=>`<option value="${i}" ${i===index?'selected':''}>${r.row} · ${esc(r.input.request_id||r.input.id||r.input.case_name||'')} · ${esc(r.decision.label||r.decision.status)}</option>`).join('')}</select></label>
    ${row?`<div class="receipt-result"><strong>${esc(decision.label||decision.status)}</strong><a href="/meaning/logic?id=${encodeURIComponent(rule.id)}&version=${rule.version}" target="_blank" rel="noopener">${esc(rule.key)} · v${rule.version} ↗</a></div>
      ${decision.missing_inputs?`<p class="package-warning">Missing evidence: ${esc(decision.missing_inputs.join(', '))}</p>`:''}
      ${decision.input_errors?`<p class="package-warning">${esc(decision.input_errors.map(e=>`${e.field}: expected ${e.expected}, got ${e.actual}`).join('; '))}</p>`:''}
      <details class="receipt-source"><summary>Original input · frozen at execution</summary><dl class="package-fields">${Object.entries(row.input).map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(text(value))}</dd>`).join('')}</dl></details>
      <ol class="receipt-path">${(decision.path||[]).map((step,i)=>{
        const packet=definition?stepPackage(definition.tree,step.node,row.input,decision):null;
        return `<li><span class="receipt-step-number">${i+1}</span><div><strong>${esc(step.question)}</strong><p class="receipt-answer">${esc(text(step.answer))} <small>· score ${Number(step.score).toFixed(2)}</small></p><details><summary>Data at this step</summary><dl class="package-fields">${Object.entries(packet?.evidence||{}).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(text(v))}</dd>`).join('')}</dl>${packet?.previous?.length?`<p class="quiet">Earlier judgments: ${esc(packet.previous.map(s=>text(s.answer)).join(' → '))}</p>`:''}<p class="quiet">${esc(step.model||'')} · ${esc(step.contract||'')}</p></details></div></li>`;}).join('')}</ol>`:''}
    ${actionReceiptHtml(receipt.action)}
    ${receipt.sql?`<details class="receipt-source"><summary>Source SQL</summary><pre><code>${esc(receipt.sql)}</code></pre></details>`:''}
    <p class="quiet">This is the recorded run, not a re-evaluation. Scores are model scores, not calibrated confidence. ${receipt.source==='execute'?'Action execution is recorded above. A queued Callie task has its own conversation.':'No business actions were executed.'}</p>`;
  $('receipt-row')?.addEventListener('change',e=>renderReceipt(Number(e.target.value)));
 }
 $('close-receipt').onclick=()=>$('receipt-dialog').close();
 return {load:r=>{record=r;},openReceipt};
}
