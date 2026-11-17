import React, {useState} from 'react'
import {GroupPill, MeasurePill, FilterChip, PivotChip, TopNChip} from '@/components/desktop/rollup-shelf'
import {columnFilters, cycleOrderBy, orderDir, orderIndex, removeGroupBy} from '@/lib/desktop/sql-builder'
import type {DesktopColumnRef, RollupAgg, RollupSpec} from '@/lib/desktop/types'

export type CubeField = {name:string;type:string;description?:string;role:'dimension'|'date'|'measure';aggregates:RollupAgg[];default_aggregate:RollupAgg|null;rule?:{kind:string;numerator:string;denominator:string}|null;note?:string}
export type CubeCatalog = {relation:string;title:string;grain:string;version:number;fields:CubeField[];pivot_cap:number}
export type CubeState = {catalog:CubeCatalog;spec:RollupSpec;applied:string}
type Lane='Rows'|'Columns'|'Values'|'Filters'
const ref=(f:CubeField):DesktopColumnRef=>({name:f.name,role:f.role==='measure'?'metric':'dimension',dataTypeId:f.role==='date'?1082:f.role==='measure'?701:25,type:f.type})
export function initialCube(catalog:CubeCatalog):CubeState {
  const dimension=catalog.fields.find(f=>f.role==='dimension')||catalog.fields.find(f=>f.role==='date')
  const measure=catalog.fields.find(f=>f.role==='measure'&&f.default_aggregate&&!/rows|count$/i.test(f.name))||catalog.fields.find(f=>f.role==='measure'&&f.default_aggregate)
  return {catalog,applied:'',spec:{groupBy:dimension?[{column:ref(dimension),...(dimension.role==='date'?{grain:'month' as const}:{})}]:[],measures:measure?[{id:'value:'+measure.name,column:ref(measure),agg:measure.default_aggregate!,alias:measure.name}]:[]}}
}
export function CubeWorkbench({value,onChange,probe,toolbar,children,rowCount=false}:{rowCount?:boolean;value:CubeState;onChange:(spec:RollupSpec)=>void;probe:(name:string,search?:string)=>Promise<{values:(string|number|null)[];truncated:boolean}>;toolbar:React.ReactNode;children:React.ReactNode}) {
  const {catalog,spec}=value
  const [search,setSearch]=useState(''),[chosen,setChosen]=useState<string|null>(null),[hover,setHover]=useState<Lane|null>(null)
  const [filterFields,setFilterFields]=useState<string[]>([])
  const [fieldsOpen,setFieldsOpen]=useState(false)
  const edit=(transform:(s:RollupSpec)=>RollupSpec)=>onChange(transform(spec))
  const field=(name:string):CubeField=>catalog.fields.find(f=>f.name===name)||{name,type:'text',role:'dimension',aggregates:[],default_aggregate:null,note:'This field is no longer available.'}
  const sort=(name:string)=>({dir:orderDir(spec,name),badge:(spec.orderBy?.length||0)>1?String(orderIndex(spec,name)+1):null})
  const kind=(name:string)=>field(name).role==='date'?'date' as const:field(name).role==='measure'?'numeric' as const:'text' as const
  const allowed=(f:CubeField,lane:Lane)=>lane==='Values'?!!f.default_aggregate:lane==='Filters'||f.role!=='measure'
  function add(name:string,lane:Lane) {
    const f=catalog.fields.find(f=>f.name===name);if(!f||!allowed(f,lane))return
    let next=structuredClone(spec)
    const column=ref(f)
    if(lane==='Rows') {
      if(next.pivot?.column.name===name)next.pivot=null
      if(!next.groupBy.some(t=>t.column.name===name))next.groupBy.push({column,...(f.role==='date'?{grain:'month' as const}:{})})
    } else if(lane==='Columns') {
      next=removeGroupBy(next,name)
      next.pivot={column,values:[],...(f.role==='date'?{grain:'month' as const}:{})}
    } else if(lane==='Values') {
      if(!next.measures.some(m=>m.column?.name===name))next.measures.push({id:'value:'+name,column,agg:f.default_aggregate!,alias:name})
    } else if(!(next.filters||[]).some(f=>f.column.name===name)) {
      setFilterFields(names=>[...new Set([...names,name])]);setChosen(null);setFieldsOpen(false);return
    }
    setChosen(null);setFieldsOpen(false);onChange(next)
  }
  const displaySpec={...spec,measures:spec.measures.map(m=>({...m,displayLabel:m.column&&field(m.column.name).rule?m.column.name+' (recomputed)':undefined}))}
  const columns=new Set([...filterFields,...(spec.filters||[]).map(f=>f.column.name)])
  function lane(label:Lane,children:React.ReactNode,empty:boolean) {
    return <section className={'cube-lane cube-lane-'+label.toLowerCase()+(hover===label?' cube-lane-over':'')} aria-label={label+' shelf'}
      onDragOver={e=>{if(e.dataTransfer.types.includes('application/x-calliope-cube-field')){e.preventDefault();setHover(label)}}}
      onDragLeave={()=>setHover(null)} onDrop={e=>{e.preventDefault();setHover(null);add(e.dataTransfer.getData('application/x-calliope-cube-field'),label)}}>
      <span className="cube-lane-label">{label}</span><div className="cube-pills">{children}{empty&&<span className="cube-empty">{label==='Columns'?'Drop a field to pivot':label==='Filters'?'Drop a field to filter':'Drop fields here'}</span>}</div>
    </section>
  }
  return <section className="cube-workbench" aria-label="Cube query shelves">
    <div className="cube-toolbar data-toolbar">{rowCount&&!spec.measures.some(m=>!m.column)&&<button onClick={()=>edit(s=>({...s,measures:[...s.measures,{id:'row-count',column:null,agg:'count',alias:'Record count'}]}))}>+ Count rows</button>}<button className="cube-fields-toggle" aria-expanded={fieldsOpen} aria-controls="cube-fields" onClick={()=>setFieldsOpen(!fieldsOpen)}>Fields</button>{toolbar}</div>
    <div className="cube-sidebar">
    <aside id="cube-fields" className={'cube-fields'+(fieldsOpen?' cube-fields-open':'')} aria-label="Cube fields" onKeyDown={e=>{if(e.key==='Escape')setFieldsOpen(false)}}>
      <header><strong>Fields <small>{catalog.fields.length}</small></strong><button className="cube-fields-close" aria-label="Close fields" onClick={()=>setFieldsOpen(false)}>×</button></header>
      <input className="workspace-field" type="search" aria-label="Find a cube field" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Find a field…"/>
      <div className="cube-field-catalog">
      {(['dimension','date','measure'] as const).filter(role=>catalog.fields.some(f=>f.role===role)).map(role=><div className="cube-field-group" key={role}><h3>{({dimension:'Dimensions',date:'Dates',measure:'Measures'})[role]}</h3>
        <div className="cube-field-list">{catalog.fields.filter(f=>f.role===role&&f.name.toLowerCase().includes(search.toLowerCase())).map(f=><button key={f.name} className={'cube-field '+(role==='measure'?'cube-measure':'')} draggable
          onDragStart={e=>{e.dataTransfer.setData('application/x-calliope-cube-field',f.name);e.dataTransfer.effectAllowed='copy'}}
          title={[f.description,f.note].filter(Boolean).join(' ')} aria-pressed={chosen===f.name} onClick={()=>setChosen(chosen===f.name?null:f.name)}>{role==='measure'?'Σ':role==='date'?'◷':'◇'} {f.name}</button>)}</div></div>)}
      </div>
      {chosen&&<div className="cube-field-actions"><span>Add {chosen} to</span>{(['Rows','Columns','Values','Filters'] as Lane[]).map(l=><button className="workspace-control" disabled={!allowed(field(chosen),l)} key={l} onClick={()=>add(chosen,l)}>{l}</button>)}</div>}
    </aside>
    {lane('Values',displaySpec.measures.map(m=>{const f=m.column?field(m.column.name):{name:'Rows',rule:null,aggregates:['count'] as RollupAgg[]};return <MeasurePill key={m.id} measure={m} caption={f.rule?f.name:undefined} fixedAggregate={!!f.rule||f.aggregates.length<2}
      onCycleAggregate={()=>edit(s=>({...s,measures:s.measures.map(x=>x.id===m.id?{...x,agg:f.aggregates[(f.aggregates.indexOf(x.agg)+1)%f.aggregates.length]}:x)}))}
      having={spec.having?.find(h=>h.measureId===m.id)} sort={sort(m.alias)} onCycleSort={()=>edit(s=>cycleOrderBy(s,m.alias))} onEdit={edit}/>}),!spec.measures.length)}
    {lane('Filters',[...columns].map(name=><FilterChip key={name} column={ref(field(name))} filters={columnFilters(spec,name)} kind={kind(name)} onEdit={transform=>{const next=transform(spec);if(!next.filters?.some(f=>f.column.name===name))setFilterFields(names=>names.filter(n=>n!==name));onChange(next)}} onProbeValues={(c,q)=>probe(c.name,q)}/>),!columns.size)}
    <div className="cube-shelf-foot"><span title="Queries use all matching cube rows. Ratios and averages are recomputed from their component totals.">{spec.measures.length?'Auto-update':'Choose a measure'}</span><TopNChip spec={displaySpec} onEdit={edit}/></div>
    </div>
    {lane('Rows',spec.groupBy.map(t=><GroupPill key={t.column.name} term={t} filters={columnFilters(spec,t.column.name)} kind={kind(t.column.name)} sort={sort(t.column.name)} onCycleSort={()=>edit(s=>cycleOrderBy(s,t.column.name))} onEdit={edit} onProbeValues={(c,q)=>probe(c.name,q)}/>),!spec.groupBy.length)}
    {lane('Columns',spec.pivot&&<PivotChip pivot={spec.pivot} onEdit={edit} onRepivot={grain=>edit(s=>({...s,pivot:s.pivot?{...s.pivot,grain,values:[]}:null}))}/>,!spec.pivot)}
    <div className="cube-result">{children}</div>
  </section>
}
