export const leaf = (label='New outcome') => ({kind:'leaf',label});
export const binary = () => ({kind:'branch',question:'What question should this branch answer?',threshold:.5,yes:leaf('Yes'),no:leaf('No')});
export const choice = () => ({kind:'choice',question:'Which description best fits?',choices:{first:'First possibility',second:'Second possibility',unclear:'Not enough information'},routes:{first:leaf('First outcome'),second:leaf('Second outcome'),unclear:leaf('Needs clarification')}});
export const blank = () => ({name:'',topic:'',source_sql:'SELECT \'Describe the request here\'::text AS customer_message',tree:leaf('Answer'),examples:[]});
export function namedSql(key, sourceClause, version=null){
 const rule="'"+key.replaceAll("'","''")+"'";
 return `SELECT rvbbit.business_logic(${rule}, to_jsonb(r)${version===null?'':', '+version}) AS decision\n${sourceClause}`;
}
const exampleDefinition = () => ({name:'HVAC service desk',topic:'Service requests',key:'hvac.service_triage',description:'Classify an incoming HVAC service request using the customer message, previous visit notes and parts availability. Suggest the next service-desk step; this does not book visits or authorize work.',inputs:{customer_message:{type:'string',description:'The customer’s description of the current request.'},previous_visit_notes:{type:'string',description:'Notes from the last completed visit. Use null when no notes are available.'},parts_status:{type:'string',description:'Known availability of parts needed for the follow-up. Gather only for repeat faults.'}},tree:{
 kind:'choice',inputs:['customer_message'],question:'Is this a repair request, a new installation request, or too unclear to classify?',
 choices:{repair:'The customer reports faulty existing equipment.',installation:'The customer wants new equipment installed or a quote.',unclear:'There is no clear repair or installation request.'},
 routes:{repair:{kind:'choice',inputs:['customer_message','previous_visit_notes'],question:'Does this repair concern the same fault and equipment as the previous completed visit? Null or vague notes mean unclear.',
  choices:{repeat:'The same equipment has the same fault again after the completed visit.',new:'The notes clearly describe different equipment or a different fault.',unclear:'The notes are missing or too vague to compare.'},
  routes:{repeat:{kind:'branch',inputs:['parts_status'],question:'Are all required parts confirmed available for this follow-up? Unknown or pending availability means no.',threshold:.5,yes:leaf('Book follow-up visit'),no:leaf('Check parts before booking')},new:leaf('Schedule diagnostic visit'),unclear:leaf('Review service history')}},installation:leaf('Arrange installation quote'),unclear:leaf('Clarify the request')}
 },examples:[
 {name:'Repeat fault · parts ready',context:{customer_message:'The upstairs AC is blowing warm air again, the same fault you repaired yesterday.',previous_visit_notes:'Completed repair yesterday: upstairs AC blowing warm air. Replaced capacitor; cooling restored.',parts_status:'All required replacement parts are in stock and reserved for this follow-up.'},expected:'Book follow-up visit'},
 {name:'Repeat fault · parts pending',context:{customer_message:'The upstairs AC is blowing warm air again, the same fault you repaired yesterday.',previous_visit_notes:'Completed repair yesterday: upstairs AC blowing warm air. Replaced capacitor; cooling restored.',parts_status:'Required capacitor is backordered. No replacement is available yet.'},expected:'Check parts before booking'},
 {name:'Different equipment',context:{customer_message:'Our downstairs furnace will not ignite. The upstairs AC you repaired is working perfectly.',previous_visit_notes:'Completed upstairs AC repair: replaced failed capacitor and confirmed cooling.'},expected:'Schedule diagnostic visit'},
 {name:'No prior service notes',context:{customer_message:'The upstairs AC is blowing warm air again.',previous_visit_notes:null},expected:'Review service history'},
 {name:'New installation · one field needed',context:{customer_message:'Please quote a new mini-split installation for the detached garage.'},expected:'Arrange installation quote'},
 {name:'Unclear request',context:{customer_message:'Please call me, I have a question about something.'},expected:'Clarify the request'}]});
