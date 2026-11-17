"""A SQL-fed Vega-Lite rendering matrix, also used by web/native regression tests."""
import copy
from ui import UI,bind

def gallery_specs():
    visits={'name':'visits'}
    x={'field':'day','type':'temporal','title':'Service day'}
    y={'aggregate':'sum','field':'amount','type':'quantitative','title':'Visit value'}
    color={'field':'job_type','type':'nominal'}
    specs={
      'layered':{'data':visits,'layer':[{'mark':{'type':'line','point':True},'encoding':{'x':x,'y':y}},{'mark':{'type':'rule','color':'#ffcc66','strokeDash':[4,4]},'encoding':{'y':{'aggregate':'mean','field':'amount','type':'quantitative'}}}]},
      'stacked':{'data':visits,'mark':'bar','encoding':{'x':{'field':'technician','type':'nominal'},'y':y,'color':color,'tooltip':[{'field':'job_type'},{'aggregate':'sum','field':'amount','format':'$,.0f'}]}},
      'area':{'data':visits,'mark':{'type':'area','interpolate':'monotone'},'encoding':{'x':x,'y':y,'color':color}},
      'donut':{'data':visits,'mark':{'type':'arc','innerRadius':65},'encoding':{'theta':{'aggregate':'count','type':'quantitative'},'color':color,'tooltip':[{'field':'job_type'},{'aggregate':'count','title':'Visits'}]}},
      'histogram':{'data':visits,'mark':'bar','encoding':{'x':{'bin':True,'field':'amount','type':'quantitative'},'y':{'aggregate':'count','type':'quantitative'}}},
      'boxplot':{'data':visits,'mark':{'type':'boxplot','extent':'min-max'},'encoding':{'x':{'field':'job_type','type':'nominal'},'y':{'field':'amount','type':'quantitative'},'color':color}},
      'errorband':{'data':visits,'mark':{'type':'errorband','extent':'ci'},'encoding':{'x':x,'y':{'field':'amount','type':'quantitative'}}},
      'heatmap':{'data':visits,'encoding':{'x':{'field':'job_type','type':'nominal'},'y':{'field':'technician','type':'nominal'}},'layer':[{'mark':'rect','encoding':{'color':{'aggregate':'count','type':'quantitative'},'tooltip':[{'field':'job_type'},{'field':'technician'},{'aggregate':'count'}]}},{'mark':{'type':'text','color':'white'},'encoding':{'text':{'aggregate':'count','type':'quantitative'}}}]},
      'facet':{'data':visits,'facet':{'column':{'field':'job_type','type':'nominal'}},'spec':{'mark':'bar','encoding':{'x':{'field':'technician','type':'nominal'},'y':y}}},
      'repeat':{'data':visits,'repeat':['amount','day_index'],'columns':2,'spec':{'mark':'point','encoding':{'x':{'field':{'repeat':'repeat'},'type':'quantitative'},'y':{'field':'amount','type':'quantitative'},'color':color}}},
      'concat':{'hconcat':[{'data':visits,'mark':'bar','encoding':{'x':{'field':'status','type':'nominal'},'y':{'aggregate':'count','type':'quantitative'}}},{'data':{'name':'parts'},'mark':'point','encoding':{'x':{'field':'available','type':'quantitative'},'y':{'field':'name','type':'nominal'},'size':{'field':'price','type':'quantitative'}}}]},
      'vertical':{'data':visits,'vconcat':[{'mark':'line','encoding':{'x':x,'y':y}},{'mark':'tick','encoding':{'x':x,'y':{'field':'job_type','type':'nominal'},'color':color}}]},
      'window':{'data':visits,'transform':[{'aggregate':[{'op':'sum','field':'amount','as':'daily'}],'groupby':['day']},{'sort':[{'field':'day'}],'window':[{'op':'sum','field':'daily','as':'running'}],'frame':[None,0]},{'calculate':'datum.running / 1000','as':'thousands'}],'mark':'line','encoding':{'x':x,'y':{'field':'thousands','type':'quantitative','title':'Cumulative value ($k)'}}},
      'regression':{'data':visits,'layer':[{'mark':{'type':'point','opacity':.5},'encoding':{'x':{'field':'day_index','type':'quantitative'},'y':{'field':'amount','type':'quantitative'}}},{'transform':[{'regression':'amount','on':'day_index'}],'mark':{'type':'line','color':'#ed9b61'},'encoding':{'x':{'field':'day_index','type':'quantitative'},'y':{'field':'amount','type':'quantitative'}}}]},
      'density':{'data':visits,'transform':[{'density':'amount','groupby':['job_type'],'as':['amount','density']}],'mark':{'type':'area','opacity':.4},'encoding':{'x':{'field':'amount','type':'quantitative'},'y':{'field':'density','type':'quantitative'},'color':color}},
      'geoshape':{'data':{'name':'regions'},'projection':{'type':'mercator'},'mark':{'type':'geoshape','stroke':'white'},'encoding':{'color':{'field':'properties.jobs','type':'quantitative'},'tooltip':[{'field':'properties.name'},{'field':'properties.jobs'}]}},
      'trail':{'data':visits,'mark':'trail','encoding':{'x':x,'y':{'field':'amount','type':'quantitative'},'size':{'field':'amount','type':'quantitative'},'color':color}},
      'errorbar':{'data':visits,'mark':{'type':'errorbar','extent':'stdev'},'encoding':{'x':{'field':'job_type','type':'nominal'},'y':{'field':'amount','type':'quantitative'}}},
      'binned2d':{'data':visits,'mark':'rect','encoding':{'x':{'bin':True,'field':'day_index','type':'quantitative'},'y':{'bin':True,'field':'amount','type':'quantitative'},'color':{'aggregate':'count','type':'quantitative'}}},
      'parameter':{'data':visits,'params':[{'name':'minimum','value':300}],'transform':[{'filter':'datum.amount >= minimum'},{'calculate':'datum.amount * 1.065','as':'total'}],'mark':'point','encoding':{'x':x,'y':{'field':'total','type':'quantitative'},'color':color}},
      'lookup':{'data':{'name':'parts'},'transform':[{'lookup':'id','from':{'data':{'name':'parts'},'key':'id','fields':['available']},'as':['stock']}],'mark':'bar','encoding':{'x':{'field':'name','type':'nominal','axis':{'labelAngle':-25}},'y':{'field':'stock','type':'quantitative'}}},
    }
    for spec in specs.values():
        spec.update({'$schema':'https://vega.github.io/schema/vega-lite/v6.json','datasets':{'visits':[],'parts':[],'regions':[],'deals':[]},'config':{'font':'LT Superior','background':'transparent','view':{'stroke':None},'header':{'labelColor':'#aeb9c9','titleColor':'#aeb9c9'},'axis':{'labelColor':'#aeb9c9','titleColor':'#aeb9c9','gridColor':'#313846'},'legend':{'labelColor':'#aeb9c9','titleColor':'#aeb9c9'},'range':{'category':['#82b7ca','#e6bc68','#ba9cda','#8fb8a8']}}})
    return specs

