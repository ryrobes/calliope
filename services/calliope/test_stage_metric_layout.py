"""Real Stage metric rendering and decision placement, with local fixtures."""
import pytest
from test_zen_chat_layout import zen_page


def test_decision_sits_beside_chat_and_preserves_reading_space(zen_page):
    page=zen_page
    page.evaluate('''() => {
      qa.state.turns[0].assistant_message='Compare the regional results before choosing a direction. '.repeat(80);
      qa.renderChat();qa.renderStage();
    }''')
    for zen,width in [(True,1500),(True,1000),(False,1500)]:
        page.set_viewport_size({'width':width,'height':900})
        page.evaluate("zen=>{qa.state.zen=zen;document.body.classList.toggle('zen',zen);qa.renderChat()}",zen)
        panel=page.locator('#chat-decisions');chat=page.locator('#calliope-chat')
        p,c=panel.bounding_box(),chat.bounding_box()
        assert p['x']>=0 and p['x']+p['width']<=c['x']-5
        assert not panel.evaluate("n=>!!n.closest('#calliope-chat')")
        before=page.locator('#messages').bounding_box()['height']
        page.evaluate("qa.state.surfaces[0].payload.request_state.status='cancelled';qa.renderChat()")
        after=page.locator('#messages').bounding_box()['height']
        # Resolving adds the composer; the form never takes transcript space.
        assert before>=after
        page.evaluate("qa.state.surfaces[0].payload.request_state.status='open';qa.renderChat()")
    page.evaluate("qa.state.zen=true;document.body.classList.add('zen')")
    page.set_viewport_size({'width':1500,'height':900})
    page.screenshot(path='/tmp/decision-beside-chat.png')
    page.set_viewport_size({'width':390,'height':700})
    page.evaluate("document.body.classList.add('mobile-chat-open');qa.renderChat()")
    page.wait_for_timeout(200)
    panel=page.locator('#chat-decisions').bounding_box();messages=page.locator('#messages').bounding_box()
    assert messages['height']>100
    assert messages['y']+messages['height']<=panel['y']
    assert page.locator('#chat-decisions').evaluate("n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+20,r.y+20))}")
    page.screenshot(path='/tmp/decision-beside-chat-mobile.png')


@pytest.mark.parametrize('unit',['count',' COUNT ','counts'])
def test_metric_presentation_units_trend_and_fade(zen_page,unit):
    page=zen_page
    page.evaluate('''unit => {
      qa.state.surfaces=[{id:'metric-one',turn_id:'one',kind:'metric',title:'Reported crime incidents',payload:{
        result:319070,display:{unit},description:'Recorded offenses across the reporting regions.',frozen:true,
        category:'Public safety',grain:'one offense per incident',definition_version:1,data_as_of:'2026-09-09T20:00:00Z',
        trend:{percent:4.6,meaning:'neutral'},observations:[
          {value:280000,observed_at:'2026-09-01'},{value:286000,observed_at:'2026-09-02'},
          {value:275000,observed_at:'2026-09-03'},{value:310000,observed_at:'2026-09-04'},
          {value:305000,observed_at:'2026-09-05'},{value:319070,observed_at:'2026-09-06'}]}}];
      qa.renderChat();qa.renderStage();
    }''',unit)
    card=page.locator('.metric-body')
    assert 'count' not in card.locator('.metric-value').inner_text().lower()
    assert '4.6%' in card.locator('.metric-delta').inner_text()
    assert card.locator('.metric-delta').evaluate("n=>getComputedStyle(n).borderRadius")=='999px'
    assert card.locator('linearGradient stop').last.get_attribute('stop-opacity')=='0'
    assert card.locator('.metric-caption').inner_text().find('T20:00')==-1
    chart=card.locator('.metric-trend').bounding_box();description=card.locator('.metric-description').bounding_box()
    assert chart['y']>=description['y']+description['height']
    card.screenshot(path='/tmp/stage-metric-refined.png')
    for display,expected in [({'suffix':' count'},'319.07K'),({'prefix':'count '},'319.07K'),({'unit':'days'},'days'),({'currency':'USD'},'$'),({'format':'percent'},'%')]:
        page.evaluate("d=>{qa.state.surfaces[0].payload.display=d;qa.renderStage()}",display)
        assert expected in card.locator('.metric-value').inner_text()
    page.evaluate("qa.state.surfaces[0].payload.display={};qa.state.surfaces[0].payload.observations=[{value:0},{value:0}];qa.state.surfaces[0].payload.trend=null;qa.renderStage()")
    assert card.locator('.metric-delta').count()==0 # no made-up change from a zero baseline
    assert 'NaN' not in card.inner_html()


def test_stage_and_calliope_metric_lens_use_declared_format(zen_page):
    page = zen_page
    for display, expected in [
        ({'format': 'integer'}, '3'),
        ({}, '3'),  # Older frozen surfaces can have no display metadata.
        ({'format': 'integer', 'currency': 'USD', 'decimals': 2}, '3'),
        ({'format': 'currency', 'currency': 'USD', 'prefix': '$'}, '$3.00'),
        ({'format': 'percent', 'suffix': '%'}, '3%'),
        ({'format': 'number', 'unit': 'days'}, '3 days'),
    ]:
        page.evaluate('''display => {
          const metric={name:'salesforce_submitted_leads',title:'Salesforce Submitted Leads',display,
            snapshot:{value:3},series:[{value:2},{value:3}]};
          qa.state.surfaces=[{id:'metric-one',turn_id:'one',kind:'metric',title:metric.title,
            payload:{name:metric.name,result:3,display,frozen:true,observations:metric.series}}];
          qa.renderStage();qa.renderCalliopeMetric(metric);
        }''', display)
        assert page.locator('.metric-body .metric-value').inner_text() == expected
        assert page.locator('.calliope-metric-hero-copy strong').text_content() == expected
        assert page.locator('.calliope-metric-chart circle title').last.text_content() == expected


def test_decision_temporarily_tucks_plan_without_losing_its_state(zen_page):
    page=zen_page
    page.evaluate("""() => {
      const plan={confirmed:true,revision:1,todos:[{id:'work',status:'in_progress',content:'Prepare the regional comparison'}]};
      qa.state.turns[0].status='running';
      const run={sessionId:'notebook',pending:qa.state.turns[0],status:'running'};
      qa.state.activeRuns.set('notebook',run);qa.applyStagePlan(run,plan);
      qa.renderChat();qa.renderStage();
    }""")
    assert page.locator('#stage-turn-plan').is_hidden()
    page.evaluate("qa.state.surfaces[0].payload.request_state.status='answered';qa.renderChat()")
    assert page.locator('#stage-turn-plan').is_visible()
    assert 'Prepare the regional comparison' in page.locator('#stage-turn-plan').inner_text()
