"""Real browser checks for profile previews and viewer-adaptive artifact behavior."""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_calliope import calliope
from test_zen_chat_layout import zen_page

HERE = Path(__file__).parent


def theme(mode):
    return {
        'schema_version': 'rvbbit.viewer-theme.v1', 'mode': mode,
        'tokens': {
            '--background': '#f4f1e8' if mode == 'light' else '#081b25',
            '--foreground': '#15232b' if mode == 'light' else '#edf6fa',
            '--panel': '#e7e3d8' if mode == 'light' else '#102c38',
            '--panel-raised': '#dedacc' if mode == 'light' else '#183744',
            '--fog': '#52616a' if mode == 'light' else '#a8bcc6',
            '--main': '#198275', '--rvbbit-accent': '#b56832',
            '--chart-1': '#198275', '--chart-2': '#b56832',
        },
        'background': {'mode': 'image', 'wallpaper': '/bg/the_black_site.jpg',
                       'solid_color': '#000000', 'wallpaper_opacity': .64},
    }


@pytest.mark.parametrize('embedded', [False, True])
@pytest.mark.parametrize('colors_only', [False, True])
def test_adaptive_artifact_keeps_colors_and_enforces_background_policy(embedded, colors_only):
    pw = pytest.importorskip('playwright.sync_api')
    spec = calliope._builtin_design_profile_specs()[int(colors_only)]
    manifest = {'design_profile': {'tokens': spec['tokens']}}
    html = ('<!doctype html><html><head><script>window.RVBBIT_DASHBOARD='
            + json.dumps({'manifest': manifest})
            + ';</script><script src="/theme/adaptive-artifact.js"></script></head>'
            '<body><h1>Quarterly overview</h1><p class="eyebrow">Current period</p>'
            '<code>123.45</code><main>Capacity needs attention.</main></body></html>')
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        requests, errors = [], []
        page.on('pageerror', lambda err: errors.append(str(err)))
        def route(r):
            path = urlsplit(r.request.url).path
            requests.append(path)
            if path == '/artifact': r.fulfill(body=html, content_type='text/html')
            elif path == '/stage': r.fulfill(body='<iframe src="/artifact" sandbox="allow-scripts"></iframe>', content_type='text/html')
            elif path == '/theme/adaptive-artifact.js':
                r.fulfill(body=(HERE/'theme/adaptive-artifact.js').read_text(), content_type='text/javascript')
            else: r.fulfill(status=204)
        page.route('**/*', route)
        if not embedded:
            page.add_init_script('localStorage.setItem("rvbbit-warehouse-adaptive-theme-v1",'+json.dumps(json.dumps(theme('dark')))+');')
        page.goto('http://design.test/stage' if embedded else 'http://design.test/artifact')
        frame = page.frames[1] if embedded else page.main_frame
        def apply(snapshot):
            if embedded:
                page.evaluate('(snapshot)=>document.querySelector("iframe").contentWindow.postMessage({type:"rvbbit.adaptive-theme.apply",snapshot},"*")', snapshot)
            else:
                page.evaluate('''snapshot=>{localStorage.setItem('rvbbit-warehouse-adaptive-theme-v1',JSON.stringify(snapshot));
                    window.dispatchEvent(new StorageEvent('storage',{key:'rvbbit-warehouse-adaptive-theme-v1'}));}''', snapshot)
        for mode in ['dark', 'light']:
            apply(theme(mode))
            frame.wait_for_function('(mode)=>document.documentElement.dataset.rvbbitColorMode===mode', arg=mode)
            result = frame.evaluate('''() => {
              const r=document.documentElement,s=getComputedStyle(r);
              return {bg:s.getPropertyValue('--artifact-bg').trim(),accent:s.getPropertyValue('--artifact-accent').trim(),
                wallpaper:s.getPropertyValue('--artifact-wallpaper').trim(),backdrop:getComputedStyle(r,'::before').backgroundImage,
                mode:r.dataset.rvbbitBackgroundMode,blur:s.getPropertyValue('--artifact-backdrop-blur').trim(),
                label:getComputedStyle(document.querySelector('.eyebrow')).fontFamily,
                code:getComputedStyle(document.querySelector('code')).fontFamily};
            }''')
            assert result['bg'] == theme(mode)['tokens']['--background']
            assert result['accent'] == '#198275'
            assert 'Superior Mono' not in result['label'] and 'Superior Mono' in result['code']
            assert result['mode'] == ('solid' if colors_only else 'image')
            if colors_only:
                assert result['wallpaper'] == result['backdrop'] == 'none'
                assert result['blur'] == '0px'
            else:
                assert 'the_black_site.jpg' in result['wallpaper']
        # An image-free artifact does not fetch the fallback or viewer wallpaper.
        if colors_only: assert not any(path.startswith('/bg/') for path in requests)
        assert not errors
        browser.close()


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_design_editor_previews_both_adaptive_profiles_and_flat_tokens(zen_page, mode):
    page = zen_page
    specs = calliope._builtin_design_profile_specs()
    profiles = [{**spec, 'is_builtin': True, 'is_adaptive': True, 'is_default': i == 0,
                 'can_edit': False, 'current_version': spec['version'],
                 'version': {'id': spec['version_id'], 'version': spec['version'],
                             'markdown': spec['markdown'], 'tokens': spec['tokens']}}
                for i, spec in enumerate(specs)]
    page.evaluate('''({profiles,snapshot})=>{
      qa.state.designProfiles=profiles;
      window.WarehouseTheme={getSnapshot:async()=>snapshot};
      document.querySelector('#style-library-dialog').showModal();
    }''', {'profiles': profiles, 'snapshot': theme(mode)})
    for i, spec in enumerate(specs):
        page.evaluate('''({id,versionId})=>{
          qa.state.designProfileId=id;qa.state.designProfileVersionId=versionId;qa.renderDesignEditor();
        }''', {'id': spec['id'], 'versionId': spec['version_id']})
        page.wait_for_function('''(bg)=>document.querySelector('#style-preview').style.getPropertyValue('--sp-bg')===bg''', arg=theme(mode)['tokens']['--background'])
        result = page.locator('#style-preview').evaluate('''el=>({
          wallpaper:el.style.getPropertyValue('--sp-wallpaper'),
          blur:getComputedStyle(el.querySelector('.style-preview-spread')).backdropFilter,
          label:getComputedStyle(el.querySelector('.style-preview-kicker')).fontFamily,
          casing:getComputedStyle(el.querySelector('.style-preview-kicker')).textTransform
        })''')
        assert result['casing'] == 'none' and 'Mono' not in result['label']
        assert ('the_black_site.jpg' in result['wallpaper']) == (i == 0)
        if i: assert result['blur'] == 'blur(0px)'
        if mode == 'dark':
            dest = Path('/tmp/calliope-design-review');dest.mkdir(exist_ok=True)
            page.locator('#style-library-dialog').screenshot(path=str(dest/f'adaptive-{i}.png'))
    # The second builtin must not accidentally become the implicit default.
    assert page.evaluate('''()=>{qa.state.designProfiles.reverse();return qa.adaptiveDesignProfile().id;}''') == specs[0]['id']
    flat = {'tokens': {'palette': {'background': '#ffffff', 'surface': '#eeeeee',
                                   'surface_alt': '#f4f7fa', 'text': '#0f172a', 'muted': '#5b6573'},
                       'effects': {'surface_mode': 'flat', 'shadow': 'none'}}}
    page.evaluate('(v)=>qa.applyDesignPreviewVariables(v)', flat)
    assert page.locator('#style-preview').evaluate('(el)=>getComputedStyle(el).boxShadow') == 'none'
    assert page.locator('.style-preview-spread').evaluate('(el)=>getComputedStyle(el).backdropFilter') == 'blur(0px)'
    page.evaluate('qa.renderDesignEditor()')
    page.wait_for_function('''(bg)=>document.querySelector('#style-preview').style.getPropertyValue('--sp-bg')===bg''', arg=theme(mode)['tokens']['--background'])
    page.set_viewport_size({'width': 390, 'height': 844})
    bounds = page.locator('#style-editor-pane').evaluate('(el)=>({width:el.clientWidth,content:el.scrollWidth})')
    assert bounds['content'] <= bounds['width'] + 1
    editor_bottom = page.locator('#style-markdown').bounding_box()['y'] + page.locator('#style-markdown').bounding_box()['height']
    assert page.locator('.style-editor-actions').bounding_box()['y'] >= editor_bottom
    page.locator('#style-library-dialog').screenshot(path='/tmp/calliope-design-review/narrow.png')
