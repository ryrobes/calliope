// Meaning hosts the same worksheet and virtualized grid as the chat Data Explorer.
import React, {useMemo, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Eye, RefreshCw} from '@/lib/icons'
import {ResultGrid} from '@/components/desktop/result-grid'
import {CubeWorkbench, type CubeCatalog} from './cube-workbench'
import type {RollupSpec} from '@/lib/desktop/types'
import type {QueryResultColumn} from '@/lib/db/types'
// @ts-expect-error Vite inline stylesheet
import workspaceStyles from '../../../services/calliope/calliope/workspace-ui.css?inline'
// @ts-expect-error Vite inline stylesheet
import sharedStyles from './data.css?inline'
// @ts-expect-error Vite inline stylesheet
import meaningStyles from './meaning-pivot.css?inline'

type Row = Record<string,string|null>
type Rollup = {columns:string[];column_types?:{name:string;oid:number}[];rows:Row[];has_more:boolean;spec:RollupSpec}
type Records = {fields:{name:string;type:string;display_type:string}[];rows:Row[];offset:number;has_more:boolean;fields_more:boolean}
type Selection = {field:string;value:string|null;grain?:string}[]
type Props = {catalog:CubeCatalog;spec:RollupSpec;result:Rollup|null;records:Records|null;loading:boolean;error:string;
  view:'results'|'records';selection:Selection|null;elapsed?:number;onChange:(spec:RollupSpec)=>void;onSelect:(selection:Selection|null)=>void;
  onView:(view:'results'|'records')=>void;onPage:(offset:number)=>void;onOpenRecord:(row:Row)=>void;
  onRefresh:()=>void;probe:(field:string,search?:string)=>Promise<{values:(string|number|null)[];truncated:boolean}>}
const types:Record<number,string>={16:'bool',20:'int8',21:'int2',23:'int4',25:'text',700:'float4',701:'float8',1700:'numeric',1082:'date',1114:'timestamp',1184:'timestamptz',114:'json',3802:'jsonb'}
const oids=Object.fromEntries(Object.entries(types).map(([id,name])=>[name,Number(id)]))
function Workspace(props:Props&{portalContainer:HTMLElement}) {
  const {catalog,spec,result,records,view,selection}=props
  const [transposed,setTransposed]=useState(false)
  const groups=result?.spec.groupBy||[]
  const shownSpec={...spec,pivot:spec.pivot&&result?.spec.pivot?.column.name===spec.pivot.column.name&&result.spec.pivot.grain===spec.pivot.grain?{...spec.pivot,values:result.spec.pivot.values}:spec.pivot}
  const columns:QueryResultColumn[]=useMemo(()=>view==='records'?(records?.fields||[]).map(f=>({name:f.name,dataTypeId:oids[f.type]||25,dataTypeName:f.display_type})):
    (result?.columns||[]).map(name=>{const oid=result?.column_types?.find(c=>c.name===name)?.oid||25;return {name,dataTypeId:oid,dataTypeName:types[oid]||`oid:${oid}`}}),[view,records?.fields,result?.columns,result?.column_types])
  const rows=view==='records'?records?.rows:result?.rows
  const explore=(row:Row)=>props.onSelect(groups.map(t=>({field:t.column.name,value:row[t.column.name],...(t.grain?{grain:t.grain}:{})})))
  return <CubeWorkbench rowCount value={{catalog,spec:shownSpec,applied:''}} onChange={props.onChange} probe={props.probe}
    toolbar={<><button aria-label="Refresh data" title="Refresh data" onClick={props.onRefresh} disabled={props.loading}><RefreshCw className="h-4 w-4"/></button><span className="meaning-worksheet-hint">Drag fields to a shelf · click a pill to edit</span></>}>
    <div className="meaning-result-toolbar">
      <div role="tablist" aria-label="Data results">
        <button role="tab" aria-selected={view==='results'} onClick={()=>props.onView('results')}>Query result</button>
        <button role="tab" aria-selected={view==='records'} onClick={()=>props.onView('records')}>Source rows{selection?.length?' · selected group':''}</button>
      </div>
      <span role="status">{props.loading?'Updating…':props.error?'Not updated':props.elapsed!=null?`${Math.round(props.elapsed)} ms`:''}</span>
    </div>
    {props.error&&<p className="meaning-grid-error" role="alert">{props.error}</p>}
    {view==='records'&&selection?.length&&<div className="meaning-row-selection"><span>{selection.map(t=>`${t.field}: ${t.value??'NULL'}`).join(' · ')}</span><button onClick={()=>props.onSelect(null)}>Clear selection</button></div>}
    <section className="data-result meaning-result" aria-label={view==='records'?'Source records':'Query result'} aria-busy={props.loading}>
      {rows&&<ResultGrid key={view} columns={columns} rows={rows} transposed={transposed} onTransposedChange={setTransposed}
        filterLabel="Search these rows" portalContainer={props.portalContainer}
        onOpenRow={view==='records'&&!props.loading&&!props.error?({row})=>props.onOpenRecord(row as Row):undefined}
        extraCellMenuItems={view==='results'&&groups.length&&!props.loading&&!props.error?({row})=>[{id:'source-records',label:'Explore source rows',icon:Eye,onSelect:()=>explore(row as Row)}]:undefined}/>}
      {!rows&&<div className="meaning-grid-empty">{props.loading?'Reading source data…':props.error?'Edit the shelves or refresh to try again.':'Add a measure to Values, or browse Source rows.'}</div>}
    </section>
    <footer className="meaning-result-footer">
      {view==='records'?<><span>{records?.rows.length?`${(records.offset||0)+1}–${(records.offset||0)+records.rows.length}`:'No rows'}{records?.has_more?' · more available':''} · live preview</span><button disabled={props.loading||!records?.offset} onClick={()=>props.onPage((records?.offset||0)-40)}>Previous rows</button><button disabled={props.loading||!records?.has_more||(records?.offset||0)>=10000} onClick={()=>props.onPage((records?.offset||0)+40)}>Next rows</button></>:
        <span>{result?.has_more?'First 200 groups · use shelf filters or Top N to refine.':`${result?.rows.length??0} result ${(result?.rows.length??0)===1?'row':'rows'}.`}{groups.length?' Right-click a cell to explore source rows.':''}</span>}
    </footer>
  </CubeWorkbench>
}
export function mountPivot(host:HTMLElement, initial:Props) {
  const shadow=host.attachShadow({mode:'open'})
  const style=document.createElement('style');style.textContent=workspaceStyles+'\n'+sharedStyles+'\n'+meaningStyles
  const surface=document.createElement('div');surface.className='meaning-pivot-surface'
  // Menus share this stylesheet and theme, outside the worksheet's containment.
  const portals=document.createElement('div');portals.className='meaning-grid-portals'
  shadow.append(style,surface,portals)
  const root=createRoot(surface)
  const update=(props:Props)=>root.render(<Workspace {...props} portalContainer={portals}/>);update(initial)
  return {update,destroy:()=>root.unmount()}
}
