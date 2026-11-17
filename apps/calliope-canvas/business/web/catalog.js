import {ScrollArea,RecordItem,ReactiveRegion,ResponsiveStack} from './regions.js';
import {Field,Attachments,AttachmentHost} from './fields.js';
import {safeLink} from './links.js';
import {recordHref} from './record-links.js';
import {ResourceDay} from './resource-day.js';
import {DataGrid} from './grid.js';
import {ResultTable} from './result-table.js';
import {Markdown} from './markdown.js';
import React from 'react';
const VegaChart=React.lazy(()=>import('./vega-chart.js').then(m=>({default:m.VegaChart})));
import {z} from 'zod';
import {Catalog, componentId, markChildRef} from '@a2ui/web_core/v0_9';
import {basicCatalog, createComponentImplementation} from '@a2ui/react/v0_9';
import spec from '../catalog.json' with {type:'json'};

const h=React.createElement;
const binding=z.union([z.string(),z.object({path:z.string()}).strict()]).describe('REF:#/$defs/DynamicString');
const fields={scrollHeight:z.union([z.literal('available'),z.number().int().min(160).max(1000)]),scrollMinimum:z.number().int().min(160).max(600),filterEvent:z.string().regex(/^(filter|[a-z][a-z0-9_]*_filter)$/),filterDelay:z.number().int().min(100).max(1000),eventName:z.string().regex(/^[A-Za-z][A-Za-z0-9_]{0,99}$/),resources:z.array(z.string().max(100)).min(1).max(12),positions:z.array(z.object({lane:z.number().int().min(0).max(11),start:z.number().int().min(0).max(1440),end:z.number().int().min(0).max(1440)}).strict()).max(100),minute:z.number().int().min(0).max(1440),dataBinding:z.union([z.object({path:z.string()}).strict(),z.never()]).describe("REF:#/$defs/DynamicValue"), booleanBinding:z.union([z.boolean(),z.object({path:z.string()}).strict()]).describe("REF:#/$defs/DynamicBoolean"), literalText:z.string(), precision:z.number().int().min(0).max(4), maxLength:z.number().int().min(1).max(10000), rowKeys:z.array(z.string()).max(100), boolean:z.boolean(), columns:z.array(z.object({label:z.string(),weight:z.number().positive().max(12).optional()}).strict()).min(1).max(8), options:z.array(z.object({label:z.string(),value:z.string()}).strict()).min(1).max(100), stringListBinding:z.union([z.array(z.string()),z.object({path:z.string()}).strict()]).describe('REF:#/$defs/DynamicStringList'), objectBinding:z.union([z.object({path:z.string().startsWith("/")}).strict(),z.never()]).describe("REF:#/$defs/DynamicValue"), chartHeight:z.number().int().min(180).max(960), text:binding, weight:z.number().positive().max(12), fraction:z.number().min(0).max(1),
  child:componentId(), children:markChildRef(z.array(componentId()).describe('REF:#/$defs/ChildList'),'child-list')};
