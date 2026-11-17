"""HTML authoring defaults and compatibility assets for the retired Dashboard mode."""
from html.parser import HTMLParser
from pathlib import Path
import re

VERSION = '1.0.1'
ASSET = f'/charts/rvbbit-dashboard-{VERSION}.js'
SUPPORTED_ASSETS = {ASSET: VERSION, '/charts/rvbbit-dashboard-1.0.0.js': '1.0.0'}
WIDGET_API = {
    'common': 'All helpers take el (selector or element), stable id, optional state/error/note, and return update(patch)/destroy(). Data is already fetched; widgets never run SQL. Keep the template refresh controller and use mount(name, rvbbitDashboard.helper, options).',
    'kpi': 'kpi({el,id,label,value,note?,format?}): ONE value per mount. For four KPIs create four elements and mount four times. No items option. value is number/string/null; null means unavailable. percent format expects a fraction (0.074 becomes 7.4%).',
    'table': 'table({el,id,rows,columns,sort?,page?,filter?,metadata?,viewportHeight?}): rows are objects; columns are [{key,label,format?}]. sort is [{id:columnKey,desc:false}] or false. page is a page size or {size}. filter searches loaded rows. Pass truncated/applied_limit in metadata. Omit viewportHeight for natural row height; set it only for deliberate virtual scrolling.',
    'chart': 'chart({el,id,data,type,x,y,series?,color?,query?,height?,key?}): type is bar/line/area/scatter; x/y/series/color/key are FIELD NAMES, never arrays or configs. data is long-form row objects. y is numeric or null. series identifies groups; multi-series bars are grouped side by side, lines/areas are separate series. query names the Lens binding. Optional height controls only the chart viewport (default 280), not card/page height.',
    'filter': 'filter({el,id,field,value,choices,onChange}): choices are strings or {value,label}; onChange(value,field) is an explicit callback that updates a bounded query predicate and calls refresh().',
    'formats': 'number, currency (USD), percent (fraction), or a function returning plain text.',
    'example': "mount('leads',rvbbitDashboard.kpi,{el:'#leads',id:'leads',label:'Leads',value:120}); mount('enrolled',rvbbitDashboard.kpi,{el:'#enrolled',id:'enrolled',label:'Enrolled',value:12}); mount('mix',rvbbitDashboard.chart,{el:'#mix',id:'mix',type:'bar',data:[{week:'Sep 6',channel:'Paid',count:80},{week:'Sep 6',channel:'Organic',count:40}],x:'week',y:'count',series:'channel',query:'weekly'});",
    'verification': 'Use the exact tested queries and result shapes from the analytical handoff. Test any changed query before embedding it. After the LAST publication, call capture_live_app(slug=...,return_image=true), OMIT path, and inspect verification, bridge, dashboard, and the image. A runnable publication is not proof of populated or correct charts. Repair and recapture the newest version. Do not claim a cutover is marked unless a visible marker or segmentation exists; keep fixed-report versus current-date windows and labels consistent.',
}
DDL = """
ALTER TABLE rvbbit.calliope_sessions ADD COLUMN IF NOT EXISTS authoring_mode text NOT NULL DEFAULT 'html';
UPDATE rvbbit.calliope_sessions SET authoring_mode='html' WHERE authoring_mode='dashboard';
ALTER TABLE rvbbit.calliope_turns ADD COLUMN IF NOT EXISTS authoring_mode text;
CREATE OR REPLACE FUNCTION rvbbit.calliope_snapshot_authoring_mode() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 -- Dashboard authoring is paused; historical turns keep their snapshots.
 NEW.authoring_mode := 'html';
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS calliope_snapshot_authoring_mode ON rvbbit.calliope_turns;
CREATE TRIGGER calliope_snapshot_authoring_mode BEFORE INSERT ON rvbbit.calliope_turns
 FOR EACH ROW EXECUTE FUNCTION rvbbit.calliope_snapshot_authoring_mode();
"""


def instruction(mode):
    return ('HOSTED BUILD MODE: HTML. Preserve the dashboard_template data bridge; author ordinary HTML/CSS/JS '
            'with free choice of presentation and widgets. Dashboard mode is disabled. '
            'Existing published pages may retain their widget libraries when revised. ')


def explicit_html(message):
    # Deliberately narrow; mentioning HTML in quoted code or saying "weirder" is not a switch.
    return bool(re.search(r'(?im)^\s*(?:please\s+)?(?:switch|use|enable)\s+(?:to\s+)?(?:raw\s+html|html\s+mode)\b', message or ''))


class Source(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.scripts=[]; self.code=[]; self.in_script=False
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.in_script=True
            src=dict(attrs).get('src')
            if src: self.scripts.append(src.split('?')[0])
    def handle_endtag(self, tag):
        if tag=='script': self.in_script=False
    def handle_data(self, data):
        if self.in_script: self.code.append(data)


def publication(html, manifest, active, previous=None):
    # Retire the authoring constraint, not the runtime of already-published pages.
    result = dict(manifest or {})
    result['authoring_mode'] = 'html'
    source = Source(html or '')
    assets = [asset for asset in source.scripts if asset in SUPPORTED_ASSETS]
    if assets:
        result['dashboard_runtime'] = SUPPORTED_ASSETS[assets[0]]
    else:
        result.pop('dashboard_runtime', None)
    return result


def template():
    return (Path(__file__).parent/'dashboard_hosted_template.html').read_text()
