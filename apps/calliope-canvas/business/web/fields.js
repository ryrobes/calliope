import React from 'react';
import * as device from './outbox.js';
const h=React.createElement;
export function fieldError(value,p){
  if(p.visible===false||p.readOnly)return '';
  const v=String(value??'').trim();
  if(!v)return p.required?'This field is required.':'';
  if(String(value).length>(p.maxLength??4000))return `Use at most ${p.maxLength??4000} characters.`;
  if(['money','quantity'].includes(p.kind)){
    if(!/^-?\d+(?:\.\d+)?$/.test(v))return 'Enter a number without commas or symbols.';
    if((v.split('.')[1]?.length||0)>(p.precision??2))return `Use at most ${p.precision??2} decimal places.`;
    if(p.minimum!==undefined&&Number(v)<Number(p.minimum))return `Must be at least ${p.minimum}.`;
    if(p.maximum!==undefined&&Number(v)>Number(p.maximum))return `Must be no more than ${p.maximum}.`;
  }else if(p.kind==='date'){
    const d=new Date(v+'T00:00:00Z');
    if(!/^\d{4}-\d{2}-\d{2}$/.test(v)||!Number.isFinite(d.valueOf())||d.toISOString().slice(0,10)!==v)return 'Use a real date in YYYY-MM-DD format.';
    if(p.minimum&&v<p.minimum)return `Must be on or after ${p.minimum}.`;
    if(p.maximum&&v>p.maximum)return `Must be on or before ${p.maximum}.`;
  }else if(p.kind==='time'&&!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(v))return 'Use a time in HH:MM format.';
  return '';
}
export const GridContext=React.createContext(null);
export const FieldResetContext=React.createContext(0);
export function Field({props:p,context}){
  const id=context.componentModel.id;const htmlId=React.useId(),grid=React.useContext(GridContext);
  const [edited,setEdited]=React.useState(false),reset=React.useContext(FieldResetContext);
  React.useEffect(()=>setEdited(false),[reset]);
  React.useEffect(()=>{if(!grid)return;grid.current.set(id,{setValue:p.setValue,props:p});return()=>grid.current.delete(id);},[grid,id,p]);
  if(p.visible===false)return null;
  const local=fieldError(p.value,p),error=p.error||((edited||String(p.value??'')!=='')?local:'');
  const props={id:htmlId,'data-field-id':id,value:p.value??'',readOnly:p.readOnly,'aria-invalid':!!error,'aria-describedby':error?htmlId+'-error':p.help?htmlId+'-help':undefined,'aria-required':!!p.required,maxLength:p.maxLength??4000,
    onChange:e=>{setEdited(true);if(!p.error?.startsWith('Changed elsewhere'))p.setError?.('');p.setValue(e.target.value);},onBlur:()=>setEdited(true),
    inputMode:['money','quantity'].includes(p.kind)?(p.precision===0?'numeric':'decimal'):undefined,
    placeholder:p.kind==='date'?'YYYY-MM-DD':p.kind==='time'?'HH:MM':undefined};
  return h('div',{className:'c-field',style:p.weight?{flex:`${p.weight} 1 0`}:undefined},h('label',{htmlFor:htmlId},p.label,p.required?' *':''),h('div',{className:'c-field-entry'},h(p.kind==='longText'?'textarea':'input',{...props,type:'text'}),p.unit&&h('span',{className:'c-field-unit'},p.unit)),error&&h('div',{id:htmlId+'-error',className:'field-error',role:'alert'},error),p.help&&h('div',{id:htmlId+'-help',className:'field-help'},p.help));
}
export const AttachmentHost=React.createContext({upload:'/api/attachments',download:id=>'/api/attachments/'+encodeURIComponent(id),pending:()=>{}});
export function Attachments({props:p,context}){
  const host=React.useContext(AttachmentHost);
  const [error,setError]=React.useState(''),[busy,setBusy]=React.useState(false),[localNames,setNames]=React.useState({});
  React.useEffect(()=>{if(p.offline)device.all('files').then(files=>setNames(Object.fromEntries(files.map(f=>[f.id,f.name])))).catch(e=>setError(e.message));},[p.offline]);
  const names={...(p.names?.value??p.names??{}),...localNames};
  const upload=async e=>{
    if(host.preview){e.target.value='';setError('File uploads are disabled in this draft preview. Test attachments in the published test app.');return;}
    const files=[...e.target.files];e.target.value='';if(!files.length)return;
    if((p.value?.length||0)+files.length>(p.offline?3:5)){setError(p.offline?'Choose at most three files.':'Choose at most five files.');return;}
    setBusy(true);host.pending(true);p.setPending?.(true);setError('');let ids=[...(p.value||[])];
    try{for(const file of files){
      if(p.offline){const record=await device.capture(file);ids.push(record.id);setNames(n=>({...n,[record.id]:record.name}));p.setValue(ids);continue;}
      if(file.size>5*1024*1024)throw Error('Files must be at most 5 MB.');
      const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
      const response=await fetch(host.upload,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:file.name,mime:file.type||'text/plain',data,...(host.published?{request_key:crypto.randomUUID()}: {})})});
      const record=await response.json();if(!response.ok)throw Error(record.error?.message||record.error);
      ids.push(record.id);setNames(n=>({...n,[record.id]:record.name}));p.setValue(ids);
    }}catch(e){setError(e.message);}finally{setBusy(false);host.pending(false);p.setPending?.(false);}
  };
  return h('div',{className:'c-attachments'},h('label',null,p.label,!p.readOnly&&h('input',{type:'file',multiple:true,accept:'.png,.jpg,.jpeg,.pdf,.txt',disabled:busy||host.preview,onChange:upload})),p.offline&&h('label',{className:'camera-capture'},'Take photo',h('input',{type:'file',accept:'image/*',capture:'environment',disabled:busy||host.preview,onChange:upload})),!p.readOnly&&h('div',{className:'field-help'},host.preview?'Draft preview · file uploads disabled':busy?(p.offline?'Saving evidence on this device…':'Uploading attachment…'):p.offline?'Saved on this device · 3 files, 2 MB each':'PNG, JPEG, PDF or text · up to 5 files, 5 MB each'),(p.value||[]).map(id=>h('div',{key:id,className:'attachment-row'},p.offline||host.preview?h('span',null,names[id]||id):h('a',{href:host.download(id),target:'_blank',rel:'noreferrer'},names[id]||id),!p.readOnly&&h('button',{type:'button',onClick:async()=>{try{if(p.offline&&!host.preview)await device.remove('files',id);p.setValue(p.value.filter(x=>x!==id));}catch(e){setError(e.message);}},'aria-label':'Remove '+(names[id]||id)},'Remove'))),error&&h('div',{className:'field-error',role:'alert'},error));
}
