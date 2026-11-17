"""Standalone handset browser contract; fixture transport, actual shipped UI."""
import base64
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).with_name('calliope')
PHOTO = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jA1sAAAAASUVORK5CYII=')


@pytest.fixture
def handset():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1200,'height':1000})
        data = {'conversations':[], 'messages':[], 'posts':[], 'drop_once':False}
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(r):
            path = urlsplit(r.request.url).path
            if path == '/calliope/sms-simulator':
                r.fulfill(body=(ROOT/'sms-simulator.html').read_text(), content_type='text/html')
            elif path in ['/calliope/sms-simulator.js','/calliope/sms-simulator.css']:
                r.fulfill(body=(ROOT/path.rsplit('/',1)[-1]).read_bytes(), content_type='text/javascript' if path.endswith('js') else 'text/css')
            elif path.endswith('/simulator'):
                r.fulfill(json={'owner':'joe@example.com','max_image_bytes':8388608,'conversations':data['conversations']})
            elif path.endswith('/conversations'):
                data['conversations'].append({'id':'one','created_at':'2026-10-05T14:00:00Z'})
                r.fulfill(json={'id':'one'})
            elif path.endswith('/messages'):
                payload=r.request.post_data_json;data['posts'].append(payload)
                assert r.request.headers['x-calliope-simulator']=='1'
                if data['drop_once']:
                    data['drop_once']=False;r.abort();return
                data['messages']=[{'id':payload['client_id'],'body':payload['message'],'images':['/fixture.png'] if payload['attachments'] else [],
                                   'direction':'outgoing','state':'delivered','created_at':'2026-10-05T14:00:00Z'},
                                  {'id':'reply','body':'I can see the plate. I’ll check the model and serial before preparing the claim.',
                                   'direction':'incoming','state':'sent','images':[],'created_at':'2026-10-05T14:00:03Z'}]
                r.fulfill(json={'state':'queued'})
            elif path.endswith('/conversations/one'):
                r.fulfill(json={'messages':data['messages'],'session_id':'floor-thread','busy':False,'queued':0})
            elif path == '/fixture.png':r.fulfill(body=PHOTO,content_type='image/png')
            else:r.abort()
        page.route('**/*',route)
        page.goto('https://sms.test/calliope/sms-simulator')
        page.get_by_text('joe@example.com').wait_for()
        yield page,data
        assert not errors
        browser.close()


@pytest.mark.parametrize('width',[1200,390])
def test_photo_only_first_message_replies_recording_and_refresh(handset,width):
    page,data=handset;page.set_viewport_size({'width':width,'height':1000})
    page.locator('#files').set_input_files({'name':'plate.png','mimeType':'image/png','buffer':PHOTO})
    page.get_by_role('button',name='Send message',exact=True).click()
    page.get_by_text('I can see the plate.',exact=False).wait_for()
    assert len(data['posts'])==1 and data['posts'][0]['message']=='' and data['posts'][0]['attachments'][0]['name']=='plate.png'
    assert page.locator('.outgoing .bubble img').is_visible()
    assert page.get_by_role('link',name='Open Callie’s thread').get_attribute('href')=='/calliope?session=floor-thread&view=zen'
    assert page.locator('.phone').evaluate('n=>n.scrollWidth<=n.clientWidth+1')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.get_by_role('button',name='Toggle recording view').click()
    assert not page.locator('.controls').is_visible()
    assert page.locator('.transport-label').is_visible()
    page.screenshot(path=f'/tmp/calliope-sms-simulator-{width}.png')
    page.reload();page.get_by_text('I can see the plate.',exact=False).wait_for()
    assert len(data['posts'])==1


def test_failed_post_retry_reuses_message_id(handset):
    page,data=handset;data['drop_once']=True
    page.get_by_role('textbox',name='Text message').fill('Please check this return.')
    page.get_by_role('button',name='Send message',exact=True).click()
    page.get_by_text('Send again to retry this same message.',exact=False).wait_for()
    assert page.get_by_role('textbox',name='Text message').input_value()=='Please check this return.'
    page.get_by_role('button',name='Send message',exact=True).click()
    page.get_by_text('I can see the plate.',exact=False).wait_for()
    assert len(data['posts'])==2 and data['posts'][0]['client_id']==data['posts'][1]['client_id']
