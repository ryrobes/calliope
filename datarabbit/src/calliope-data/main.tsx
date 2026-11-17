import { formatIncomingSql } from './sql-format'
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ThinkingOrb, type OrbState } from 'thinking-orbs'
import { ArrowClockwise, ArrowUpRight, Stack, ChatTeardropText, Code, Database, Info, X, ClockCounterClockwise, MagnifyingGlass, Play } from '@phosphor-icons/react'
import { ResultGrid } from '@/components/desktop/result-grid'
import type { QueryResultColumn } from '@/lib/db/types'
import { SqlPreview } from './sql-preview'
import { SqlWorkbench, initialSql, type SqlState } from './sql-workbench'
import { CubeWorkbench, initialCube, type CubeState, type CubeCatalog } from './cube-workbench'
import type { RollupSpec } from '@/lib/desktop/types'
import './data.css'

type Dataset = {relation: string; schema?:string;name?:string;title: string; description: string; area?: string; kind?: string; catalog_kind?:'table'|'cube';grain?:string; estimated_rows?: number; matched_fields?: string[]; columns?: {name:string;type:string;description?:string}[]; meaning?: Record<string, unknown>}
type Metric = {name:string;title:string;description?:string;category?:string;version:number;snapshot?:Record<string,unknown>|null;series?:Record<string,unknown>[]}
type AreaCard = {key:string;name:string;about:string;table_count:number;subarea_count:number;question_count:number;gap_count:number;published_at:string;subtopics?:string[];sample_question?:string;metric_idea_count?:number;breakdown_count?:number}
type MetricIdea = {name:string;formula:string;grain:string;why:string}
type BreakdownIdea = {name:string;purpose:string;measures:string[];dimensions:string[];grain:string;caveats?:string}
type MeaningArea = {key:string;name:string;about:string;members:string[];published_at:string;roles:Record<string,string>;
  relationships?:{source:string;description:string}[];
  subareas:{name:string;about:string;tables:string[]}[];questions:string[];gaps:string[];
  candidate_metrics:MetricIdea[];candidate_breakdowns:BreakdownIdea[];datasets:Dataset[];cubes:Dataset[];metrics:Metric[];related_error?:string}
declare global {interface Window {CalliopeMetricCards?:{cardMarkup:(metric:Metric,options:Record<string,unknown>)=>string}}}
type Result = {columns: {name:string;type?:string}[]; rows: (unknown[] | Record<string, unknown>)[]; row_count:number; truncated?:boolean; elapsed_ms?:number; byte_limited?:boolean; saved_snapshot?:boolean; error?:{message:string}}
type Proposal = {title?:string;summary?:string;sql?:string;assumptions?:string[];clarification?:{question:string;options:string[]}}
type Run = {id:string;status:string;result:Result;error?:string;completed_at:string}
type Origin = {surface_id:string;session_id:string;title:string;turn_ordinal?:number;observed_at:string;read_only_reason?:string}
type Draft = {id:string;question:string;sources:string[];proposal:Proposal;status:string;error?:string;created_at:string;latest_run?:Run;run_summary?:{status:string;row_count?:number;truncated?:boolean}|null;context?:{sql_editor?:boolean;origin?:Origin;datasets?:Dataset[];meaning_area?:{key:string;name:string};cube_shelf?:{relation:string;spec:RollupSpec;definition_version:number}}}
type Context = {session_id:string|null;title:string;can_publish:boolean;theme?:Record<string,string>}
type EntryResult = {action:'search'|'query';items:Dataset[];metrics?:Metric[];metrics_error?:string;meaning_areas?:AreaCard[];areas_error?:string;sources:Dataset[];question:string;message:string;entry_route_id:string}

