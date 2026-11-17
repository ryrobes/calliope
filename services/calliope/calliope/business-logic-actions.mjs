import {escapeHtml as esc} from './business-logic-model.mjs';
import {mountCodeEditor} from './business-logic-code-editor.mjs';

export function actionHtml(node){
 const a=node.action;
 if(!a)return '<div class="outcome-action empty"><button data-action="add-sql-action">+ SQL action</button><button data-action="add-callie-action">+ Callie task</button></div>';
 return `<section class="outcome-action" aria-label="Outcome action"><header><span>${a.kind==='sql'?'SQL action':'Callie task'} · on explicit execution</span><button data-action="remove-action" aria-label="Remove outcome action">×</button></header>
 <input class="action-label" data-action-edit="label" aria-label="Action label" value="${esc(a.label||'')}" maxlength="100" placeholder="Name the action">
 ${a.kind==='sql'?`<div class="action-sql-editor logic-code-editor"></div><p><code>$1::jsonb</code> contains <code>input</code> and <code>decision</code>.</p><label class="action-effects"><input type="checkbox" data-action-effects ${a.external_effects!=='read_only'?'checked':''}> SQL may write to an external service</label>`:`<textarea class="node-text action-instruction" data-action-edit="instruction" aria-label="Callie task instruction" maxlength="8000">${esc(a.instruction)}</textarea><p>Callie receives this instruction, the source row and the recorded decision in a new conversation.</p>`}
 <div class="action-run-state" aria-live="polite"></div></section>`;
}

export function mountActionEditors(root,treeNode,changed){
 return [...root.querySelectorAll('.action-sql-editor')].map(host=>{
  const path=host.closest('[data-node]').dataset.node;
  return mountCodeEditor(host,{value:treeNode(path).action.sql,label:'Outcome action SQL '+path,onChange:sql=>changed(path,sql)});
 });
}

export function actionReceiptHtml(action){
 if(!action)return '';
 if(action.status==='none')return '<p class="quiet">This outcome has no action.</p>';
 const result=action.result;
 return `<section class="receipt-action"><header><strong>${esc(action.label)}</strong><span class="action-state">${esc(action.status)}</span></header>
 ${action.error?`<p class="package-warning">${esc(action.error)}</p>`:''}
 ${result?.url?`<a class="action-conversation" href="${esc(result.url)}" target="_blank" rel="noopener">Open Callie conversation ↗</a><p class="quiet">The task was queued. Follow the conversation for Callie’s progress and answer.</p>`:''}
 ${result?.rows?`<p>${result.results?.[0]?.rows_affected??result.rows.length} rows affected · ${esc(result.run_id||'')}</p>`:''}
 ${result?`<details ${result.rows?'open':''}><summary>Action output</summary><pre>${esc(JSON.stringify(result.rows||result,null,2))}</pre></details>`:''}
 <details><summary>Data sent to the action</summary><pre>${esc(JSON.stringify(action.input,null,2))}</pre></details></section>`;
}