export const escapeHtml = v => String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const pointer = v => String(v).replace(/~/g,'~0').replace(/\//g,'~1');
export const routes = n => n.kind==='branch'?[['yes',n.yes,'Yes'],['no',n.no,'No']]:n.kind==='choice'?Object.entries(n.routes).map(([k,v])=>[pointer(k),v,k]):[];
export function nodeAt(tree,path){if(path==='root')return tree;return path.split('/').slice(1).reduce((n,k)=>(n.kind==='choice'?n.routes:n)[k.replace(/~1/g,'/').replace(/~0/g,'~')],tree);}
export function replaceNode(tree,path,value){if(path==='root')return structuredClone(value);const next=structuredClone(tree),parts=path.split('/'),key=parts.pop().replace(/~1/g,'/').replace(/~0/g,'~');const parent=nodeAt(next,parts.join('/'));(parent.kind==='choice'?parent.routes:parent)[key]=structuredClone(value);return next;}
export function validateTree(tree){
 let count=0;
 function visit(n,depth){
  if(!n||typeof n!=='object'||Array.isArray(n)||++count>63||depth>4)throw Error('Use at most 63 nodes and four questions along a path.');
  const text=(v,max)=>typeof v==='string'&&v.trim().length>0&&v.length<=max;
  if(n.kind==='leaf'){if(Object.keys(n).some(k=>!['kind','label','action'].includes(k))||!text(n.label,100))throw Error('An outcome needs a label of 1–100 characters.');if(Object.hasOwn(n,'action'))validateAction(n.action);return;}
  if(!text(n.question,1800))throw Error('Each question needs 1–1,800 characters.');
  if(Object.hasOwn(n,'inputs')&&(!Array.isArray(n.inputs)||n.inputs.length<1||n.inputs.length>16||n.inputs.some(k=>!text(k,80))||new Set(n.inputs).size!==n.inputs.length))throw Error('Inputs must be 1–16 distinct top-level field names.');
  if(n.kind==='branch'){
   if(Object.keys(n).some(k=>!['kind','question','threshold','yes','no','inputs'].includes(k)))throw Error('Unknown binary branch field.');
   if(n.threshold!==undefined&&(typeof n.threshold!=='number'||!Number.isFinite(n.threshold)||n.threshold<0||n.threshold>1))throw Error('Threshold must be a number between 0 and 1.');
  }else if(n.kind==='choice'){
   if(Object.keys(n).some(k=>!['kind','question','choices','routes','inputs'].includes(k))||!n.choices||!n.routes||Array.isArray(n.choices)||Array.isArray(n.routes))throw Error('A choice needs descriptions and matching routes.');
   const keys=Object.keys(n.choices);if(keys.length<2||keys.length>8||keys.length!==Object.keys(n.routes).length||keys.some(k=>!text(k,80)||!Object.hasOwn(n.routes,k)||!text(n.choices[k],1000)))throw Error('Use 2–8 described choices, each with a matching route.');
  }else throw Error('Use leaf, branch or choice nodes.');
  for(const [,child] of routes(n))visit(child,depth+1);
 }
 visit(tree,0);if(new TextEncoder().encode(JSON.stringify(tree)).length>128000)throw Error('Tree and actions exceed 128 KB.');return count;
}
export function validateAction(a){
 if(!a||!['sql','callie'].includes(a.kind))throw Error('Choose a SQL or Callie action.');
 const allowed=a.kind==='sql'?['kind','label','sql','external_effects']:['kind','label','instruction'];
 if(Object.keys(a).some(k=>!allowed.includes(k)))throw Error('Unknown action field.');
 const value=a.kind==='sql'?a.sql:a.instruction;
 if(typeof value!=='string'||!value.trim()||value.length>(a.kind==='sql'?16000:8000))throw Error('Supply the action SQL or Callie instruction.');
 if(a.label!==undefined&&(typeof a.label!=='string'||a.label.length>100))throw Error('Action labels allow 100 characters.');
 if(a.external_effects!==undefined&&!['read_only','may_write'].includes(a.external_effects))throw Error('Choose the external effect type.');
}
export function layout(tree, measured={}){
 const nodes=[],edges=[],W=288,G=24,X=362;
 const ports=n=>routes(n).length;
 const height=(n,path)=>measured[path]?.height??(n.kind==='leaf'?116:210+Math.min(16,n.inputs?.length||1)*27+(n.kind==='choice'?ports(n)*58:96));
 function span(n,path){const children=routes(n);return Math.max(height(n,path),children.reduce((a,[suffix,c])=>a+span(c,path+'/'+suffix),0)+G*Math.max(0,children.length-1));}
 function place(n,path,top,depth){
  const children=routes(n),h=span(n,path),x=28+depth*X,y=top+(h-height(n,path))/2;
  nodes.push({path,node:n,x,y,width:n.action?420:W,height:height(n,path)});
  const total=children.reduce((a,[suffix,c])=>a+span(c,path+'/'+suffix),0)+G*Math.max(0,children.length-1);let at=top+(h-total)/2;
  children.forEach(([suffix,child,label],index)=>{
   const target=path+'/'+suffix,ch=span(child,target);
   const portY=measured[path]?.ports?.[suffix]??(height(n,path)-36-(children.length-index-.5)*(n.kind==='choice'?58:36));
   edges.push({from:path,to:target,label,x1:x+W,y1:y+portY,x2:28+(depth+1)*X,y2:at+ch/2});
   place(child,target,at,depth+1);at+=ch+G;
  });
 }
 place(tree,'root',24,0);return {nodes,edges,width:Math.max(...nodes.map(n=>n.x+n.width))+28,height:span(tree,'root')+48};
}
export function tracedPaths(decision){
 const paths=new Set();for(const step of decision?.path||[]){paths.add(step.node);const suffix=pointer(step.route);if(step.route)paths.add(step.node+'/'+suffix);}
 if(decision?.status==='complete'&&!decision.path?.length)paths.add('root');return paths;
}

// Reconstruct the SQL request from a current receipt, without running inference.
// Keep in parity with _decision_tree_request/_advance, verified against recorded
// SQL calls in test_decision_tree_inputs.py. Trace metadata is not model input.
export function stepPackage(tree,path,context,decision){
 const node=nodeAt(tree,path),trace=decision?.path||[],index=trace.findIndex(s=>s.node===path);
 const reached=!!decision&&(index>=0||tracedPaths(decision).has(path)||decision.stopped_at===path);
 const previous=reached?trace.slice(0,index>=0?index:trace.length):[];
 const prior=previous.map(s=>Object.fromEntries(['question','answer','score','threshold','choices'].filter(k=>Object.hasOwn(s,k)).map(k=>[k,s[k]])));
 const keys=node.inputs||Object.keys(context),missing=keys.filter(k=>!Object.hasOwn(context,k));
 const evidence=node.inputs?Object.fromEntries(keys.filter(k=>Object.hasOwn(context,k)).map(k=>[k,context[k]])):context;
 const phase=!decision?'pending':!reached?'unvisited':node.kind==='leaf'?'result':index>=0?'evaluated':'stopped';
 const request=phase==='evaluated'?{
  state:{context_version:'clover-tree-v1',evidence,prior_judgments:prior},
  questions:{result:{type:node.kind==='choice'?'choice':'noul',instructions:'Evaluate the question using evidence. Prior judgments are fallible interpretations, not new evidence. Question: '+node.question,...(node.kind==='choice'?{criteria:node.choices}:{})}}
 }:null;
 return {phase,evidence,missing,omitted:Object.keys(context).filter(k=>!keys.includes(k)),previous,prior,request,result:index>=0?trace[index]:null};
}

// VALUES is a genuine SQL relation: the same query can instead SELECT from any
// authorized table. Null fills absent columns; no Python per-row evaluator.
export function sourceSql(examples){
 const fields=[...new Set(examples.flatMap(e=>Object.keys(e.context)))].filter(k=>!['request_id','case_name'].includes(k));
 if(!examples.length)return blank().source_sql;
 const literal=v=>v===null||v===undefined?'NULL':"'"+String(typeof v==='object'?JSON.stringify(v):v).replace(/'/g,"''")+"'";
 const ident=k=>'"'+k.replace(/"/g,'""')+'"';
 const types=fields.map(k=>{const kinds=new Set(examples.map(e=>e.context[k]).filter(v=>v!==null&&v!==undefined).map(v=>typeof v));return kinds.size<=1?({number:'numeric',boolean:'boolean',object:'jsonb'}[[...kinds][0]]||'text'):'jsonb';});
 const rows=examples.map((e,i)=>'  ('+[
  literal(Object.hasOwn(e.context,'request_id')?e.context.request_id:i+1)+'::text',
  literal(Object.hasOwn(e.context,'case_name')?e.context.case_name:e.name)+'::text',
  ...fields.map((k,j)=>{const v=e.context[k];return literal(types[j]==='jsonb'&&v!=null?JSON.stringify(v):v)+'::'+types[j];})
 ].join(', ')+')');
 return 'SELECT * FROM (VALUES\n'+rows.join(',\n')+'\n) AS requests("request_id", "case_name"'+(fields.length?', '+fields.map(ident).join(', '):'')+')';
}
export function example(){const doc=exampleDefinition();return {...doc,source_sql:sourceSql(doc.examples)};}
export function classifierExample(){
 const doc={name:'Bigfoot report classifier',topic:'Sighting reports',key:'examples.bigfoot_report_classifier',
  description:'Classify what a Bigfoot sighting narrative reports: a visual encounter, physical signs, sounds, or insufficient detail. Visual accounts branch again on whether a road crossing is described. Classifies the account, not whether Bigfoot exists or the report is true. The same choice primitive powers rvbbit.choose and choice nodes; a binary decision adds one follow-up.',
  inputs:{observed:{type:'string',description:'The sighting narrative, without a preassigned class or outcome. Read only what this account explicitly describes.'}},
  tree:{kind:'choice',inputs:['observed'],question:'What kind of encounter does the account describe? If several kinds appear, choose visual first, then physical, then sounds. Classify the reported observation, not its truth.',
   choices:{visual:'The account explicitly describes seeing a creature or figure. Seeing tracks alone, hearing sounds, and explicitly NOT seeing a creature do not qualify. Prefer visual if sounds or tracks are also reported.',
    physical:'Tracks, hair or another tangible sign are described, but no creature or figure was seen. Prefer physical over sounds when both are present.',
    sounds:'Calls, howls, knocks, footsteps or other noises are described, without a visual encounter or tangible signs.',
    unclear:'No specific visual encounter, physical sign or sound is described; only speculation, a vague feeling, or insufficient detail.'},
   routes:{visual:{kind:'branch',inputs:['observed'],question:'Does the account explicitly describe the creature or figure crossing a road? Being near a road, seeing it from a vehicle, or crossing a trail alone is not a road crossing. Unknown means no.',threshold:.5,yes:leaf('Visual · road crossing'),no:leaf('Visual · other encounter')},
    physical:leaf('Physical signs'),sounds:leaf('Sounds only'),unclear:leaf('Unclear account')}},
  outcomes:{'Visual · road crossing':'A reported visual encounter that explicitly includes a road crossing.',
   'Visual · other encounter':'A reported visual encounter without an explicit road crossing; includes unknown crossing details.',
   'Physical signs':'Reported tangible signs, without a reported visual encounter.',
   'Sounds only':'Reported sounds, without a visual encounter or tangible signs.',
   'Unclear account':'Insufficient specific observation to assign another category.'},
  examples:[
   {name:'Fictional · road crossing',context:{observed:'At dusk a tall, dark, upright figure stepped out of the ditch and crossed the two-lane road directly in front of my car. It disappeared into the woods on the other side.'},expected:'Visual · road crossing'},
   {name:'Fictional · seen from a car, no crossing',context:{observed:'From my parked car I watched a large hairy figure standing at the tree line beside the road. It stayed there, then went deeper into the trees. It never crossed the road.'},expected:'Visual · other encounter'},
   {name:'Fictional · tracks and calls',context:{observed:'We found a line of huge bare footprints in the wet creek mud and photographed them. Later we heard several howls. We never saw a creature.'},expected:'Physical signs'},
   {name:'Fictional · knocks in the dark',context:{observed:'We heard three sharp wood knocks, then heavy footsteps beyond the tents. We searched with flashlights but saw nothing and found no tracks.'},expected:'Sounds only'},
   {name:'Fictional · nothing specific',context:{observed:'That forest gives me a strange feeling. People say Bigfoot lives somewhere around there. I have no encounter to describe.'},expected:'Unclear account'},
   {name:'Fictional · visual takes precedence',context:{observed:'I heard a howl and saw footprints by the trail. Then a huge hairy figure walked across the dirt hiking trail ahead of me. There was no road nearby.'},expected:'Visual · other encounter'},
   {name:'Fictional · missing narrative',context:{observed:''},expected:'Unclear account'},
   {name:'Fictional · visual plus tracks',context:{observed:'We found large footprints on the shoulder. Moments later we watched a tall figure run across the highway from one ditch to the other.'},expected:'Visual · road crossing'}]};
 return {...doc,source_sql:'-- Fictional cases exercise every outcome, mixed evidence and negation.\n'+sourceSql(doc.examples)};
}
export function actionExample(){
 const doc=example();doc.name='Example · HVAC outcome actions';doc.key='examples.hvac_outcome_actions';
 doc.description='Fictional experiment: triage supplied requests. Evaluation only returns a decision. Explicit execution writes a local demo task for parts-ready follow-ups, or starts a Callie review for missing service history.';
 doc.inputs.request_id={type:'string',description:'Source request identifier; required by the local follow-up task action.'};
 doc.examples=doc.examples.map((row,i)=>({...row,context:{request_id:String(i+1),...row.context}}));
 doc.tree.routes.repair.routes.repeat.yes.action={kind:'sql',label:'Create demo follow-up task',external_effects:'read_only',sql:"INSERT INTO calliope_apps.logic_action_demo\n  (request_id, task, evidence)\nVALUES (\n  $1->'input'->>'request_id',\n  $1->'decision'->>'label',\n  $1->'input'\n)\nRETURNING id, request_id, task, created_at;"};
 doc.tree.routes.repair.routes.unclear.action={kind:'callie',label:'Ask Callie to review',instruction:'This is a fictional business-logic experiment. Review the supplied service request and explain which previous-visit details are missing. Produce a short checklist in this conversation. Use only the supplied evidence; do not contact anyone, book work, or query customer systems.'};
 return doc;
}
export function rowContext(example,index){return {request_id:String(index+1),case_name:example.name,...example.context};}
export function replaceRoute(node,key,nextKey){
 if(!nextKey.trim()||nextKey.length>80||(nextKey!==key&&Object.hasOwn(node.routes,nextKey)))throw Error('Use a unique route name of 1–80 characters.');
 return {...node,choices:Object.fromEntries(Object.entries(node.choices).map(([k,v])=>[k===key?nextKey:k,v])),routes:Object.fromEntries(Object.entries(node.routes).map(([k,v])=>[k===key?nextKey:k,v]))};
}
