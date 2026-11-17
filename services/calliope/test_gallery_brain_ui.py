"""Brain navigation in the real Gallery shell, including delayed fetches and errors."""
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import pytest
import server
import calliope


def fixture():
    items=[];nodes=[];edges=[]
    for i in range(12):
        family='documents' if i<6 else 'memory';ref=('source:' if i<6 else 'bank:')+str(i)
        name=['Meeting transcripts','Linear tickets','Company SOPs','System learning','Google Docs','Sales playbooks','Calliope','Iris · Dashboard studio','Hestia · Warehouse steward','Nemesis · Analytical skeptic','Semantic architect','Knowledge gardener'][i]
        items.append(dict(agent_name='Calliope' if i==6 else 'Iris' if i==7 else None,avatar_url='/calliope/specialists/callie-avatar-night.jpg' if i==6 else '/calliope/specialists/iris.png' if i==7 else None,ref=ref,family=family,name=name,kind='Google Meet' if i==0 else 'Hindsight' if i>5 else 'manual',enabled=True,documents=500+i,memories=10000+i,links=24000+i,entities=200,chunks=15000,ready=480,pending=20,errors=0,updated_at='2026-09-15T05:00:00Z',latest_memory_at='2026-09-15T05:00:00Z'))
        nodes.append(dict(id=ref,label=name,kind=family,root=True,sources=[ref]))
        for j in range(25):
            key=ref+'/'+str(j);nodes.append(dict(id=key,label='Document' if i<6 else 'world',kind='document' if i<6 else 'memory',root=False,sources=[ref]));edges.append(dict(source=ref,target=key,kind='contains'))
            if j:edges.append(dict(source=ref+'/'+str(j-1),target=key,kind='semantic'))
    for family,graph,name in [('catalog','db_catalog','Database catalog'),('context','business_context','Business topology')]:
        ref='graph:'+graph
        items.append(dict(ref=ref,family=family,name=name,kind=name,nodes=200,
                          counts={'db_table':100,'db_column':100} if family=='catalog' else {'business_domain':20,'business_concept':180},updated_at='2026-09-15T05:00:00Z'))
        nodes.append(dict(id=ref,label=name,kind=family,root=True,sources=[ref]))
        for j in range(25):
            key=ref+'/'+str(j)
            nodes.append(dict(id=key,label='sales.orders' if family=='catalog' else 'Revenue',kind='db_table' if family=='catalog' else 'business_concept',family=family,root=False,sources=[ref]))
            edges.append(dict(source=ref,target=key,kind='contains'))
    edges.append(dict(source='graph:business_context/0',target='graph:db_catalog/0',kind='same_table'))
    return dict(items=items,graph=dict(nodes=nodes,edges=edges,unavailable=[],sample_per_source=12,total_sources=len(items),shown_sources=len(items)),selected='',unavailable=[],observed_at='2026-09-15T06:00:00Z')


