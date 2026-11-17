import {test} from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {MessageProcessor, NodeResolver, getValue, isComponentNode} from '@a2ui/web_core/v0_9';
import {workspaceCatalog, customComponents} from './catalog.js';

test('the actual SQL-backed definitions load in the official A2UI processor', () => {
  const script = `import sys,json,tempfile,pathlib
sys.path.insert(0,'..')
from server import Store
with tempfile.TemporaryDirectory() as d:
 s=Store(pathlib.Path(d)/'test.sqlite')
 snapshots=[s.snapshot(i,view=v) for i in ['cedar','harbor','oak','willow','station'] for v in ['dossier','pipeline','briefing','hvac','counter','receiving','dispatch','field']]
 ordered=s.action({'view':'counter','name':'counter_order','selected':'Q-1043','context':{'version':1,'allow_backorder':True},'requestId':'contract-order'})
 linked=s.action({'view':'counter','name':'counter_visit','selected':'Q-1043','context':{'draft':ordered['draft']},'requestId':'contract-visit'})
 snapshots.extend([ordered,linked,s.snapshot(linked['messages'][2]['updateDataModel']['value']['visit']['id'],view='hvac')])
 print(json.dumps(snapshots))`;
  const snapshots = JSON.parse(execFileSync('python3',['-c',script],{encoding:'utf8'}));
  for (const snapshot of snapshots) {
    const p = new MessageProcessor([workspaceCatalog]);
    p.processMessages(snapshot.messages);
    const surface = p.getSurfaces().get('transaction-desk');
    assert.ok(surface);
    assert.equal(surface.dataModel.get('/draft/id'),snapshot.selected);
    assert.equal(surface.dataModel.get('/draft/notes'),snapshot.draft.notes);
    const resolver=new NodeResolver(surface,workspaceCatalog), seen=new Set();
    const walk=node=>{
      assert.ok(isComponentNode(node),'Child references must resolve to nodes');
      assert.equal(node.state,'resolved');
      seen.add(node.componentId);
      const props=getValue(node.props);
      if(node.componentId.startsWith("chart-") && props.spec) assert.ok(Array.isArray(props.spec.value.data.values),"Chart spec must resolve from the data model");
      for(const child of [...(props.children||[]),...(props.child?[props.child]:[])]) walk(child);
    };
    walk(getValue(resolver.rootNode));
    assert.equal(seen.size,snapshot.messages[1].updateComponents.components.length);
    resolver.dispose();
    p.dispose();
  }
});

test('presentation tokens reject free-form styles, code and arbitrary artwork',()=>{
  const schemas=Object.fromEntries(customComponents.map(c=>[c.name,c.schema]));
  assert.equal(schemas.CSurface.safeParse({child:'x',style:{position:'fixed'}}).success,false);
  assert.equal(schemas.CText.safeParse({text:'x',size:'72px'}).success,false);
  assert.equal(schemas.CText.safeParse({text:{call:'eval',args:{}}}).success,false);
  assert.equal(schemas.CArtwork.safeParse({asset:'../../secret'}).success,false);
  assert.equal(schemas.CDataGrid.safeParse({columns:[],children:[]}).success,false);
  assert.equal(schemas.CRecordPicker.safeParse({label:'Part',value:{path:'/controls/product'},options:[{label:'Capacitor',value:'CAP-45-5'}]}).success,true);
  assert.equal(schemas.CMeter.safeParse({label:'x',value:2}).success,false);
});


test('charts reject external resources and excessive datasets',async()=>{
  const {validateChart}=await import('./vega-chart.js');
  const basic={data:{values:[{x:'Repair',y:2}]},mark:'bar'};
  assert.equal(validateChart(basic),basic);
  for(const spec of [
    {...basic,data:{url:'file:///etc/passwd',values:[]}},
    {...basic,mark:'image'},
    {...basic,data:{values:Array(5001).fill({})}},
  ]) assert.throws(()=>validateChart(spec));
});

test('the installed processor reconciles a receipt and inspector without replacing unchanged inputs',async()=>{
 const {applyIncremental,clone,model,reconcile}=await import('./continuity.js');
 const script=`import sys,json,tempfile,pathlib
sys.path.insert(0,'..')
from server import Store
with tempfile.TemporaryDirectory() as d:
 s=Store(pathlib.Path(d)/'test.sqlite')
 a=s.action({'view':'receiving','selected':'PO-2001','name':'refresh'})
 b=s.action({'view':'receiving','selected':'PO-2001','name':'open_record','context':{'kind':'customer','recordId':'cedar'}})
 c=s.action({'view':'receiving','selected':'PO-2001','name':'close_record','panes':b['panes']})
 draft=c['draft'];draft['reference']='test';draft['lines']['PO-2001-0']['quantity']='1'
 d=s.action({'view':'receiving','selected':'PO-2001','name':'receiving_post','context':{'draft':draft},'requestId':'contract-receipt'})
 print(json.dumps([a,b,c,d]))`;
 const snapshots=JSON.parse(execFileSync('python3',['-c',script],{encoding:'utf8'}));const p=new MessageProcessor([workspaceCatalog]);p.processMessages(snapshots[0].messages);const surface=p.getSurfaces().get('transaction-desk');const field=surface.componentsModel.get('receipt-notes');surface.dataModel.set('/draft/notes','keep my draft');let previous=snapshots[0];
 for(const snapshot of snapshots.slice(1)){const next=clone(snapshot);model(next).draft=reconcile(previous.draft,surface.dataModel.get('/draft'),snapshot.draft).local;applyIncremental(p,surface,previous,next);assert.equal(surface.componentsModel.get('receipt-notes'),field);assert.equal(surface.dataModel.get('/draft/notes'),'keep my draft');previous=snapshot;}
 assert.equal(surface.componentsModel.has('record-pane'),false);assert.equal(surface.dataModel.get('/receiving_lines/0/received'),100);p.dispose();
});
