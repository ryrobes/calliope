import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Code, MagicWand, Play, TreeStructure } from '@phosphor-icons/react'
import type { SQLNamespace } from '@codemirror/lang-sql'
import { SqlEditor } from '@/components/desktop/sql-editor'
import { ExplainGraph, type ExplainRoot } from '@/components/desktop/explain-graph'
import { buildFunctionCompletionSource } from '@/lib/desktop/sql-completion'
import type { SemanticOpMeta } from '@/lib/desktop/types'
import type { SchemaFunction } from '@/lib/db/types'
import { formatDialect, postgresql } from 'sql-formatter'

export type SqlState = {text:string;title:string;resultSql:string;planSql:string;
  plan:{plan:ExplainRoot;elapsed_ms?:number;engine?:string}|null;mode:'result'|'explain';error:string}
export const initialSql = ():SqlState=>({text:'',title:'SQL exploration',resultSql:'',planSql:'',plan:null,mode:'result',error:''})
type Catalog = {datasets:{schema:string;name:string;title:string;columns:{name:string;type:string;description?:string}[]}[];
  functions:SchemaFunction[];operators:SemanticOpMeta[]}

export function SqlWorkbench({value,onChange,onRun,onExplain,busy,readOnly,hasResult,canPublish,onPublish,loadCatalog,notify,children,loading,origin}:
  {value:SqlState;onChange:(value:Partial<SqlState>)=>void;onRun:()=>void;onExplain:()=>void;busy:boolean;readOnly:boolean;
   hasResult:boolean;canPublish:boolean;onPublish:()=>void;loadCatalog:(signal:AbortSignal)=>Promise<Catalog>;
   notify:(message:string,error?:boolean)=>void;children:ReactNode;loading:ReactNode;origin:ReactNode}) {
  const [catalog,setCatalog]=useState<Catalog|null>(null)
  const [catalogBusy,setCatalogBusy]=useState(true)
  useEffect(()=>{const c=new AbortController();loadCatalog(c.signal).then(setCatalog).catch(e=>{
    if(!c.signal.aborted)notify(e.message||'SQL completions could not load',true)
  }).finally(()=>{if(!c.signal.aborted)setCatalogBusy(false)});return()=>c.abort()},[])
  const completions=useMemo(()=>{
    const schema:Record<string,Record<string,SQLNamespace>>={}
    for(const d of catalog?.datasets||[]) {
      (schema[d.schema]??={})[d.name]={self:{label:d.name,type:'class',detail:d.title},
        children:d.columns.map(c=>({label:c.name,type:'property',detail:c.type,info:c.description||undefined}))}
    }
    const source=buildFunctionCompletionSource(catalog?.functions||[])
    return {schema,sources:source?[source]:[]}
  },[catalog])
  const format=()=>{try{onChange({text:formatDialect(value.text,{dialect:postgresql,keywordCase:'upper',tabWidth:2})})}catch(e){notify((e as Error).message||'This SQL could not be formatted',true)}}
  const canRun=!busy&&!readOnly&&!!value.text.trim()
  return <section className="sql-workbench" aria-label="SQL workspace">
    {origin}
    <div className="sql-title"><Code size={16} aria-hidden="true"/><input aria-label="Query title" value={value.title} maxLength={160}
      onChange={e=>onChange({title:e.target.value})} placeholder="Name this query"/></div>
    <div className="sql-editor-pane" aria-label="SQL editor">
      <SqlEditor value={value.text} onChange={text=>onChange({text})} readOnly={readOnly}
        onRun={()=>{if(canRun)onRun()}} onExplain={()=>{if(canRun)onExplain()}} onFormat={format}
        onScrubRun={()=>{if(canRun)(value.mode==='explain'?onExplain:onRun)()}}
        placeholder="Write a SELECT query…" schema={completions.schema} defaultSchema="public"
        completionSources={completions.sources} semanticOperators={catalog?.operators} />
    </div>
    <div className="sql-actions data-toolbar">
      <button className="sql-run" disabled={!canRun} onClick={onRun} title="Run query · Ctrl/⌘ Enter"><Play size={15} weight="fill"/>Run</button>
      <button disabled={!canRun} onClick={onExplain} title="Explain without executing · Ctrl/⌘ Shift Enter"><TreeStructure size={16}/>Explain</button>
      <button disabled={!value.text.trim()||readOnly} onClick={format} title="Format SQL · Alt Shift F"><MagicWand size={16}/><span>Format</span></button>
      <span className="sql-key-hint">Ctrl/⌘ ↵</span>
      <button className="data-publish" disabled={busy||!canPublish||value.mode!=='result'} onClick={onPublish}>Add to Stage</button>
    </div>
    <div className="sql-results" aria-label="SQL output">
      <div className="sql-result-tools">
        <div role="tablist" aria-label="SQL output view">
          <button role="tab" aria-selected={value.mode==='result'} onClick={()=>onChange({mode:'result'})}>Results</button>
          <button role="tab" aria-selected={value.mode==='explain'} onClick={()=>onChange({mode:'explain'})}>Explain</button>
        </div>
        <small>{catalogBusy?'Loading completions…':catalog?`${catalog.datasets.length} datasets`:'Completions unavailable'}</small>
      </div>
      {loading}
      {value.error&&<p className="query-error" role="alert">{value.error}</p>}
      {value.mode==='explain'?value.plan?<>
        <p className="sql-plan-caption">{value.text===value.planSql?'Estimated plan':'Previous plan · SQL changed'} · {value.plan.elapsed_ms??0} ms</p>
        <div className="sql-explain"><ExplainGraph plan={value.plan.plan} analyzed={false}/></div>
      </>:<div className="sql-empty"><TreeStructure size={28}/><p>See how your query will run.</p><small>EXPLAIN shows the plan and estimated semantic work without running the query.</small></div>:
      hasResult?children:<div className="sql-empty"><Code size={28}/><p>Your results appear here.</p><small>Run a query with Ctrl/⌘ Enter. Drag numbers and dates to explore variations.</small></div>}
    </div>
  </section>
}
