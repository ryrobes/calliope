import importlib.util
import json
from pathlib import Path

import system_updates as updates
from test_system_console import zen_page, mount_system, db, insert, OWNER


def test_inventory_distinguishes_targets_local_builds_and_missing_observations(tmp_path, monkeypatch):
    path=tmp_path/'inventory.json';monkeypatch.setenv('CALLIOPE_DEPLOYMENT_INVENTORY_PATH',str(path))
    missing=updates.inventory()
    assert missing['warning'] and all(not r['installed'] for r in missing['components'])
    path.write_text(json.dumps({'captured_at':'2020-01-01T00:00:00Z','containers':[
        {'container':'calliope-server','image':'calliope-server:local-test','version':'1.1.4','image_id':'sha256:abc','Env':['SECRET=hidden']},
        {'container':'calliope-clover','image':'calliope-clover:selfhosted','state':'running'},
        {'container':'calliope-postgres','image':'my-local/postgres:development','state':'running'}]}))
    value=updates.inventory()
    assert value['stale'] and not value['registry_checked'] and not value['upgrades_enabled']
    server=next(r for r in value['components'] if r['name']=='calliope-server')
    assert server['installed'][0]['image']=='calliope-server:local-test'
    assert 'local-test' not in server['target']
    clover = next(r for r in value['components'] if r['name']=='calliope-clover')
    assert clover['target'] == 'calliope-clover:' + value['release_version']
    assert clover['kind'] == 'release'  # Now shipped with the product, not a local extra.
    postgres=[r for r in value['components'] if r['name']=='calliope-postgres']
    assert len(postgres)==1 and postgres[0]['kind']=='release'
    assert postgres[0]['installed'][0]['image']=='my-local/postgres:development'
    assert 'SECRET' not in json.dumps(value)
    path.write_text('broken');assert updates.inventory()['warning']


def test_capture_only_exports_image_metadata():
    path=Path(__file__).parents[2]/'scripts/release/capture-deployment.py'
    spec=importlib.util.spec_from_file_location('capture',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    rows=module.project([{'Name':'/calliope-server','Image':'sha256:123','Config':{'Image':'server:v1','Env':['KEY=secret'],'Labels':{'org.opencontainers.image.version':'1.1.4','private':'secret'}},'State':{'Status':'running'}}, {'Name':'/unrelated','Config':{'Image':'other:v1'}}], 'Test host')
    assert len(rows)==1 and rows[0]['version']=='1.1.4'
    assert 'secret' not in json.dumps(rows)


def test_power_labels_follow_route_before_optional_worker():
    def power(**kw):return updates.capability_power(kw)['label']
    assert power(route={'source':'hosted-provider'},option={'runtime':'optional-gpu'})=='API'
    assert power(id='web_research',route={'source':'hosted-provider'})=='CPU + API'
    assert power(option={'runtime':'optional-gpu'})=='GPU'
    assert power(package={'gpu_required':False})=='CPU'
    assert power(model_version='openrouter/openai/gpt-6-luna')=='API'
    assert power(model_version='fish-s2.1-pro-v1')=='API'
    assert power(model_version='clover-decisions-v1/typesafe/jev-1.13')=='API'
    assert power(option={'runtime':'optional-cpu'},model_version='clover-cpu-analytics-v1/openrouter/voyageai/voyage-4')=='CPU + API'
    assert power()=='Worker'


def test_updates_are_part_of_server_owned_conversation_context(db):
    import system_console as system
    sid=str(insert(db)['id'])
    value={'id':'aa7d55d0-770f-40c9-9467-c79a8fef243a','session_id':sid,'observed_at':'2026-10-11T18:00:00Z','window':'24h','updates':{'release_version':'1.1.4','upgrades_enabled':False},'capabilities':[],'usage':{},'services':[],'warnings':[]}
    system.save_snapshot(db,OWNER,value)
    result=system.hydrate(db,OWNER,{'tab':'updates','snapshot_id':value['id'],'updates':{'release_version':'FAKE'}},sid)
    assert result['updates']['release_version']=='1.1.4'


def test_updates_and_power_chips_render_responsively(zen_page):
    page=zen_page;mount_system(page)
    # Replace the HTTP observation, keeping the real stage renderer and CSS.
    data={'id':'inventory','observed_at':'2026-10-11T18:00:00Z','window':'24h','services':[],
          'capabilities':[{'id':'extract','kind':'specialist','state':'ready','model_version':'GLiNER','power':{'label':'CPU','detail':'CPU worker'},'option':{}}],
          'packages':[],'warnings':[],'usage':{},'updates':{'release_version':'1.1.4','engine_version':'4.2.10','captured_at':'2026-10-11T18:00:00Z','components':[
          {'name':'calliope-server','kind':'release','target':'calliope-server:1.1.4','installed':[{'container':'calliope-server','image':'calliope-server:checks-polish','state':'running','image_id':'sha256:'+'a'*64}]},
          {'name':'calliope-clover','kind':'additional','target':None,'installed':[]}]}}
    page.route('**/api/calliope/system/snapshot?*',lambda r:r.fulfill(json=data))
    page.get_by_role('button',name='Refresh system',exact=True).click()
    page.get_by_role('button',name='Capabilities',exact=True).click()
    page.locator('.system-cap-row .system-power').get_by_text('CPU',exact=True).wait_for()
    page.get_by_role('button',name='Updates',exact=True).click()
    assert page.get_by_role('button',name='Upgrade all · coming later').is_disabled()
    assert 'calliope-server:checks-polish' in page.locator('.system-updates').inner_text()
    assert 'Outside release manifest' in page.locator('.system-updates').inner_text()
    assert page.evaluate('CalliopeSystemStage.origin().tab')=='updates'
    for width in (1500,1000,390):
        page.set_viewport_size({'width':width,'height':900})
        assert page.locator('.system-body').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