async function api(path:string, body?:unknown, signal?:AbortSignal) {
  const r = await fetch('/api/calliope/data/'+path, {signal, credentials:'same-origin', cache:'no-store',
    ...(body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Calliope-Data':'1'}, body:JSON.stringify(body)})})
  const data = await r.json()
  if (!r.ok) throw new Error(data.error?.message || 'This request could not finish')
  return data
}

function post(value:Record<string,unknown>) { window.parent.postMessage(value, location.origin) }
function clientId() { return Array.from(crypto.getRandomValues(new Uint32Array(4)),n=>n.toString(16).padStart(8,'0')).join('') }

function DatasetTile({item,onOpen}:{item:Dataset;onOpen:()=>void}) {
  return <button className="dataset-card data-tile" onClick={onOpen}>
    <strong><span className="data-tile-title" title={item.title}>{item.title}</span><span className="data-tile-open" aria-hidden="true">↗</span></strong>
    <p title={item.description||undefined}>{item.description||'Open a preview to see the fields and example rows.'}</p>
    {!!item.matched_fields?.length&&<small className="dataset-matches" title={`Matching fields: ${item.matched_fields.join(', ')}`}>Matching fields: {item.matched_fields.join(', ')}</small>}
    <span className="data-tile-meta">
      {item.grain&&<small className="data-tile-source" title={item.grain}>{item.grain}</small>}
      {item.estimated_rows!=null&&<small>About {Math.max(0,item.estimated_rows).toLocaleString()} rows</small>}
      <small className="data-tile-source" title={item.relation}>{item.relation}</small>
    </span>
  </button>
}

function AreaTile({area,onOpen}:{area:AreaCard;onOpen:()=>void}) {
  return <button className="area-card" onClick={onOpen} aria-label={`${area.name} · Explore area`}>
    <span className="area-card-eyebrow"><Stack size={17} aria-hidden="true"/>Meaning area<ArrowUpRight className="area-card-arrow" size={18} aria-hidden="true"/></span>
    <strong className="area-card-title">{area.name}</strong>
    <span className="area-card-scope"><Database size={13} aria-hidden="true"/>{area.table_count} {area.table_count===1?'dataset':'datasets'}<span>·</span>{area.subarea_count} {area.subarea_count===1?'subtopic':'subtopics'}</span>
    <p className="area-card-about" title={area.about}>{area.about}</p>
    {!!area.subtopics?.length&&<span className="area-card-topics" title={area.subtopics.join(' · ')}>{area.subtopics.map(s=><span key={s}>{s}</span>)}{area.subarea_count>area.subtopics.length&&<span>+{area.subarea_count-area.subtopics.length} more</span>}</span>}
    {area.sample_question&&<span className="area-card-question"><ChatTeardropText size={15} aria-hidden="true"/><span><small>A question to explore</small><span title={area.sample_question}>{area.sample_question}</span></span></span>}
    <span className="area-card-foot"><span>{area.question_count} {area.question_count===1?'question':'questions'}</span><span>{area.gap_count} {area.gap_count===1?'unknown':'unknowns'}</span><span>{area.metric_idea_count||0} metric {area.metric_idea_count===1?'idea':'ideas'}</span><span>{area.breakdown_count||0} {area.breakdown_count===1?'breakdown':'breakdowns'}</span></span>
  </button>
}

function metricIdeaRequest(metric:MetricIdea) {
  return `Explore the proposed metric “${metric.name}”.\nProposed calculation: ${metric.formula||'Not yet defined'}\nProposed grain: ${metric.grain||'Not yet defined'}\nPurpose: ${metric.why||'Not yet described'}\n\nShow one scalar value for the selected scope and period, with no grouping or time series. This is a proposal, not an approved metric definition. Validate the grain, units, denominator and joins, state assumptions, and ask for any missing business definition.`
}

function breakdownRequest(idea:BreakdownIdea) {
  return `Explore the proposed breakdown “${idea.name}”.\nPurpose: ${idea.purpose}\nMeasures: ${idea.measures.join('; ')}\nGroup by: ${idea.dimensions.join('; ')}\nEach result row: ${idea.grain||'Confirm grouping'}\nCaveats: ${idea.caveats||'Validate before interpreting'}\n\nShow the grouped comparison that serves this purpose. This is a proposed analysis, not a scalar metric or an approved definition. Validate measures, grain and denominators; keep separate populations separate and clarify alternative groupings rather than guessing. Use only the relevant datasets and verified joins.`
}

function MetricTile({metric}:{metric:Metric}) {
  // Shared Gallery markup escapes catalog text and keeps values/trends consistent.
  const markup=useMemo(()=>window.CalliopeMetricCards?.cardMarkup(metric,{smooth:true,
    openLabel:`Inspect ${metric.title}`}),[metric])
  const open=()=>post({type:'calliope.data.metric',name:metric.name})
  return markup?<div className="data-metric-tile" onClick={event=>{
    if((event.target as Element).closest('[data-metric-open]'))open()
  }} dangerouslySetInnerHTML={{__html:markup}}/>:
    <button className="data-tile" onClick={open}><strong>{metric.title}</strong><p>{metric.description}</p></button>
}

const relativeTime = new Intl.RelativeTimeFormat(undefined,{numeric:'auto'})
function explorationAge(createdAt:string) {
  const minutes=Math.max(0,Math.floor((Date.now()-new Date(createdAt).getTime())/60000))
  if(!Number.isFinite(minutes))return 'Saved exploration'
  if(minutes<1)return 'Just now'
  if(minutes<60)return relativeTime.format(-minutes,'minute')
  if(minutes<1440)return relativeTime.format(-Math.floor(minutes/60),'hour')
  return relativeTime.format(-Math.floor(minutes/1440),'day')
}

function ExplorationTile({draft,sources,disabled,onOpen}:{draft:Draft;sources:Dataset[];disabled:boolean;onOpen:()=>void}) {
  const title=draft.proposal.title||draft.question
  const sourceNames=draft.sources.map(relation=>sources.find(s=>s.relation===relation)?.title||relation).join(', ')
  const result=draft.run_summary
  const status=result?.status==='error'?'Needs another try':result?.status==='running'?'Running query':
    draft.status==='ready'?'Query ready':draft.status==='clarification'?'Needs a detail':
    draft.status==='building'?'Building':draft.status==='error'?'Needs another try':draft.status
  return <button className="history-item data-tile" disabled={disabled} onClick={onOpen}>
    <strong><span className="data-tile-title" title={title}>{title}</span><span className="data-tile-open" aria-hidden="true">↗</span></strong>
    {draft.question!==title&&<p title={draft.question}>{draft.question}</p>}
    <span className="data-tile-meta">
      {!!sourceNames&&<small className="data-tile-source" title={draft.sources.join(', ')}>{sourceNames}</small>}
      <small>{result?.status==='complete'&&result.row_count!=null
        ?`${result.row_count.toLocaleString()} ${result.row_count===1?'row':'rows'}${result.truncated?' shown':''}`:status}</small>
      <small><time dateTime={draft.created_at} title={new Date(draft.created_at).toLocaleString()}>{explorationAge(draft.created_at)}</time></small>
    </span>
  </button>
}

function applyTheme(theme:Record<string,string> = {}) {
  for(const [key,value] of Object.entries(theme)) document.documentElement.style.setProperty(key,String(value))
  // The iframe must follow Calliope's selected theme, not the browser's preference.
  document.documentElement.dataset.theme=theme['--data-color-scheme']==='light'?'light':'dark'
}

function DataLoading({label,state='working',compact=false}:{label:string;state?:OrbState;compact?:boolean}) {
  return <div className={`data-loading${compact?' data-loading-compact':''}`} role="status" aria-live="polite">
    <ThinkingOrb state={state} size={compact?20:64} aria-hidden="true" />
    <span>{label}</span>
  </div>
}

const oids:Record<string,number> = {bool:16,int8:20,int2:21,int4:23,text:25,float4:700,float8:701,numeric:1700,json:114,jsonb:3802,date:1082,timestamp:1114,timestamptz:1184}

function DataGrid({result, label,stage=false}:{result:Result; label:string;stage?:boolean}) {
  const [transposed, setTransposed] = useState(false)
  const [record, setRecord] = useState<Record<string,unknown>|null>(null)
  const columns:QueryResultColumn[] = result.columns.map(c=>({name:c.name,dataTypeId:oids[c.type||'']||25,dataTypeName:c.type||'text'}))
  const rows = result.rows.map(row=>Array.isArray(row)?Object.fromEntries(columns.map((c,i)=>[c.name,row[i]])):row)
  return <section className="data-result" aria-label={label}>
    {!stage&&<div className="result-caption"><span>{label} · {rows.length.toLocaleString()} rows{result.truncated?' shown · more available':''}</span>
      <small>{result.elapsed_ms != null ? `${result.elapsed_ms.toLocaleString()} ms` : ''}</small></div>}
    <ResultGrid columns={columns} rows={rows} transposed={transposed} onTransposedChange={setTransposed}
      filterLabel="Search these rows" showSearch={!stage} onOpenRow={({row})=>setRecord(row)}/>
    {record && <div className="record-backdrop"><section className="record-panel" role="dialog" aria-modal="true" aria-label="Record details">
      <header><h2>Record details</h2><button className="workspace-control" onClick={()=>setRecord(null)} autoFocus>Close</button></header>
      <dl>{columns.map(c=><React.Fragment key={c.name}><dt>{c.name}</dt><dd><pre>{typeof record[c.name]==='object'?JSON.stringify(record[c.name],null,2):String(record[c.name]??'—')}</pre></dd></React.Fragment>)}</dl>
    </section></div>}
  </section>
}

type Visit = {cube:CubeState|null;cubeError:string;id:string;q:string;area:string;selected:Dataset[];preview:{dataset:Dataset;result:Result}|null;
  areaDetail:MeaningArea|null;areaKey:string|null;areaTopic:string;areaMatches:AreaCard[];areasError:string;
  question:string;draft:Draft|null;run:Run|null;busy:string;answer:string;view:'discover'|'explore'|'recent'|'area'|'sql';sql:SqlState;inputs:string;scroll:number;
  matches:Dataset[]|null;metricMatches:Metric[];metricsError:string;pendingQuestion:string;searchNote:string;entryRouteId:string|null}
const initialVisit = ():Visit => ({cube:null,cubeError:'',id:clientId(),q:'',area:'',selected:[],preview:null,question:'',draft:null,
  areaDetail:null,areaKey:null,areaTopic:'',areaMatches:[],areasError:'',
  run:null,busy:'',answer:'',view:'discover',sql:initialSql(),inputs:'',scroll:0,matches:null,metricMatches:[],metricsError:'',pendingQuestion:'',searchNote:'',entryRouteId:null})

function useJourney() {
  const [journey,setJourney] = useState(()=>({entries:[initialVisit()],index:0}))
  const current = useRef(journey)
  const body = useRef<HTMLDivElement>(null)
  function commit(next:typeof journey) {current.current=next;setJourney(next)}
  function patch(values:Partial<Visit>|((visit:Visit)=>Partial<Visit>), id=current.current.entries[current.current.index].id) {
    const j=current.current
    commit({...j,entries:j.entries.map(e=>e.id===id?{...e,...(typeof values==='function'?values(e):values)}:e)})
  }
  function rememberScroll() {patch({scroll:body.current?.scrollTop||0})}
  function push(values:Partial<Visit>) {
    rememberScroll()
    const j=current.current, entry={...j.entries[j.index],id:clientId(),busy:'',scroll:0,...values}
    commit({entries:[...j.entries.slice(0,j.index+1),entry],index:j.index+1})
    return entry.id
  }
  function go(delta:number) {
    const index=current.current.index+delta
    if(index<0||index>=current.current.entries.length)return
    rememberScroll();commit({...current.current,index})
  }
  const page=journey.entries[journey.index]
  useLayoutEffect(()=>{if(body.current)body.current.scrollTop=page.scroll},[page.id])
  return {page,patch,push,go,body,canBack:journey.index>0,canForward:journey.index<journey.entries.length-1,
    exists:(id:string)=>current.current.entries.some(e=>e.id===id)}
}

function App() {
  const [context,setContext] = useState<Context>({session_id:null,title:'',can_publish:true})
  const [items,setItems] = useState<Dataset[]>([])
  const [metrics,setMetrics] = useState<Metric[]>([])
  const [metricsError,setMetricsError] = useState('')
  const [areas,setAreas] = useState<string[]>([])
  const [meaningAreas,setMeaningAreas] = useState<AreaCard[]>([])
  const [areasError,setAreasError] = useState('')
  const [recent,setRecent] = useState<Draft[]>([])
  const [searching,setSearching] = useState(false)
  const [cubeTool,setCubeTool] = useState<'sql'|'question'|'about'|null>(null)
  const [queryTool,setQueryTool] = useState<'sources'|'sql'|'about'|null>(null)
  const nav=useJourney(), {page,patch,push}=nav
  const {q,area,selected,preview,question,draft,run,busy,view,answer}=page
  const pendingPublications = useRef(new Map<string,string>())
  const openedRequests = useRef(new Set<string>())
  const sqlRequests = useRef(new Set<string>())
  const searchInput = useRef<HTMLInputElement>(null)
  const visibleItems=page.matches??items
  const visibleMetrics=page.matches!==null?page.metricMatches:metrics
  const visibleAreas=page.matches!==null?page.areaMatches:meaningAreas
  const areaSearchError=page.matches!==null?page.areasError:areasError
  const metricSearchError=page.matches!==null?page.metricsError:metricsError
  const filteredItems=visibleItems.filter(i=>!area||q||i.area===area)
  const tables=filteredItems.filter(i=>i.catalog_kind!=='cube')
  const cubes=filteredItems.filter(i=>i.catalog_kind==='cube')
  const filteredMetrics=visibleMetrics.filter(i=>!area||q||i.category===area)
  const filteredAreas=visibleAreas.filter(i=>!area||q||i.key===area)
  const origin=draft?.context?.origin
  const readOnly=!!origin?.read_only_reason
  const inputsCurrent = !!draft && page.inputs===JSON.stringify([question,selected.map(x=>x.relation)])
  const shelfKey=page.cube?JSON.stringify(page.cube.spec):''
  const clean = view==='sql'?!!draft&&page.sql.text===page.sql.resultSql&&(page.sql.title.trim()||'SQL exploration')===draft.proposal.title:
    inputsCurrent && draft?.status==='ready' && (!page.cube||page.cube.applied===shelfKey)
  const loadRecent = async()=>{const data=await api('drafts');setRecent(data.drafts);return data}
  const notify=(message:string,error=false)=>post({type:'calliope.data.notify',message,error})
  const fail=(e:unknown)=>notify((e as Error).message||'This request could not finish',true)

  useEffect(()=>{
    const receive=(e:MessageEvent)=>{
      if(e.origin!==location.origin || e.source!==window.parent) return
      if(e.data?.type==='calliope.data.context') {
        setContext(e.data)
        applyTheme(e.data.theme)
      }
      if(e.data?.type==='calliope.data.explore'&&typeof e.data.request_id==='string'&&typeof e.data.surface_id==='string') {
        post({type:'calliope.data.exploring',request_id:e.data.request_id})
        if(!openedRequests.current.has(e.data.request_id)) {
          openedRequests.current.add(e.data.request_id)
          openSurface(e.data.surface_id, typeof e.data.artifact_query_id==='string'?e.data.artifact_query_id:undefined, typeof e.data.preview_id==='string'?e.data.preview_id:undefined, e.data.sql_mode===true)
        }
      }
      if(e.data?.type==='calliope.data.sql'&&typeof e.data.request_id==='string'&&typeof e.data.sql==='string') {
        post({type:'calliope.data.exploring',request_id:e.data.request_id})
        if(!openedRequests.current.has(e.data.request_id)) {
          openedRequests.current.add(e.data.request_id)
          openSuppliedSql(e.data.sql, typeof e.data.title==='string'?e.data.title:'SQL exploration')
        }
      }
      const id=pendingPublications.current.get(e.data?.request_id)
      if(['calliope.data.published','calliope.data.handed_off'].includes(e.data?.type) && id) {
        patch({busy:''},id);pendingPublications.current.delete(e.data.request_id)
      }
    }
    window.addEventListener('message',receive)
    post({type:'calliope.data.ready'})
    loadRecent().catch(fail)
    return()=>window.removeEventListener('message',receive)
  },[])

  useEffect(()=>{
    const controller = new AbortController()
    setSearching(true)
    const timer=setTimeout(()=>{
      api('search?q='+encodeURIComponent(q),undefined,controller.signal).then(data=>{
        setItems(data.items);setAreas(data.areas);setMetrics(data.metrics||[]);setMetricsError(data.metrics_error||'')
        setMeaningAreas(data.meaning_areas||[]);setAreasError(data.areas_error||'')
      }).catch(e=>{
        if(e.name!=='AbortError') {setItems([]);setMetrics([]);setMetricsError('');setMeaningAreas([]);setAreasError('');fail(e)}
      }).finally(()=>{if(!controller.signal.aborted)setSearching(false)})
    },q?250:0)
    return()=>{clearTimeout(timer);controller.abort()}
  },[q])

  async function openArea(key:string) {
    const id=push({view:'area',busy:'area',areaDetail:null,areaKey:key,areaTopic:'',question:'',
      draft:null,run:null,selected:[],preview:null,cube:null,answer:'',inputs:''})
    try {patch({areaDetail:await api('areas/detail?key='+encodeURIComponent(key))},id)}
    catch(e){if(nav.exists(id))fail(e)}finally{patch({busy:''},id)}
  }

  async function queryArea(request:string, topicName=page.areaTopic) {
    const a=page.areaDetail
    if(!a||busy||!request.trim())return
    const topic=a.subareas.find(s=>s.name===topicName)
    const sources=a.datasets.filter(d=>!topic||topic.tables.includes(d.relation))
    const id=push({view:'explore',busy:'build',selected:sources,question:request,draft:null,run:null,preview:null,inputs:'',cube:null,areaTopic:topicName})
    try {
      const d:Draft=await api('drafts',{question:request,sources:sources.map(s=>s.relation),area_key:a.key,area_topic:topicName})
      await finishBuild(d,id)
    }catch(e){if(nav.exists(id))fail(e)}finally{patch({busy:''},id)}
  }

  function askCallie(kind:'gap'|'question'|'metric'|'breakdown',index:number) {
    if(!page.areaDetail||busy)return
    const requestId=clientId()
    pendingPublications.current.set(requestId,page.id);patch({busy:'handoff'})
    post({type:'calliope.data.handoff',request_id:requestId,area_key:page.areaDetail.key,kind,index})
  }

  async function openDataset(item:Dataset) {
    setCubeTool(null)
    setQueryTool(null)
    const id=push({busy:'preview',view:'explore',preview:null,selected:[item],question:page.pendingQuestion,
      areaKey:page.areaDetail?.members.includes(item.relation)?page.areaDetail.key:null,
      draft:null,run:null,answer:'',inputs:'',pendingQuestion:'',cube:null,cubeError:''})
    try {
      if(item.catalog_kind==='cube') {
        const catalog:CubeCatalog=await api('cubes/metadata?relation='+encodeURIComponent(item.relation))
        patch({cube:initialCube(catalog)},id)
        return
      }
      const value=await api('preview?relation='+encodeURIComponent(item.relation))
      if(value.result.error)throw new Error(value.result.error.message)
      patch({preview:value,selected:[value.dataset]},id)
    } catch(e) {if(nav.exists(id))fail(e)}
    finally{patch({busy:''},id)}
  }

  async function restore(draftId:string) {
    setQueryTool(null);setCubeTool(null)
    const id=push({...initialVisit(),busy:'restore',view:'explore'})
    try {
      const d:Draft=await api('drafts/'+draftId)
      const cube=await restoreCube(d)
      const sources=d.sources.map(relation=>items.find(i=>i.relation===relation)||{relation,title:relation,description:''})
      patch({cube,areaKey:d.context?.meaning_area?.key||null,selected:sources,question:d.question,draft:d,run:d.latest_run||null,inputs:JSON.stringify([d.question,d.sources])},id)
      if(d.context?.sql_editor)patch({view:'sql',sql:{...initialSql(),text:formatIncomingSql(d.proposal.sql||''),title:d.proposal.title||d.question,resultSql:formatIncomingSql(d.proposal.sql||'')}},id)
    }catch(e){if(nav.exists(id))fail(e)}finally{patch({busy:''},id)}
  }

  async function openSurface(surfaceId:string, artifactQueryId?:string, previewId?:string, sqlMode=false) {
    setQueryTool(null);setCubeTool(null)
    const id=push({...initialVisit(),busy:'restore',view:'explore'})
    try {
      const d:Draft=await api('surfaces/'+encodeURIComponent(surfaceId)+(artifactQueryId?'/queries/'+encodeURIComponent(artifactQueryId):'')+'/explore',{preview_id:previewId})
      if(!nav.exists(id))return
      const cube=await restoreCube(d)
      patch({cube,areaKey:d.context?.meaning_area?.key||null,selected:d.sources.map(relation=>({relation,title:relation,description:''})),question:d.question,
        draft:d,run:d.latest_run||null,inputs:JSON.stringify([d.question,d.sources])},id)
      if(sqlMode||d.context?.sql_editor)patch({view:'sql',cube:null,sql:{...initialSql(),text:formatIncomingSql(d.proposal.sql||''),title:d.proposal.title||d.question,resultSql:formatIncomingSql(d.proposal.sql||'')}},id)
      await loadRecent()
    }catch(e){if(nav.exists(id))fail(e)}finally{patch({busy:''},id)}
  }

  async function restoreCube(d:Draft):Promise<CubeState|null> {
    const saved=d.context?.cube_shelf
    if(!saved)return null
    const catalog:CubeCatalog=await api('cubes/metadata?relation='+encodeURIComponent(saved.relation))
    // Reopening displays the frozen result; only an explicit edit/refresh runs again.
    return {catalog,spec:saved.spec,applied:JSON.stringify(saved.spec)}
  }

  async function summarize() {
    const item=selected[0]
    if(!item||busy)return
    const id=push({busy:'preview',cubeError:''})
    try {const catalog:CubeCatalog=await api('cubes/metadata?relation='+encodeURIComponent(item.relation))
      patch({cube:initialCube(catalog),busy:''},id)
    }catch(e){patch({busy:''},id);fail(e)}
  }

  // Debounce shelf edits and discard obsolete responses. Existing results stay visible.
  useEffect(()=>{
    const cube=page.cube
    if(view!=='explore'||!cube||cube.applied===shelfKey||!cube.spec.measures.length)return
    const controller=new AbortController(),id=page.id
    const timer=setTimeout(async()=>{
      patch({busy:'shelf',cubeError:''},id)
      try {
        const d:Draft=await api('cubes/shelf',{relation:cube.catalog.relation,spec:cube.spec,previous_id:draft?.id},controller.signal)
        if(controller.signal.aborted)return
        if(d.latest_run?.status!=='complete')throw new Error(d.latest_run?.error||'The cube query could not finish')
        const spec=d.context!.cube_shelf!.spec
        patch({draft:d,run:d.latest_run||null,question:d.question,inputs:JSON.stringify([d.question,d.sources]),
          cube:{...cube,spec,applied:JSON.stringify(spec)},busy:'',cubeError:d.latest_run?.error||''},id)
        await loadRecent()
      }catch(e){if(!controller.signal.aborted){patch({busy:'',cubeError:(e as Error).message},id);fail(e)}}
    },350)
    return()=>{clearTimeout(timer);controller.abort()}
  },[page.id,shelfKey,view])

  async function execute(d:Draft, id:string) {
    patch({busy:'run'},id)
    const value:Run=await api('drafts/'+d.id+'/run',{})
    patch({run:value},id)
    if(value.error&&nav.exists(id))notify(value.error,true)
    return value
  }

  async function finishBuild(d:Draft, id:string) {
    patch({question:d.question,draft:d,selected:d.context?.datasets||d.sources.map(relation=>items.find(i=>i.relation===relation)||{relation,title:relation,description:''}),inputs:JSON.stringify([d.question,d.sources])},id)
    if(!nav.exists(id))return
    if(d.error)notify(d.error,true)
    else if(d.status==='ready'&&d.proposal.sql)await execute(d,id)
    await loadRecent()
  }

  async function discover() {
    if(busy||!q.trim())return
    const request=q.trim()
    const id=push({...initialVisit(),q:request,busy:'route'})
    try {
      const entry:EntryResult=await api('resolve',{q:request})
      if(!nav.exists(id))return
      patch({matches:entry.items,metricMatches:entry.metrics||[],metricsError:entry.metrics_error||'',
        areaMatches:entry.meaning_areas||[],areasError:entry.areas_error||'',
        pendingQuestion:entry.question,searchNote:entry.message,entryRouteId:entry.entry_route_id},id)
      if(entry.action==='query'&&entry.sources.length) {
        patch({view:'explore',selected:entry.sources,question:request,pendingQuestion:'',busy:'build'},id)
        const d:Draft=await api('drafts',{question:request,sources:entry.sources.map(x=>x.relation),entry_route_id:entry.entry_route_id})
        await finishBuild(d,id)
      }
    }catch(e){if(nav.exists(id))fail(e)}finally{patch({busy:''},id)}
  }

  async function build(clarificationAnswer?:string) {
    if(busy || readOnly || !question.trim() || !selected.length)return
    if(clarificationAnswer!==undefined && (!inputsCurrent||draft?.status!=='clarification'))return
    const previous=draft
    const id=push({busy:clarificationAnswer!==undefined?'refine':'build',view:'explore',draft:null,run:null,answer:'',cube:null,cubeError:''})
    try {
      const d:Draft=clarificationAnswer!==undefined
        ? await api('drafts/'+previous!.id+'/refine',{answer:clarificationAnswer})
        : await api('drafts',{question,sources:selected.map(x=>x.relation),area_key:page.areaKey,previous_id:previous?.id,entry_route_id:page.entryRouteId})
      await finishBuild(d,id)
    }catch(e){if(nav.exists(id))fail(e)}finally{patch({busy:''},id)}
  }

  async function refresh() {
    if(!clean||!draft||busy)return
    if(page.cube){push({cube:{...page.cube,applied:''},cubeError:'',busy:''});return}
    const id=push({run:null,busy:'run'})
    try{await execute(draft,id)}catch(e){if(nav.exists(id))fail(e)}finally{patch({busy:''},id)}
  }

  function publish() {
    if(!run||run.status!=='complete'||run.result.saved_snapshot||!clean||busy)return
    patch({busy:'publish'})
    const requestId=clientId()
    pendingPublications.current.set(requestId,page.id)
    post({type:'calliope.data.publish',request_id:requestId,run_id:run.id,title:draft?.proposal.title,
      session_id:context.session_id})
  }

  function openSql(copy=false) {
    if(view==='sql')return
    const source=preview?.dataset||selected[0]
    const quote=(s:string)=>'"'+s.replaceAll('"','""')+'"'
    const ref=source?(quote(source.schema||source.relation.split('.')[0])+'.'+quote(source.name||source.relation.split('.').slice(1).join('.'))):''
    const text=formatIncomingSql(copy?(draft?.proposal.sql||(ref?`SELECT * FROM ${ref}\nLIMIT 1000;`:'')):'')
    push({...initialVisit(),view:'sql',draft:copy?draft:null,run:copy?run:null,preview:copy?preview:null,
      sql:{...initialSql(),text,title:copy?(draft?.proposal.title||source?.title||'SQL exploration'):'SQL exploration',resultSql:copy?text:''}})
  }

  async function openSuppliedSql(text:string,title:string) {
    text=formatIncomingSql(text)
    const id=push({...initialVisit(),view:'sql',busy:'run',sql:{...initialSql(),text,title,mode:'result'}})
    try {
      const d:Draft=await api('sql/run',{sql:text,title})
      patch(current=>({draft:d,run:d.latest_run||null,sql:{...current.sql,resultSql:text,error:d.latest_run?.error||''}}),id)
      await loadRecent()
    }catch(e){patch(current=>({sql:{...current.sql,error:(e as Error).message}}),id)}
    finally{patch({busy:''},id)}
  }

  async function executeSql(action:'run'|'explain') {
    const id=page.id,text=page.sql.text
    if(busy||!text.trim()||sqlRequests.current.has(id))return
    sqlRequests.current.add(id)
    patch(current=>({busy:action,sql:{...current.sql,mode:action==='explain'?'explain':'result',error:''}}),id)
    try {
      const value=await api('sql/'+action,{sql:text,title:page.sql.title,previous_id:draft?.id})
      if(action==='explain')patch(current=>({sql:{...current.sql,plan:value,planSql:text}}),id)
      else {
        const d:Draft=value
        patch(current=>({draft:d,run:d.latest_run||null,preview:null,sql:{...current.sql,resultSql:text,error:d.latest_run?.error||''}}),id)
        await loadRecent()
      }
    }catch(e){patch(current=>({sql:{...current.sql,error:(e as Error).message}}),id)}
    finally{sqlRequests.current.delete(id);patch({busy:''},id)}
  }

  const currentResult=run?.status==='complete'?run.result:(!draft&&!busy?preview?.result:null)
  const navigationTitle=view==='sql'?'SQL workspace':view==='recent'?'Recent explorations':view==='area'?page.areaDetail?.name:view==='explore'?(draft?.proposal.title||selected[0]?.title):null
  const cubeWorkspace=view==='explore'&&!!page.cube&&!readOnly
  return <main className="data-app">
    <nav className="data-navigation" aria-label="Data navigation">
      <div className="data-nav-history">
        <button className="data-nav-back" onClick={()=>nav.go(-1)} disabled={!nav.canBack} aria-label="Back" title="Back"><Play size={16} weight="fill" aria-hidden="true"/></button>
        <button onClick={()=>nav.go(1)} disabled={!nav.canForward} aria-label="Forward" title="Forward"><Play size={16} weight="fill" aria-hidden="true"/></button>
      </div>
      <h1 className={navigationTitle?'':'data-nav-placeholder'} title={navigationTitle||undefined}>{navigationTitle||'Explore your company data'}</h1>
      <div className="data-nav-actions">
        <button className="data-nav-sql" aria-label="SQL workspace" title="SQL workspace" aria-pressed={view==='sql'} onClick={()=>openSql()}><Code size={17} aria-hidden="true"/><span>SQL</span></button>
        <button aria-label="Recent" title="Recent explorations" aria-pressed={view==='recent'} onClick={()=>{if(view!=='recent')push({view:'recent'});loadRecent().catch(fail)}}><ClockCounterClockwise size={19} aria-hidden="true"/></button>
        <button aria-label="Find data" title="Find data" aria-pressed={view==='discover'} onClick={()=>{if(view!=='discover')push(initialVisit());else searchInput.current?.focus()}}><MagnifyingGlass size={19} aria-hidden="true"/></button>
      </div>
    </nav>
    <div className={'data-body'+(['explore','sql'].includes(view)?' data-body-workspace':'')+(view==='sql'?' data-body-sql':'')} ref={nav.body}>
      {view==='sql'&&<SqlWorkbench value={page.sql} onChange={values=>patch(current=>({sql:{...current.sql,...values}}))}
        onRun={()=>executeSql('run')} onExplain={()=>executeSql('explain')} busy={!!busy} readOnly={false}
        hasResult={!!currentResult} canPublish={!!clean&&!!run&&run.status==='complete'&&!run.result.saved_snapshot&&context.can_publish}
        onPublish={publish} loadCatalog={signal=>api('sql/catalog',undefined,signal)} notify={notify}
        loading={busy?<DataLoading compact label={busy==='explain'?'Explaining your query…':busy==='publish'?'Adding to Stage…':'Running your query…'}/>:null}
        origin={<>{origin&&<aside className="data-origin">From <button onClick={()=>post({type:'calliope.data.origin',surface_id:origin.surface_id,session_id:origin.session_id})}>{origin.title} ↗</button><span> · Editing a copy</span></aside>}{readOnly&&<p className="query-read-only">{origin?.read_only_reason}</p>}</>}>
        {currentResult&&<DataGrid result={currentResult} label={currentResult.saved_snapshot?'Saved Stage result':clean?'Query result':'Previous result · SQL changed'}/>}
      </SqlWorkbench>}
      {view==='recent'&&<section className="history" aria-label="Recent explorations">
        {!recent.length&&<p>Your generated queries will appear here.</p>}
        <div className="data-tile-grid">{recent.map(d=><ExplorationTile key={d.id} draft={d} sources={items} disabled={!!busy} onOpen={()=>restore(d.id)}/>)}</div></section>}
      {view==='discover'?<>
        <form className="entry-search" onSubmit={e=>{e.preventDefault();discover()}}>
          <label className="search-label">Find data or ask a question<input className="workspace-field" ref={searchInput} type="search" value={q} maxLength={4000} disabled={!!busy}
            onChange={e=>patch({q:e.target.value,matches:null,pendingQuestion:'',searchNote:'',entryRouteId:null})}
            placeholder="Sales data, or total sales by region…" autoFocus/></label>
          <button className="workspace-control workspace-control-primary" type="submit" disabled={!!busy||!q.trim()}>{busy==='route'?'Finding the next step…':'Go'}</button>
        </form>
        {!q&&areas.length>1&&<label className="area-label">Filter by topic<select className="workspace-field" value={area} onChange={e=>patch({area:e.target.value})}><option value="">All topics</option>{areas.map(a=><option key={a} value={a}>{a.replace(/^subject:/,'').replaceAll('_',' ')}</option>)}</select></label>}
        <p className="muted">Explore an area, find a dataset, or describe the result you want.</p>
        {busy==='route'&&<DataLoading state="searching" label="Finding the right data for your request…"/>}
        {page.searchNote&&<p className="entry-note" role="status">{page.searchNote}</p>}
        <div className="search-status">{searching&&page.matches===null?<DataLoading compact state="searching" label="Finding data…"/>:<span>{filteredItems.length+filteredMetrics.length+filteredAreas.length} results</span>}</div>
        <section className="data-search-section" aria-label="Meaning areas">
          <h2>Areas <span>{filteredAreas.length}</span></h2>
          <div className="area-card-grid">{filteredAreas.map(a=><AreaTile key={a.key} area={a} onOpen={()=>openArea(a.key)}/>)}</div>
          {areaSearchError?<p className="muted" role="status">{areaSearchError}</p>:!searching&&!filteredAreas.length&&<p className="muted">{q||area?'No matching areas.':'No published Meaning areas yet. You can still explore the datasets below.'}</p>}
        </section>
        <section className="data-search-section" aria-label="Datasets">
          <h2>Datasets <span>{tables.length}</span></h2>
          <div className="dataset-list data-tile-grid">{tables.map(item=><DatasetTile key={item.relation} item={item} onOpen={()=>openDataset(item)}/>)}</div>
          {!searching&&!tables.length&&<p className="muted">No matching datasets. Try a broader term.</p>}
        </section>
        <section className="data-search-section metric-results" aria-label="Metrics">
          <h2>Metrics <span>{filteredMetrics.length}</span></h2>
          <div className="data-metric-grid">{filteredMetrics.map(metric=><MetricTile key={metric.name} metric={metric}/>)}</div>
          {metricSearchError?<p className="muted" role="status">{metricSearchError}</p>:!searching&&!filteredMetrics.length&&<p className="muted">{q||area?'No matching metrics.':'No saved metrics yet.'}</p>}
        </section>
        <section className="data-search-section" aria-label="Cubes">
          <h2>Cubes <span>{cubes.length}</span></h2>
          <div className="cube-list data-tile-grid">{cubes.map(item=><DatasetTile key={item.relation} item={item} onOpen={()=>openDataset(item)}/>)}</div>
          {!searching&&!cubes.length&&<p className="muted">{q||area?'No matching cubes.':'No cubes available yet.'}</p>}
        </section>
      </>:view==='area'?<section className="meaning-area" aria-label="Area exploration">
        {busy==='area'&&<DataLoading label="Opening this area…"/>}
        {!busy&&!page.areaDetail&&<p className="muted">This area could not be opened. Use Back to return to discovery.</p>}
        {page.areaDetail&&<>
          <p className="area-about">{page.areaDetail.about}</p>
          <nav className="area-links" aria-label="Area sections">{[['area-datasets','Data'],['area-questions','Questions'],['area-gaps','Open questions'],['area-metrics','Metric ideas'],['area-breakdowns','Breakdowns']].map(([id,label])=><button key={id} onClick={()=>document.getElementById(id)?.scrollIntoView({block:'start',behavior:'smooth'})}>{label}</button>)}</nav>
          <form className="area-composer" aria-label="Area query" onSubmit={e=>{e.preventDefault();queryArea(question)}}>
            <label>What would you like to see?<textarea className="workspace-field" value={question} maxLength={4000} disabled={!!busy} onChange={e=>patch({question:e.target.value})} rows={2} placeholder={`Ask about ${page.areaDetail.name.toLowerCase()}…`}/></label>
            <div className="actions"><span className="muted">{page.areaTopic||'Across this area'} · relevant datasets chosen for your question</span><button className="workspace-control workspace-control-primary" disabled={!!busy||!question.trim()}>Show results</button></div>
          </form>
          {!!page.areaDetail.subareas.length&&<section className="data-search-section area-topics" aria-label="Subtopics">
            <h2>Explore a subtopic</h2><div className="area-topic-list"><button aria-pressed={!page.areaTopic} onClick={()=>patch({areaTopic:''})}>Whole area</button>{page.areaDetail.subareas.map(s=><button key={s.name} aria-pressed={page.areaTopic===s.name} onClick={()=>patch({areaTopic:s.name})}>{s.name}</button>)}</div>
            {page.areaTopic&&<p className="area-topic-about">{page.areaDetail.subareas.find(s=>s.name===page.areaTopic)?.about}</p>}
          </section>}
          <section id="area-datasets" className="data-search-section" aria-label="Area datasets"><h2>Datasets</h2>
            <div className="data-tile-grid">{page.areaDetail.datasets.filter(d=>!page.areaTopic||page.areaDetail!.subareas.find(s=>s.name===page.areaTopic)?.tables.includes(d.relation)).map(d=><DatasetTile key={d.relation} item={d} onOpen={()=>openDataset(d)}/>)}</div>
          </section>
          {!!page.areaDetail.relationships?.length&&<section className="data-search-section" aria-label="Known relationships"><h2>Known relationships</h2><p className="muted">Recorded by Meaning; check grain and caveats when combining datasets.</p><div className="area-question-list">{page.areaDetail.relationships.map((r,i)=><article className="area-question" key={i}><small>{r.source}</small><p>{r.description}</p></article>)}</div></section>}
          <section id="area-questions" className="data-search-section" aria-label="Sample questions"><h2>Questions to explore <span>{page.areaDetail.questions.length}</span></h2>
            <p className="muted">Show results here, or bring a question to Callie for a deeper investigation.</p>
            <div className="area-question-list">{page.areaDetail.questions.map((s,i)=><article className="area-question" key={i}><p>{s}</p><div><button disabled={!!busy} onClick={()=>queryArea(s)}>Show results</button><button disabled={!!busy} title="Prepare a Chat draft with this area’s context" onClick={()=>askCallie('question',i)}>Ask Callie</button></div></article>)}</div>
          </section>
          {!!page.areaDetail.metrics.length&&<section className="data-search-section metric-results" aria-label="Area metrics"><h2>Saved metrics</h2><div className="data-metric-grid">{page.areaDetail.metrics.map(m=><MetricTile key={m.name} metric={m}/>)}</div></section>}
          {!!page.areaDetail.cubes.length&&<section className="data-search-section" aria-label="Area cubes"><h2>Cubes</h2><div className="data-tile-grid">{page.areaDetail.cubes.map(d=><DatasetTile key={d.relation} item={d} onOpen={()=>openDataset(d)}/>)}</div></section>}
          {page.areaDetail.related_error&&<p className="muted">{page.areaDetail.related_error}</p>}
          <section id="area-gaps" className="data-search-section" aria-label="Open questions"><h2>What we still don’t know <span>{page.areaDetail.gaps.length}</span></h2>
            <p className="muted">Bring these to Callie to investigate and enrich Meaning with what you learn.</p>
            <div className="area-question-list">{page.areaDetail.gaps.map((s,i)=><article className="area-question" key={i}><p>{s}</p><div><button disabled={!!busy} title="Prepare a Chat draft with this area’s context" onClick={()=>askCallie('gap',i)}>Ask Callie</button></div></article>)}</div>
            {!page.areaDetail.gaps.length&&<p className="muted">No open questions recorded in this publication.</p>}
          </section>
          <section id="area-metrics" className="data-search-section" aria-label="Metric ideas"><h2>Metric ideas <span>{page.areaDetail.candidate_metrics.length}</span></h2>
            <p className="muted">One headline value for a scope and period. Suggestions from Meaning; these are not saved metric definitions.</p>
            <div className="area-question-list">{page.areaDetail.candidate_metrics.map((m,i)=><article className="area-question area-metric-idea" key={i}><h3>{m.name}</h3><p>{m.why}</p><p className="muted">{m.formula}</p><small>{m.grain}</small><div>
              <button disabled={!!busy} title="Explore this proposed calculation across the area’s datasets" onClick={()=>queryArea(metricIdeaRequest(m),'')}><Database size={14} aria-hidden="true"/>Try in Data</button>
              <button disabled={!!busy} title="Prepare a Chat draft with the proposal, grain, purpose and area context" onClick={()=>askCallie('metric',i)}><ChatTeardropText size={14} aria-hidden="true"/>Explore with Callie</button>
            </div></article>)}</div>
          </section>
          {!!page.areaDetail.candidate_breakdowns?.length&&<section id="area-breakdowns" className="data-search-section" aria-label="Breakdowns"><h2>Breakdowns <span>{page.areaDetail.candidate_breakdowns.length}</span></h2>
            <p className="muted">Named comparisons with a purpose. Explore the groups, then refine the story they tell.</p>
            <div className="area-question-list">{page.areaDetail.candidate_breakdowns.map((b,i)=><article className="area-question area-breakdown" key={i}>
              <h3>{b.name}</h3><p>{b.purpose}</p>
              <dl><dt>Measures</dt><dd>{b.measures.join('; ')}</dd><dt>Group by</dt><dd>{b.dimensions.join(' · ')}</dd>{b.grain&&<><dt>Each row</dt><dd>{b.grain}</dd></>}</dl>
              {b.caveats&&<small>{b.caveats}</small>}
              <div><button disabled={!!busy} title="Explore this grouped comparison across the area’s datasets" onClick={()=>queryArea(breakdownRequest(b),'')}><Database size={14} aria-hidden="true"/>Try in Data</button>
                <button disabled={!!busy} title="Prepare a Chat draft with the breakdown’s purpose, measures and grouping" onClick={()=>askCallie('breakdown',i)}><ChatTeardropText size={14} aria-hidden="true"/>Explore with Callie</button></div>
            </article>)}</div>
          </section>}
          <p className="muted">Published Meaning · {new Date(page.areaDetail.published_at).toLocaleString()}</p>
        </>}
      </section>:cubeWorkspace&&page.cube?<CubeWorkbench value={page.cube}
        onChange={spec=>push({cube:{...page.cube!,spec,applied:''},cubeError:'',busy:''})}
        probe={(field,search)=>api('cubes/values',{relation:page.cube!.catalog.relation,spec:page.cube!.spec,field,search})}
        toolbar={<>
          <button aria-label="Refresh results" title="Refresh results" disabled={!!busy||!clean} onClick={refresh}><ArrowClockwise size={17}/></button>
          <button aria-label="View SQL" title="View SQL" aria-pressed={cubeTool==='sql'} disabled={!draft?.proposal.sql} onClick={()=>setCubeTool(cubeTool==='sql'?null:'sql')}><Code size={18}/></button>
          <button className="data-sql-escape" aria-label="Edit a copy in SQL" title="Edit a copy in SQL" disabled={!!busy||!draft?.proposal.sql} onClick={()=>openSql(true)}><Code size={13}/>↗</button>
          <button aria-label="Describe a different result" title="Describe a different result" aria-pressed={cubeTool==='question'} onClick={()=>setCubeTool(cubeTool==='question'?null:'question')}><ChatTeardropText size={18}/></button>
          <button aria-label="About this cube" title="About this cube" aria-pressed={cubeTool==='about'} onClick={()=>setCubeTool(cubeTool==='about'?null:'about')}><Info size={18}/></button>
          <button className="data-publish" disabled={!!busy||!clean||!context.can_publish||!run||run.result.saved_snapshot} onClick={publish}>{busy==='publish'?'Adding…':'Add to Stage'}</button>
        </>}>
        {origin&&<aside className="data-origin"><span>From </span><button title={`Saved ${new Date(origin.observed_at).toLocaleString()}. Edits create a new result.`} onClick={()=>post({type:'calliope.data.origin',surface_id:origin.surface_id,session_id:origin.session_id})}>{origin.title}{origin.turn_ordinal?` · Turn ${origin.turn_ordinal}`:''} ↗</button></aside>}
        {cubeTool&&<section className="data-inspector" aria-label={cubeTool==='sql'?'Query SQL':cubeTool==='question'?'Describe a different result':'About this cube'}>
          <header><strong>{cubeTool==='sql'?'View SQL':cubeTool==='question'?'Describe a different result':page.cube.catalog.title}</strong><button aria-label="Close details" onClick={()=>setCubeTool(null)}><X size={16}/></button></header>
          {cubeTool==='sql'&&draft?.proposal.sql&&<SqlPreview sql={draft.proposal.sql}/>}
          {cubeTool==='about'&&<><p>{selected[0]?.description||page.cube.catalog.grain}</p><p className="muted">{page.cube.catalog.relation} · {page.cube.catalog.grain}</p><p className="muted">Queries use all matching cube rows. Ratios and averages are recomputed from their component totals.</p></>}
          {cubeTool==='question'&&<form onSubmit={e=>{e.preventDefault();build()}}><label>What would you like to see?<textarea className="workspace-field" value={question} maxLength={4000} onChange={e=>patch({question:e.target.value})} rows={2}/></label><button className="workspace-control" disabled={!!busy||!question.trim()}>Update results</button></form>}
        </section>}
        {page.cubeError&&<p className="query-error" role="alert">{page.cubeError} The previous result is still shown.</p>}
        {!!busy&&<DataLoading compact label={busy==='publish'?'Adding to Stage…':'Updating cube summary…'}/>}
        {currentResult?<DataGrid key={page.id+':'+run?.id} result={currentResult} label={run?.result.saved_snapshot?'Saved Stage result':clean?'Query result':'Previous result · request changed'}/>:<div className="cube-result-empty">{page.cube.spec.measures.length?'Preparing your summary…':'Add a measure to see your data.'}</div>}
      </CubeWorkbench>:view==='explore'?<section className="query-workbench" aria-label={draft?'Query workspace':'Dataset workspace'}>
        {page.areaKey&&<aside className="data-origin"><span>Area </span><button onClick={()=>openArea(page.areaKey!)}>{draft?.context?.meaning_area?.name||page.areaDetail?.name||'Explore this area'} ↗</button></aside>}
        <div className="data-toolbar" aria-label="Data actions">
          <button aria-label="Datasets and fields" title="Datasets and fields" aria-pressed={queryTool==='sources'} onClick={()=>setQueryTool(queryTool==='sources'?null:'sources')}><Database size={17}/><span>Data{selected.length>1?` · ${selected.length}`:''}</span></button>
          {draft?.proposal.sql&&<button aria-label="View SQL" title="View SQL" aria-pressed={queryTool==='sql'} onClick={()=>setQueryTool(queryTool==='sql'?null:'sql')}><Code size={18}/></button>}
          {(draft?.proposal.sql||preview)&&<button className="data-sql-escape" aria-label="Edit a copy in SQL" title="Edit a copy in SQL" disabled={!!busy} onClick={()=>openSql(true)}><Code size={13}/>↗</button>}
          {draft&&<button aria-label="Query details" title="Query details" aria-pressed={queryTool==='about'} onClick={()=>setQueryTool(queryTool==='about'?null:'about')}><Info size={18}/></button>}
          {!readOnly&&draft?.proposal.sql&&<button aria-label="Refresh results" title="Refresh results" disabled={!!busy||!clean} onClick={refresh}><ArrowClockwise size={17}/></button>}
          {!readOnly&&run?.status==='complete'&&!run.result.saved_snapshot&&<button className="data-publish" disabled={!!busy||!clean||!context.can_publish} onClick={publish}>{busy==='publish'?'Adding…':'Add to Stage'}</button>}
        </div>
        <div className={'query-canvas'+(queryTool==='sources'?' query-canvas-sources':'')}>
          {queryTool==='sources'&&<aside className="query-sources data-inspector" aria-label="Datasets and fields">
            <header><strong>Datasets & fields</strong><button aria-label="Close details" onClick={()=>setQueryTool(null)}><X size={16}/></button></header>
            <div className="query-source-list">{selected.map(item=>{
              const data=preview?.dataset.relation===item.relation?preview.dataset:item.columns?item:items.find(candidate=>candidate.relation===item.relation)||item
              return <section className="query-source" key={item.relation}>
                <header><h2>{data.title}</h2>{!readOnly&&selected.length>1&&<button aria-label={`Remove ${data.title}`} title={`Remove ${data.title}`} disabled={!!busy} onClick={()=>push({selected:selected.filter(s=>s.relation!==item.relation),run:null})}><X size={14}/></button>}</header>
                <small>{data.relation}</small>
                {data.description&&<p>{data.description}</p>}
                {Array.isArray(data.meaning?.caveats)&&data.meaning.caveats.map((c,i)=><p className="assumption" key={i}>{String((c as {text?:string}).text||'')}</p>)}
                {!!data.columns?.length&&<dl className="field-list">{data.columns.map(c=><React.Fragment key={c.name}><dt>{c.name}</dt><dd>{c.description||c.type}</dd></React.Fragment>)}</dl>}
              </section>
            })}</div>
            {!readOnly&&!page.cube&&!page.areaKey&&<label className="query-add-source">Add another dataset<select className="workspace-field" aria-label="Add another dataset" value="" disabled={!!busy||selected.length>=4} onChange={e=>{const item=items.find(i=>i.relation===e.target.value);if(item)push({selected:[...selected,item],run:null})}}>
              <option value="">Choose a dataset…</option>{items.filter(i=>!selected.some(s=>s.relation===i.relation)).map(i=><option key={i.relation} value={i.relation}>{i.title}</option>)}</select></label>}
          </aside>}
          <div className="query-result">
            {origin&&<aside className="data-origin"><span>From </span><button title={`Saved ${new Date(origin.observed_at).toLocaleString()}. Edits create a new result.`} onClick={()=>post({type:'calliope.data.origin',surface_id:origin.surface_id,session_id:origin.session_id})}>{origin.title}{origin.turn_ordinal?` · Turn ${origin.turn_ordinal}`:''} ↗</button></aside>}
            {readOnly&&<p className="query-read-only">{origin?.read_only_reason}</p>}
            {(queryTool==='sql'||queryTool==='about')&&<section className="data-inspector" aria-label={queryTool==='sql'?'Query SQL':'Query details'}>
              <header><strong>{queryTool==='sql'?'View SQL':'Query details'}</strong><button aria-label="Close details" onClick={()=>setQueryTool(null)}><X size={16}/></button></header>
              {queryTool==='sql'&&draft?.proposal.sql&&<SqlPreview sql={draft.proposal.sql}/>}
              {queryTool==='about'&&<><p>{draft?.proposal.summary||draft?.question}</p>{run?.status==='complete'&&<p className="muted">Saved {new Date(run.completed_at).toLocaleString()} · {context.session_id?`Stage: ${context.title||'current notebook'}`:'Adding creates a new notebook'}</p>}</>}
            </section>}
            {!!draft?.proposal.assumptions?.length&&<div className="query-assumptions" aria-label="Query assumptions">{draft.proposal.assumptions.map(a=><p className="assumption" key={a}>{a}</p>)}</div>}
            {(draft?.error||run?.error)&&!readOnly&&<p className="query-error" role="alert">{draft?.error||run?.error}</p>}
            {draft?.proposal.clarification&&<section className="clarification" aria-label="Query clarification"><p>{draft.proposal.clarification.question}</p><div className="clarification-options">{draft.proposal.clarification.options.map(o=><button className="workspace-control" disabled={!!busy||!inputsCurrent||readOnly} key={o} onClick={()=>build(o)}>{o}</button>)}</div>
              <form onSubmit={e=>{e.preventDefault();if(answer.trim())build(answer.trim())}}><label>Your answer<input className="workspace-field" value={answer} maxLength={2000} disabled={!!busy||!inputsCurrent||readOnly} onChange={e=>patch({answer:e.target.value})} placeholder="Or answer in your own words…"/></label>
                <button className="workspace-control" type="submit" disabled={!!busy||!inputsCurrent||readOnly||!answer.trim()}>Use answer</button></form>
            </section>}
            {!!busy&&<DataLoading compact state={busy==='build'||busy==='refine'?'composing':'working'} label={({preview:'Loading a preview…',restore:'Opening saved exploration…',refine:'Updating your request and rebuilding…',build:'Building your query…',run:'Running your query…',publish:'Adding to Stage…'} as Record<string,string>)[busy]||'Loading data…'}/>}
            {currentResult?<DataGrid key={page.id+':'+(run?.id||preview?.dataset.relation)} result={currentResult} label={run?(run.result.saved_snapshot?'Saved Stage result':clean?'Query result':'Previous result · request changed'):'Dataset preview'}/>:<div className="query-result-empty">{busy?'':draft?.proposal.clarification?'Answer the question above to continue.':'Describe the result you want below.'}</div>}
            {!readOnly&&!page.cube&&<form className="query-composer" aria-label="Query request" onSubmit={e=>{e.preventDefault();build()}}>
              <label>What would you like to see?<textarea className="workspace-field" value={question} maxLength={4000} disabled={!!busy} onChange={e=>patch({question:e.target.value})} placeholder="For example: total sales by region for 2023" rows={2}/></label>
              <div className="actions"><span className="muted">{draft&&!inputsCurrent?'Update results to apply your changes.':''}</span><button className="workspace-control workspace-control-primary" disabled={!!busy||!question.trim()||!selected.length}>{busy==='refine'?'Updating request…':busy==='build'?'Building query…':busy==='run'?'Running query…':draft?'Update results':'Show results'}</button></div>
            </form>}
            {!readOnly&&!page.cube&&selected.length===1&&selected[0].catalog_kind==='cube'&&<button className="workspace-control" onClick={summarize} disabled={!!busy}>Summarize with shelves</button>}
          </div>
        </div>
      </section>:null}
    </div>
  </main>
}

function StageResult() {
  const [result,setResult]=useState<Result|null>(null)
  useEffect(()=>{
    const receive=(e:MessageEvent)=>{
      if(e.origin!==location.origin||e.source!==window.parent||e.data?.type!=='calliope.data.result')return
      setResult(e.data.result)
      applyTheme(e.data.theme)
    }
    window.addEventListener('message',receive)
    document.body.classList.add('data-viewer')
    post({type:'calliope.data.result.ready'})
    return()=>window.removeEventListener('message',receive)
  },[])
  return result?<DataGrid result={result} label="Saved query result" stage/>:<DataLoading label="Loading saved result…"/>
}

applyTheme()
createRoot(document.getElementById('data-root')!).render(new URLSearchParams(location.search).get('view')==='result'?<StageResult/>:<App/> )
