"""Real compiler/export checks; install-vega.py supplies the pinned offline engine."""
import json
from pathlib import Path
import tempfile
import unittest

AVAILABLE=(Path(__file__).resolve().parents[1]/'local/vega/python/vl_convert').is_dir()

@unittest.skipUnless(AVAILABLE,'requires business/install-vega.py')
class ChartScene(unittest.TestCase):
    def test_hvac_marks_keep_formatted_tooltips_and_data_at_both_sizes(self):
        from render_chart import render
        from server import Store
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            data=Store(root/'fixture.sqlite').snapshot(view='hvac')['messages'][2]['updateDataModel']['value']
            for width in (280,600):
                for name,spec in data['charts'].items():
                    spec['width']=width
                    spec['config']['font']='sans-serif'
                    output=root/'chart.png'
                    render(spec,output)
                    png=output.read_bytes()
                    self.assertEqual(int.from_bytes(png[16:20],'big'),width*2)
                    scene=json.loads(Path(str(output)+'.json').read_text())
                    self.assertEqual(scene['width'],width)
                    self.assertEqual(len(scene['hits']),len(data['chart_rows'][name]))
                    for hit in scene['hits']:
                        self.assertTrue(hit['tooltip'])
                        self.assertLess(hit['bounds'][0],hit['bounds'][2])
                        self.assertLess(hit['bounds'][1],hit['bounds'][3])
                        if name=='revenue':
                            self.assertIn('Value: $',hit['tooltip'])
                            self.assertIn(hit['datum']['week_label'],hit['tooltip'])
                        if name=='mix': self.assertIn(hit['datum']['technician'],hit['tooltip'])

    def test_faceted_negative_bars_and_quoted_sql_alias(self):
        from render_chart import instrument,interactions,vega
        spec={'data':{'values':[{'group':'one','strange alias':'A','n':-4},{'group':'two','strange alias':'B','n':7}]},'facet':{'column':{'field':'group'}},'spec':{'width':150,'height':160,'mark':'bar','encoding':{'x':{'field':'strange alias','type':'nominal'},'y':{'field':'n','type':'quantitative'},'tooltip':[{'field':'strange alias','title':'Name "quoted"'},{'field':'n','title':'Amount','format':'.2f'}]}}}
        compiled=vega.vegalite_to_vega(spec,vl_version='6.1')
        instrument(compiled,spec)
        scene=interactions(vega.vega_to_scenegraph(compiled,allowed_base_urls=[]))
        self.assertEqual(len(scene['hits']),2)
        a,b=scene['hits']
        self.assertLess(a['bounds'][2],b['bounds'][0])
        self.assertEqual(a['datum']['strange alias'],'A')
        self.assertIn('Amount: −4.00',a['tooltip'])

    def test_data_tooltip_and_clipped_marks(self):
        from render_chart import instrument,interactions,vega
        spec={'data':{'values':[{'x':1,'value':5,'active':True},{'x':2,'value':20,'active':False}]},'width':200,'height':100,'mark':{'type':'point','filled':True,'clip':True,'tooltip':{'content':'data'}},'encoding':{'x':{'field':'x','type':'quantitative'},'y':{'field':'value','type':'quantitative','scale':{'domain':[0,10]}}}}
        compiled=vega.vegalite_to_vega(spec,vl_version='6.1')
        instrument(compiled,spec)
        scene=interactions(vega.vega_to_scenegraph(compiled,allowed_base_urls=[]))
        self.assertEqual(len(scene['hits']),1)
        self.assertIn('active: true',scene['hits'][0]['tooltip'])
