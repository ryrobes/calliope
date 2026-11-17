"""Exercise the real appearance bundle without changing a deployed user's preferences."""
import functools
import http.server
import threading
from pathlib import Path

import pytest
import warehouse_theme


def test_workspace_picker_inheritance_and_save_controls(tmp_path):
    browser_api = pytest.importorskip("playwright.sync_api")
    sync_playwright, expect = browser_api.sync_playwright, browser_api.expect
    root=Path(__file__).resolve().parent
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin='http://127.0.0.1:'+str(server.server_port)
    items=warehouse_theme.theme_library()
    base={'version':1,'source':{'kind':'library',**{k:items[0][k] for k in ['id','url','label']}},'palette':{'baseHue':25,'chroma':.1,'vibrant':'#cc5500','darkVibrant':'#442200','lightVibrant':'#eeaa55'},'tokens':{'--main':'#cc5500','--background':'#110900'}}
    workspace={'appearance':base,'mode':'dark'};personal={'appearance':None,'mode':None};writes=[];fail=False
    html='''<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/theme/superior-fonts.css"><link rel="stylesheet" href="/theme/warehouse-theme.css"><style>:root{--void:#100d0b;--panel:#21170f;--bone:#eee1d6;--dim:#c1a898;--line:#65482f;--main:#ea9955;--sans:Arial;--serif:Georgia;--mono:monospace}body{background:#17100b;color:var(--bone);font-family:Arial}.bg{position:fixed;inset:0;z-index:-1;background:var(--warehouse-wallpaper);background-size:cover}header{padding:24px}button{cursor:pointer}</style><script defer src="/theme/warehouse-theme.js"></script></head><body><div class="bg"></div><header>Calliope <span data-warehouse-theme-anchor></span><button onclick="WarehouseTheme.openWorkspace()">Workspace settings</button></header></body></html>'''
    def handle(route):
     nonlocal fail
     path=route.request.url.split(origin)[-1];method=route.request.method
     if path=='/test':return route.fulfill(body=html,content_type='text/html')
     if path=='/theme/library':return route.fulfill(json={'items':items})
     if path.startswith('/api/calliope/response-preferences'):return route.fulfill(status=401,json={})
     if path=='/api/calliope/admin/workspace':
      if method=='PUT':
       body=route.request.post_data_json;writes.append(('workspace',body))
       if fail:return route.fulfill(status=500,json={'error':{'message':'Save failed; your setting is unchanged.'}})
       workspace.update(body)
      return route.fulfill(json=workspace)
     if path=='/api/calliope/appearance':
      if method=='PUT':
       body=route.request.post_data_json;writes.append(('personal',body));personal.update(body)
      own=personal.get('appearance') is not None
      return route.fulfill(json={'appearance':personal['appearance'] if own else workspace['appearance'], 'mode':personal['mode'] or workspace['mode'],'personal_mode':personal['mode'],'source':'personal' if own else 'workspace' if workspace['appearance'] else 'builtin','owner':'test@example.com','has_upload':False})
     return route.continue_()
    with sync_playwright() as p:
     try:
      browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
     except Exception as error:
      server.shutdown()
      pytest.skip(f'Chromium is unavailable: {error}')
     page=browser.new_page(viewport={'width':1400,'height':960});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',handle)
     page.goto(origin+'/test');page.wait_for_function('window.WarehouseTheme?.getState()?.source?.id')
     assert not writes,'Hydration unexpectedly saved a preference'
     before=page.evaluate('WarehouseTheme.getState()')
     page.get_by_role('button',name='Workspace settings',exact=True).click()
     expect(page.get_by_role('heading',name='A default room for everyone')).to_be_visible()
     expect(page.locator('[data-theme-voice]')).to_be_hidden();expect(page.locator('.warehouse-theme-upload')).to_be_hidden()
     page.locator('[data-theme-id]').nth(1).click()
     page.wait_for_function('document.querySelector("[data-theme-preview-status]").hidden')
     assert page.evaluate('WarehouseTheme.getState()')==before,'Preview changed personal UI'
     page.screenshot(path=str(tmp_path/'workspace-desktop.png'))
     fail=True;page.get_by_role('button',name='Save workspace default',exact=True).click()
     expect(page.locator('[data-theme-error]')).to_contain_text('Save failed')
     expect(page.get_by_role('dialog')).to_be_visible()
     fail=False;page.get_by_role('button',name='Save workspace default',exact=True).click()
     expect(page.get_by_role('dialog')).not_to_be_visible()
     page.wait_for_function('(id)=>WarehouseTheme.getState()?.source?.id===id',arg=items[1]['id'])
     assert not any(t=='personal' for t,_ in writes)
     # Mode-only change must not pin an inherited wallpaper.
     page.evaluate('WarehouseTheme.setMode("light")')
     page.wait_for_timeout(200)
     assert writes[-1]==('personal',{'mode':'light'}),writes[-1]
     assert personal['appearance'] is None
     # Explicit personal choice survives changes to workspace defaults.
     page.evaluate('WarehouseTheme.open()');page.locator('[data-theme-id]').nth(2).click();page.wait_for_function('document.querySelector("[data-theme-preview-status]").hidden')
     page.get_by_role('button',name='Apply',exact=True).click();expect(page.get_by_role('dialog')).not_to_be_visible()
     assert personal['appearance']['source']['id']==items[2]['id']
     page.get_by_role('button',name='Workspace settings',exact=True).click();page.locator('[data-theme-id]').nth(3).click();page.wait_for_function('document.querySelector("[data-theme-preview-status]").hidden')
     page.get_by_role('button',name='Save workspace default',exact=True).click();expect(page.get_by_role('dialog')).not_to_be_visible()
     assert page.evaluate('WarehouseTheme.getState().source.id')==items[2]['id']
     page.evaluate('WarehouseTheme.open()');page.get_by_role('button',name='Use workspace default',exact=True).click();expect(page.get_by_role('dialog')).not_to_be_visible()
     assert page.evaluate('WarehouseTheme.getState().source.id')==items[3]['id']
     assert page.evaluate('WarehouseTheme.getMode()')=='light'
     before_writes=len(writes);page.reload();page.wait_for_function('window.WarehouseTheme?.getState()?.source?.id')
     page.wait_for_timeout(200);assert len(writes)==before_writes
     page.set_viewport_size({'width':780,'height':800});page.get_by_role('button',name='Workspace settings',exact=True).click();expect(page.get_by_role('dialog')).to_be_visible();page.screenshot(path=str(tmp_path/'workspace-narrow.png'))
     assert not errors,errors
     print('Browser checks passed: shared preview, failed save, inheritance, explicit override, mode-only change, reset, reload, narrow layout')
     browser.close()
    server.shutdown()