def add_charts(doc,query,T):
    specs=gallery_specs();templates=copy.deepcopy(specs)
    templates['status']={'data':{'name':'visits'},'mark':{'type':'bar','cornerRadiusEnd':4},'encoding':{'x':{'field':'status','type':'nominal'},'y':{'aggregate':'count','type':'quantitative'},'tooltip':[{'field':'status'},{'aggregate':'count'}]}}
    templates['revenue']={'data':{'name':'visits'},'mark':{'type':'line','point':True},'encoding':{'x':{'field':'day','timeUnit':'yearmonthdate','type':'temporal'},'y':{'aggregate':'sum','field':'amount','type':'quantitative'}}}
    templates['mix']=copy.deepcopy(specs['heatmap'])
    templates['pipeline']={'data':{'name':'deals'},'mark':'bar','encoding':{'x':{'field':'stage','type':'nominal'},'y':{'aggregate':'sum','field':'price','type':'quantitative'},'color':{'field':'stage','type':'nominal'}}}
    for spec in templates.values():
        spec.setdefault('datasets',{'visits':[],'parts':[],'regions':[],'deals':[]})
        spec.setdefault('config',copy.deepcopy(specs['layered']['config']))
    doc['data']['chartTemplates']=templates;doc['data']['charts']=copy.deepcopy(templates)
    query('chart_data',f"""WITH visits AS (
      SELECT id,customer,technician,job_type,status,scheduled_date AS day,amount_cents/100.0 amount,(scheduled_date::date-current_date) AS day_index
      FROM {T('hvac_jobs')} WHERE scheduled_date::date>=current_date-$2::integer AND ($3='Everyone' OR technician=$3)),
      parts AS (SELECT p.id,p.name,p.price_cents/100.0 price,sum(s.on_hand-s.reserved)/100.0 available FROM {T('counter_products')} p JOIN {T('counter_stock')} s ON s.product_id=p.id GROUP BY p.id),
      datasets AS (SELECT jsonb_build_object('visits',coalesce((SELECT jsonb_agg(to_jsonb(v)) FROM visits v),'[]'::jsonb),
       'parts',coalesce((SELECT jsonb_agg(to_jsonb(p)) FROM parts p),'[]'::jsonb),'deals',coalesce((SELECT jsonb_agg(to_jsonb(d)) FROM {T('deals')} d),'[]'::jsonb),
       'regions',(SELECT jsonb_agg(jsonb_build_object('type','Feature','properties',jsonb_build_object('name',technician,'jobs',n),'geometry',jsonb_build_object('type','Polygon','coordinates',jsonb_build_array(jsonb_build_array(jsonb_build_array(-75+i,40),jsonb_build_array(-75+i,40.8),jsonb_build_array(-74.2+i,40.8),jsonb_build_array(-74.2+i,40),jsonb_build_array(-75+i,40)))))) FROM (SELECT technician,count(*) n,row_number() OVER(ORDER BY technician)::integer i FROM visits GROUP BY technician) regions)) AS data)
      SELECT jsonb_object_agg(key,jsonb_set(value,'{{datasets}}',datasets.data)) AS charts FROM jsonb_each($1::jsonb),datasets""",['/chartTemplates','/state/filter/days/0','/state/filter/technician/0'],'value',path='/charts')
    for name in specs:
        u=UI();root=u.stack('body',[u.text('heading',name.title(),'title',family='display'),u.add('chart','CVegaChart',spec=bind('/charts/'+name),height=440,description=name+' chart from shared SQL datasets'),u.text('scope','Real Vega-Lite 6.1 rendering. Native hover/click support is limited to supported marks; selections, brushing and parameter widgets are not native interactions.','caption','muted')])
        doc['fragments']['gallery-'+name]={'root':root,'components':u.nodes}
    u=UI();root=u.stack('body',[u.text('eyebrow','CALLIOPE / CHART RENDERING GALLERY','eyebrow','muted'),u.text('heading','More than a bar chart.','display',family='display'),u.text('copy','Layers, transforms and composed views over the same live SQL datasets.','body','muted'),u.filters('filters','SQL dataset','/state/filter','filter',[u.picker('days','Period','/state/filter/days',[(v,v+' days') for v in ['7','28','56','365']]),u.picker('tech','Technician','/state/filter/technician',[(v,v) for v in ['Everyone','Alex Morgan','Sam Rivera','Jordan Lee']])]),u.picker('kind','Chart family','/state/controls/chart',[(v,v.title()) for v in specs]),u.stack('gallery-slot',[])],gap='lg')
    doc['fragments']['charts']={'root':root,'components':u.nodes}
    doc['switch']['gallery-slot']={'path':'/state/controls/chart','cases':{v:'gallery-'+v for v in specs},'default':'gallery-layered'}
