"""Real Vega rendering must retain authored style through updates and retheming."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

import server


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                     args=['--no-sandbox'])
        page = browser.new_page()
        page.set_content('<style>body{font-family:Georgia;color:#543210}</style><div id="chart" style="width:500px"></div>'
                         + server._dash_runtime_shim('authored-style', 1, {}))
        # The exact bundled renderer, without network or database traffic.
        for url in server._charts_runtime_config()['urls']:
            page.add_script_tag(path=str(Path(server.__file__).parent / url.lstrip('/')))
        yield page
        browser.close()


def test_unpinned_runtime_uses_authored_typography_without_house_palette(page):
    result = page.evaluate('''() => ({tokens:rvbbitTheme.tokens(),config:rvbbitCharts.config()})''')
    assert result['tokens']['palette'] == []
    assert result['tokens']['fontBody'] == 'Georgia'
    assert result['config']['font'] == 'Georgia'
    assert result['config']['title']['color'] == 'rgb(84, 50, 16)'
    assert result['config']['range'] == {}


def test_authored_config_and_encodings_survive_update_and_mode_change(page):
    page.evaluate('''async () => {
      window.opts = {embed:{config:{axis:{labelFont:'Georgia',labelFontSize:16}}},
        config:{font:'Courier New',axis:{labelColor:'#ac1234'},range:{category:['#aa2233','#cc8844']}}};
      window.originalOptions=JSON.stringify(opts);
      window.spec={data:{values:[{kind:'A',value:2},{kind:'B',value:4}]},height:180,
        mark:'bar',encoding:{x:{field:'kind',type:'nominal'},y:{field:'value',type:'quantitative'},
          color:{field:'kind',type:'nominal'}}};
      window.handle=await rvbbitCharts.embed('#chart',spec,opts);
    }''')

    def rendered():
        return page.evaluate('''() => ({
          fills:[...document.querySelectorAll('.mark-rect path')].map(n=>n.getAttribute('fill')),
          labels:[...document.querySelectorAll('.role-axis-label text')].map(n=>({
            color:n.getAttribute('fill'),font:n.getAttribute('font-family'),size:n.getAttribute('font-size')})),
          unchanged:JSON.stringify(opts)===originalOptions
        })''')

    first = rendered()
    assert set(first['fills']) == {'#aa2233', '#cc8844'}
    assert first['labels'] and all(x == {'color': '#ac1234', 'font': 'Georgia', 'size': '16px'}
                                   for x in first['labels'])
    assert first['unchanged']
    page.evaluate('''async()=>{await handle.update({...spec,data:{values:[{kind:'A',value:5},{kind:'B',value:8}]}})}''')
    updated = rendered()
    assert updated['fills'] == first['fills']
    assert all(x in first['labels'] for x in updated['labels'])
    page.evaluate("window.previousView=handle.view;document.documentElement.dataset.rvbbitColorMode=rvbbitTheme.mode()==='dark'?'light':'dark'")
    page.wait_for_function('handle.view!==previousView && rvbbitCharts.pending===0')
    assert rendered() == updated
    # Explicit colors in the spec itself also remain stronger than runtime defaults.
    page.evaluate("async()=>{await handle.update({...spec,encoding:{...spec.encoding,color:{value:'#005599'}}})}")
    assert set(rendered()['fills']) == {'#005599'}


def test_explicit_tokens_are_available_to_selected_profile_or_authored_page(page):
    page.evaluate('''() => {
      for(const [k,v] of Object.entries({'--artifact-font-body':'Courier New',
          '--artifact-accent':'#804020','--artifact-chart-1':'#123456','--artifact-chart-2':'#654321'}))
        document.documentElement.style.setProperty(k,v);
    }''')
    config = page.evaluate('rvbbitCharts.config()')
    assert config['font'] == 'Courier New'
    assert config['bar']['color'] == '#804020'
    assert config['range']['category'] == ['#123456', '#654321']