def test_brain_gallery(monkeypatch,tmp_path):
    sync_playwright=pytest.importorskip('playwright.sync_api').sync_playwright
    monkeypatch.setattr(calliope,'is_enabled',lambda:True)
    html=server._landing_html([], 'admin@example.com',can_administer=True)
    assert 'data-gallery-view="brain"' not in server._landing_html([], 'user@example.com',can_administer=False)
    data=json.loads(Path(os.environ['BRAIN_UI_FIXTURE']).read_text()) if os.environ.get('BRAIN_UI_FIXTURE') else fixture()
    root=Path(__file__).resolve().parent
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1600,'height':1100});errors=[];requests=[];avatars=[];fail=[False]
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            u=urlsplit(r.request.url)
            if u.path=='/gallery':r.fulfill(body=html,content_type='text/html');return
            if u.path=='/api/calliope/gallery/brain':
                requests.append(r.request.url)
                if fail[0]:r.fulfill(status=503,json={'error':{'message':'Try again shortly'}});return
                ref=parse_qs(u.query).get('ref',[''])[0]
                graph=dict(data['graph']);graph['nodes']=[n for n in graph['nodes'] if not ref or ref in n['sources']];ids={n['id'] for n in graph['nodes']};graph['edges']=[e for e in graph['edges'] if e['source'] in ids and e['target'] in ids]
                r.fulfill(json=dict(data,selected=ref,graph=graph));return
            if u.path.startswith('/calliope/specialists/'):avatars.append(r.request.url)
            if u.path.startswith('/api/'):r.fulfill(json={'metrics':[],'items':[],'jobs':[],'home':[]});return
            p=root/u.path.lstrip('/').replace('calliope/specialists/', 'calliope/')
            if p.is_file() and p.is_relative_to(root):r.fulfill(body=p.read_bytes(),content_type=mimetypes.guess_type(p)[0] or 'application/octet-stream');return
            r.fulfill(status=204)
        page.route('**/*',route)
        page.goto('http://gallery.test/gallery?view=brain',wait_until='networkidle')
        page.wait_for_function("document.querySelectorAll('[data-brain-source]').length>1")
        assert page.locator('#gallery-brain').is_visible() and page.locator('#artifact-browser').is_hidden()
        assert page.locator('#gallery-tables').is_hidden() and page.locator('#gallery-jobs').is_hidden()
        assert page.locator('#gallery-kicker').text_content()=='Knowledge inventory'
        assert page.locator('.brain-family').evaluate_all('(els)=>els.map(e=>e.dataset.family)')==['catalog','context','documents','memory']
        assert page.locator('.brain-source:not(.brain-source-all)').first.bounding_box()['height']<75
        assert page.locator('[data-gallery-view="brain"]').get_attribute('aria-selected')=='true'
        page.wait_for_timeout(1800);page.screenshot(path=str(tmp_path/'brain-all.png'),full_page=True)
        assert page.locator('.brain-family[data-family=catalog]').is_visible()
        assert page.locator('.brain-family[data-family=context]').is_visible()
        assert 'unlinked bank' in page.locator('.brain-feedback').inner_text()
        for ref,label in [('graph:db_catalog','Database catalog'),('graph:business_context','Business topology')]:
            page.locator(f'[data-brain-source="{ref}"]').click()
            page.wait_for_function("label=>document.querySelector('.brain-map-heading strong').textContent===label",arg=label)
            page.screenshot(path=str(tmp_path/(ref.split(':')[1]+'.png')),full_page=True)
            assert 'nodes' in page.locator(f'[data-brain-source="{ref}"]').inner_text()
        page.locator('[data-brain-source=""]').click()
        page.wait_for_function("document.querySelector('.brain-map-heading strong').textContent==='All knowledge sources'")
        # Requests only on demand; navigating back reuses the current observation.
        count=len(requests);page.locator('[data-gallery-view="artifacts"]').click();assert page.locator('#gallery-brain').is_hidden()
        page.locator('[data-gallery-view="brain"]').click();assert len(requests)==count
        target=data['items'][0];page.locator('[data-brain-source]').nth(1).click()
        page.wait_for_function("document.querySelector('.brain-map-heading strong').textContent!=='All knowledge sources'")
        selected=page.locator('[data-brain-source][aria-pressed=true]').get_attribute('data-brain-source');assert selected
        assert 'brain_source=' in page.url
        page.locator('[data-brain-zoom="1.25"]').click();page.locator('[data-brain-fit]').click()
        page.locator('#gallery-brain canvas').focus();page.keyboard.press('+');page.keyboard.press('ArrowRight');page.keyboard.press('0')
        page.screenshot(path=str(tmp_path/'brain-selected.png'),full_page=True)
        fail[0]=True;page.locator('[data-brain-refresh]').click();page.wait_for_function("document.querySelector('.brain-feedback').textContent.includes('Try again')")
        assert page.locator('[data-brain-source]').count()==len(data['items'])+1 # retain successful inventory
        fail[0]=False;page.locator('[data-brain-source=""]').click();page.wait_for_function("document.querySelector('.brain-map-heading strong').textContent==='All knowledge sources'")
        page.locator('#q').fill('no such source');assert page.locator('.brain-empty').is_visible();assert 'No graph nodes' in page.locator('.brain-empty').inner_text()
        page.locator('#q').fill('');page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(tmp_path/'brain-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('[data-gallery-view="data"]').click();assert page.locator('#gallery-brain').is_hidden()
        assert avatars and all('thumbnail=1' in url for url in avatars)
        assert not errors,errors
        browser.close()


def test_brain_page_scrollbars_take_the_palette():
    """The source list is the Brain view's only scroll pane; it and the page bar (while the Brain
    view shows) are themed with the palette instead of the browser default (2026-09-23)."""
    from pathlib import Path
    css = (Path(__file__).resolve().parent / "theme" / "gallery-brain.css").read_text(encoding="utf-8")
    assert ".brain-sources,html:has(#gallery-brain:not([hidden])){scrollbar-width:thin;scrollbar-color:color-mix(in oklch,var(--jade)" in css
    assert ".brain-sources::-webkit-scrollbar-thumb,html:has(#gallery-brain:not([hidden]))::-webkit-scrollbar-thumb{" in css
