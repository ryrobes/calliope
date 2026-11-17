import {at,clone,equal} from './continuity.js';
import {fieldError} from './fields.js';

export class LiveFilters {
  install(nodes,data){
    this.definition=nodes.find(n=>n.component==='CFilterBar');
    this.fields=nodes.filter(n=>n.value?.path?.startsWith(this.definition?.value.path+'/'));
    this.seen=clone(this.definition?at(data,this.definition.value.path):null);this.due=null;
  }
  observe(data,now){
    if(!this.definition)return;
    const value=at(data,this.definition.value.path);
    if(equal(value,this.seen))return;
    this.seen=clone(value);
    const valid=value&&typeof value==='object'&&!Array.isArray(value)&&this.fields.every(f=>{
      const v=at(data,f.value.path);
      if(f.component==='CField')return !fieldError(v,f);
      if(['ChoicePicker','CRecordPicker'].includes(f.component))return Array.isArray(v)&&v.length>0;
      return true;
    });
    this.due=valid?now+Math.max(100,Math.min(1000,this.definition.debounceMs??250)):null;
  }
  poll(now){if(this.due==null||now<this.due)return null;this.due=null;return {name:this.definition.onChange,context:clone(this.seen)};}
  pending(){return this.due!=null;}
  cancel(){this.due=null;}
  matches(submitted,data){return !!this.definition&&equal(submitted,at(data,this.definition.value.path));}
}
