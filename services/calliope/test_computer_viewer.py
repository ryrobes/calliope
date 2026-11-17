"""Viewer shell integration without a guest or control-plane mutation."""
from pathlib import Path

import pytest
from desktop_viewer import DISCONNECTED_HTML, adapt_ui
from test_zen_chat_layout import zen_page
from test_computer_modes import setup

ROOT = Path(__file__).parent


def viewer(page, *, mock_effect=True, stage=False):
    setup(page)
    if mock_effect:
        page.route('**/calliope/ascii-veil.js', lambda r: r.fulfill(content_type='text/javascript', body='''
          export function mountAsciiVeil(host, options) {
            host.dataset.options=JSON.stringify(options);host.dataset.playing='true';
            return {dispose(){host.dataset.playing='false'}};
          }'''))
    page.route('**/viewer-fixture*', lambda r: r.fulfill(content_type='text/html', body='''
      <!doctype html><html class="noVNC_loading"><head>
      <style>#noVNC_container{background:#282828}#noVNC_transition{background:white}</style>
      <link rel="stylesheet" href="/calliope/computer-viewer.css">
      <script defer src="/calliope/computer-viewer.js"></script></head><body>
      <div id="noVNC_transition">Reconnecting…</div><div id="noVNC_status"></div>
      <div id="noVNC_container"><div style="height:100%;display:flex;align-items:center;justify-content:center;background:#282828">
      <canvas width="1024" height="768" style="width:60%;height:70%"></canvas></div></div>
      </body></html>'''))
    page.locator('#computer-open').click()
    frame = page.frame_locator('.computer-screen iframe')
    frame.locator('#calliope-viewer-status').wait_for()
    return page.frames[-1]


def test_viewer_theme_connection_feedback_and_hidden_animation(zen_page):
    page = zen_page
    frame = viewer(page, stage=True)
    page.wait_for_function("document.querySelector('.computer-screen iframe').contentDocument.querySelector('#calliope-viewer-backdrop').dataset.playing==='true'")
    frame.evaluate("document.documentElement.className='noVNC_connected'")
    frame.wait_for_function("document.querySelector('#calliope-viewer-status span').textContent==='Connected'")
    assert frame.locator('#noVNC_container').is_visible()
    assert frame.locator('#noVNC_transition').is_hidden()
    assert frame.locator('#noVNC_container div').evaluate("e=>getComputedStyle(e).backgroundColor") == 'rgba(0, 0, 0, 0)'
    page.evaluate("document.documentElement.style.setProperty('--void','#142536');document.documentElement.style.setProperty('--amber','#aabbcc');document.querySelector('.computer-shelf').style.color='#102030';window.dispatchEvent(new Event('warehouse-theme-change'))")
    frame.wait_for_function("getComputedStyle(document.documentElement).getPropertyValue('--void')==='#142536'")
    assert frame.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--text')") == 'rgb(16, 32, 48)'
    assert frame.locator('#calliope-viewer-backdrop').evaluate("e=>JSON.parse(e.dataset.options).accent") == '#aabbcc'
    frame.evaluate("document.documentElement.className='noVNC_reconnecting'")
    frame.wait_for_function("document.querySelector('#calliope-viewer-status span').textContent==='Reconnecting…'")
    assert frame.locator('#noVNC_container').is_hidden()  # no stale desktop posing as live
    frame.evaluate("let e=document.querySelector('#noVNC_status');e.textContent='Connection refused';e.className='noVNC_open noVNC_status_error'")
    frame.wait_for_function("document.querySelector('#calliope-viewer-status span').textContent.includes('Connection refused')")
    page.get_by_role('tab', name='Terminal', exact=True).click()
    assert page.locator('.computer-screen').is_visible()
    frame.wait_for_function("document.querySelector('#calliope-viewer-backdrop').dataset.playing==='true'")
    page.locator('.computer-tabs').get_by_role('tab', name='History', exact=True).click()
    frame.wait_for_function("document.querySelector('#calliope-viewer-backdrop').dataset.playing==='true'")
    # A matching payload from the child itself is not trusted as the parent.
    frame.evaluate("window.postMessage({type:'calliope-computer-theme',background:'red'},location.origin)")
    assert frame.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--void')") == '#142536'
    page.locator('#workspace-tab-chat').click()
    assert page.locator('.computer-screen iframe').count() == 1
    frame.wait_for_function("document.querySelector('#calliope-viewer-backdrop').dataset.playing==='false'")