fields.resultColumns=z.array(z.object({name:z.string().min(1).max(200),type:z.string().optional(),format:z.enum(['text','integer','plain','decimal','boolean','time','json']).optional()}).strict()).min(1).max(256);
fields.count=z.number().int().min(0);
fields.visibleRows=z.number().int().min(3).max(50);
const weight=p=>p.weight ? {flex:`${p.weight} 1 0`, minWidth:0} : {minWidth:0};
fields.recordReference=z.union([z.object({path:z.string().startsWith('/')}).strict(),z.object({slug:z.string().regex(/^[a-zA-Z0-9_-]{1,160}$/),record:z.string().regex(/^[a-zA-Z0-9_-]{1,160}$/),id:z.union([z.string().min(1).max(512),z.number().int().safe()])}).strict(),z.null()]).describe('REF:#/$defs/DynamicValue');
function RecordPicker({props:p,context}) {
  const id=React.useId();
  const hasEmpty=p.options.some(o=>o.value==='');
  return h('div',{className:'c-picker',style:weight(p)},h('label',{htmlFor:id},p.label),h('select',{id,'data-field-id':context.componentModel.id,value:p.value?.[0]||'',onChange:e=>p.setValue(!hasEmpty&&e.target.value===''?[]:[e.target.value])},!hasEmpty&&h('option',{value:''},'Please select'),p.options.map(o=>h('option',{key:o.value,value:o.value},o.label))));
}
function ResponsiveSection({props:p,buildChild}) {
  const [phone,setPhone]=React.useState(()=>window.matchMedia('(max-width:600px)').matches);
  const [open,setOpen]=React.useState(false);
  React.useEffect(()=>{const q=window.matchMedia('(max-width:600px)');const change=()=>setPhone(q.matches);q.addEventListener('change',change);return()=>q.removeEventListener('change',change);},[]);
  React.useEffect(()=>setOpen(false),[p.summary]);
  if(p.collapse==='phone' && phone) return h('section',{className:'c-section compact-section',style:weight(p)},
    h('button',{type:'button',className:'compact-section-heading','aria-expanded':open,onClick:()=>setOpen(!open)},h('span',null,p.title,p.summary&&h('small',null,p.summary)),h('span',null,open?'Hide':'Choose')),
    open&&buildChild(p.child));
  return h('section',{className:`c-section tone-${p.tone||'neutral'}`,'aria-label':p.title,style:weight(p)},h('div',{className:'section-heading'},h('h2',null,p.title),p.subtitle&&h('p',null,p.subtitle)),buildChild(p.child));
}
const renders={
  CRecordLink:({props:p})=>{const host=React.useContext(AttachmentHost),href=recordHref(p.reference);return h('a',{className:'c-link',href:host.preview?undefined:href,'aria-disabled':host.preview||!href,style:weight(p),onClick:e=>{e.preventDefault();if(!host.preview&&href)host.openRecord?.(p.reference);},title:host.preview?'Record navigation is simulated in previews':undefined},p.label,' →');},
  CLink:({props:p})=>{const host=React.useContext(AttachmentHost),href=safeLink(p.href);return h('a',{className:'c-link',href:host.preview?undefined:href,target:'_blank',rel:'noopener noreferrer','aria-disabled':host.preview||!href,style:weight(p),title:host.preview?'Browser links are disabled in previews':undefined},p.label,' ↗');},
  CScrollArea:ScrollArea, CRecordItem:RecordItem, CReactiveRegion:ReactiveRegion,
  CResourceDay:ResourceDay,
  CField:Field,
  CAttachments:Attachments,
  CRecordPane:({props:p,buildChild})=>h('aside',{className:'c-record-pane','aria-label':p.title},h('div',{className:'text-eyebrow ink-cool'},p.title),buildChild(p.child)),
  CRecordPicker:RecordPicker,
  CDataGrid:DataGrid,
  CResultTable:ResultTable,
  CMarkdown:Markdown,
  CMetric:({props:p})=>h('div',{className:'c-metric'},p.status&&h('div',{className:'text-eyebrow ink-accent'},p.status),h('div',{className:'c-metric-row'},h('span',{className:'c-metric-value font-display'},p.value),p.delta&&h('span',{className:`c-metric-delta meaning-${p.meaning||'neutral'}`},p.delta)),p.caption&&h('div',{className:'muted c-metric-caption'},p.caption)),
  CVegaChart:({props:p,context})=>h(React.Suspense,{fallback:h('div',{className:'muted',style:{height:p.height||260}},'Loading charts…')},h(VegaChart,{...p,context})),
  CFilterBar:({props:p,buildChild})=>h('section',{className:'c-filter-bar','aria-label':p.label},h('div',{className:'filter-heading'},p.label),h('div',{className:'c-filter-controls'},p.children.map(id=>h(React.Fragment,{key:id},buildChild(id))))),
  CSection:ResponsiveSection,
  CStack:ResponsiveStack,
  CSurface:({props:p,buildChild})=>h('section',{className:`c-surface tone-${p.tone||'neutral'} treatment-${p.treatment||'soft'} pad-${p.padding||'md'}`,style:weight(p)},buildChild(p.child)),
  CText:({props:p})=>h(p.size==='display'?'h1':p.size==='title'?'h3':'div',{className:`c-text text-${p.size||'body'} ink-${p.tone||'normal'} font-${p.family||'body'}`,style:weight(p)},p.text),
  CBadge:({props:p})=>h('span',{className:`c-badge tone-${p.tone||'neutral'}`},p.text),
  CMeter:({props:p})=>h('div',{className:`c-meter tone-${p.tone||'accent'}`},h('div',{className:'meter-label'},h('span',null,p.label),h('span',null,`${Math.round(p.value*100)}%`)),h('div',{className:'meter-track',role:'progressbar','aria-label':p.label,'aria-valuenow':Math.round(p.value*100),'aria-valuemin':0,'aria-valuemax':100},h('div',{className:'meter-fill',style:{width:`${p.value*100}%`}}))),
  CArtwork:({props:p})=>h('figure',{className:`c-artwork artwork-${p.size||'banner'}`,style:weight(p)},h('img',{src:new URL('../assets/neighborhood.png',import.meta.url).href,alt:'Illustrated neighborhood with homes and trees',loading:'eager'}),h('figcaption',null,'Neighborhood study / concept artwork')),
};
export const customComponents=Object.entries(spec.components).map(([name,properties])=>{
  const shape={};
  for(const [key,kind] of Object.entries(properties)) {
    let schema=Array.isArray(kind)?z.enum(kind):fields[kind];
    if(!['child','children','text','value','label','href','reference','asset','spec','description','columns','rows','options','onChange','title','recordId','selectedId','status','revision'].includes(key)) schema=schema.optional();
    shape[key]=schema;
  }
  return createComponentImplementation({name,schema:z.object(shape).strict()},renders[name]);
});
export const workspaceCatalog=new Catalog(spec.id,'0.9', [...basicCatalog.components.values(),...customComponents], [...basicCatalog.functions.values()], basicCatalog.themeSchema);