def test_viewer_plain_background_when_effect_unavailable(zen_page):
    page = zen_page
    page.route('**/calliope/ascii-veil.js', lambda r: r.fulfill(content_type='text/javascript', body="export function mountAsciiVeil(){throw new Error('No WebGL')}"))
    frame = viewer(page, mock_effect=False)
    frame.evaluate("document.documentElement.className='noVNC_connected'")
    frame.wait_for_function("document.querySelector('#calliope-viewer-status span').textContent==='Connected'")
    assert frame.locator('#noVNC_container').is_visible()
    assert frame.locator('#calliope-viewer-status').is_visible()


def test_stopped_reconnect_has_explicit_retry(zen_page):
    page = zen_page
    viewer(page)
    page.frame_locator('.computer-screen iframe').locator('#calliope-viewer-status').wait_for()
    frame = page.frames[-1]
    frame.evaluate("document.documentElement.className='noVNC_disconnected'")
    frame.get_by_role('button', name='Reconnect', exact=True).click()
    frame.wait_for_function("document.querySelector('#calliope-viewer-status span')?.textContent==='Loading desktop…'")
    assert frame.get_by_role('button', name='Reconnect', exact=True).is_hidden()


@pytest.mark.parametrize('stage', [False, True])
def test_disconnected_document_recovers_original_viewer_mode(zen_page, stage):
    page = zen_page
    frame = viewer(page, stage=stage)
    original = frame.url
    assert 'view_only' not in original
    page.route('**/disconnected.html', lambda r: r.fulfill(content_type='text/html', body=DISCONNECTED_HTML))
    # A forged event from the outer document cannot reload the iframe.
    frame.evaluate('window.recoverySentinel = true')
    page.evaluate("window.postMessage({type:'calliope-computer-reconnect'}, location.origin)")
    page.wait_for_timeout(100)
    assert frame.evaluate('window.recoverySentinel')
    # Kasm navigates inside the iframe, leaving the parent's src unchanged.
    frame.goto('http://zen.test/disconnected.html')
    page.wait_for_function("document.querySelector('.computer-screen iframe').contentWindow.location.href === document.querySelector('.computer-screen iframe').src")
    assert frame.url == original
    # Repeated failures don't create an infinite automatic reload loop.
    frame.goto('http://zen.test/disconnected.html')
    frame.locator('#calliope-viewer-status').wait_for()
    page.wait_for_timeout(100)
    assert frame.url.endswith('/disconnected.html')
    if stage:
        frame.get_by_role('button', name='Reconnect', exact=True).click()
        page.wait_for_function("document.querySelector('.computer-screen iframe').contentWindow.location.href === document.querySelector('.computer-screen iframe').src")
        assert frame.url == original


def test_embedded_watchdog_does_not_expire_passive_viewer(zen_page):
    # Verbatim interval body from the pinned guest's ui-D357AMxM.js. The actual
    # served bundle is also checked during release, so minifier drift is explicit.
    watchdog = b'''function(){if(o.rfb){const s=(Date.now()-o.rfb.lastActiveAt)/1e3;let l=1200;Number.isFinite(parseFloat(o.rfb.idleDisconnect))&&(l=parseFloat(o.rfb.idleDisconnect)*60),s>l?(wt("Idle Disconnect reached, disconnecting rfb session..."),parent.postMessage({action:"idle_session_timeout",value:"Idle session timeout exceeded"},"*"),setTimeout(function(){window.location.replace("disconnected.html")},1e4)):o.rfb.sendKey(1,null,!1)}}'''
    result = zen_page.evaluate('''source => {
      const exercise = code => {
        const calls = []; const o = {rfb:{lastActiveAt:0, idleDisconnect:20, sendKey:(...args)=>calls.push(args)}};
        const Date = {now:()=>24*60*60*1000}, wt=()=>{};
        const parent={postMessage:()=>calls.push('timeout')}, setTimeout=()=>calls.push('redirect');
        eval('(' + code + ')')();
        return calls;
      };
      return {before:exercise(source.before), after:exercise(source.after)};
    }''', {'before': watchdog.decode(), 'after': adapt_ui(watchdog).decode()})
    assert result['before'] == ['timeout', 'redirect']
    # Preserve Kasm's existing heartbeat, without synthesizing viewer activity.
    assert result['after'] == [[1, None, False]]
