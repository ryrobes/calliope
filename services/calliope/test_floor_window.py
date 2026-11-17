"""Continuous variable-height columns: bounded DOM, stable nodes and automatic paging."""
from pathlib import Path
from test_floor_metrics_browser import browser_page  # noqa:F401


def install(page):
    page.set_content('<style>.lane-body{height:500px;overflow:auto;overflow-anchor:none;width:300px}.piece{box-sizing:border-box;margin:0 0 8px}[data-window-cards]{display:flow-root}</style><div class="lane-body"></div>')
    page.add_script_tag(path=str(Path(__file__).parent/'calliope/floor-stream.js'))


def test_large_lane_keeps_dom_bounded_and_visible_nodes_stable(browser_page):
    page=browser_page;install(page)
    page.evaluate('''() => {
      window.list=Array.from({length:3000},(_,i)=>({item_id:'p:'+i,height:80+i%4*30}));
      window.view=new FloorWindow(document.querySelector('.lane-body'),{items:list,total:list.length,
        markup:item=>`<article class="piece" data-id="${item.item_id}" tabindex="0" style="height:${item.height}px"><button>${item.item_id}</button></article>`});
    }''')
    page.wait_for_timeout(100)
    assert page.locator('.piece').count()<30
    assert page.locator('[data-window-more]').is_hidden()
    page.evaluate("window.retained=document.querySelector('.piece');retained.querySelector('button').focus();document.querySelector('.lane-body').scrollTop=20")
    page.wait_for_timeout(100)
    assert page.evaluate("retained===document.querySelector('.piece') && document.activeElement===retained.querySelector('button')")
    page.evaluate('view.status(true,null);view.status(false,null)')
    assert page.evaluate("retained===document.querySelector('.piece')")
    page.locator('.lane-body').evaluate('el=>el.scrollTop=8000')
    page.wait_for_timeout(150)
    assert page.locator('.piece').count()<30
    assert int(page.locator('.piece').last.get_attribute('data-id').split(':')[1])>40
    page.locator('.lane-body').evaluate('el=>el.scrollTop=el.scrollHeight')
    page.locator('[data-id="p:2999"]').wait_for()
    assert page.locator('.piece').count()<30


def test_short_pages_fill_viewport_automatically_and_retry_is_explicit(browser_page):
    page=browser_page;install(page)
    page.evaluate('''() => {
      window.calls=0;window.failed=false;
      const body=document.querySelector('.lane-body');
      window.view=new FloorWindow(body,{onMore:()=>transport.request('done'),markup:item=>`<article class="piece" data-id="${item.item_id}" style="height:40px">${item.item_id}</article>`});
      window.transport=new FloorStream(async params=>{
        calls++;const start=Number(params.cursor||0);if(failed)throw Error('offline');
        await new Promise(r=>setTimeout(r,10));
        return {cards:[start,start+1].map(i=>({item_id:'p:'+i})),total:80,lanes:[],pages:{done:{next_cursor:String(start+2),has_more:true}}};
      },(data,event)=>{if(event.loading)view.status(true,null);else if(event.error)view.status(false,event.error);else view.update(data.cards,data.total)});
      transport.reset({});
    }''')
    page.wait_for_function("""() => {
      if(window.observedCalls!==calls){window.observedCalls=calls;window.observedAt=performance.now()}
      return calls>=8 && !view.loading && performance.now()-window.observedAt>120;
    }""")
    calls=page.evaluate('calls');page.wait_for_timeout(100)
    assert page.evaluate('calls')==calls and calls<30
    page.evaluate("failed=true;document.querySelector('.lane-body').scrollTop=999999")
    page.locator('[data-window-more]').wait_for(state='visible')
    calls=page.evaluate('calls');page.wait_for_timeout(150);assert page.evaluate('calls')==calls
    page.evaluate('failed=false');page.locator('[data-window-more]').click()
    page.wait_for_function('!view.error && !view.loading')
    assert page.evaluate('calls')>calls


def test_drag_source_survives_scroll_and_background_updates(browser_page):
    page=browser_page;install(page)
    page.evaluate('''() => {
      const items=Array.from({length:100},(_,i)=>({item_id:String(i)}));
      window.view=new FloorWindow(document.querySelector('.lane-body'),{items,total:100,markup:i=>`<article class="piece" data-id="${i.item_id}" style="height:100px">${i.item_id}</article>`});
      window.source=document.querySelector('.piece');source.classList.add('dragging');
      document.querySelector('.lane-body').scrollTop=5000;view.status(true,null);
    }''')
    page.wait_for_timeout(100);assert page.evaluate('source.isConnected')
    page.evaluate("source.classList.remove('dragging');source.dispatchEvent(new Event('dragend',{bubbles:true}))")
    page.wait_for_function('!source.isConnected')


def test_late_height_changes_preserve_the_visible_item(browser_page):
    page=browser_page;install(page)
    page.evaluate('''() => {
      window.items=Array.from({length:100},(_,i)=>({item_id:String(i),height:100}));
      window.view=new FloorWindow(document.querySelector('.lane-body'),{items,total:100,markup:i=>`<article class="piece" data-id="${i.item_id}" style="height:${i.height}px">${i.item_id}</article>`});
    }''')
    page.wait_for_timeout(100)
    page.locator('.lane-body').evaluate('el=>el.scrollTop=500');page.wait_for_timeout(100)
    before=page.locator('[data-id="5"]').bounding_box()['y']
    page.evaluate('items[2].height=260;view.update(items,100)');page.wait_for_timeout(100)
    assert abs(page.locator('[data-id="5"]').bounding_box()['y']-before)<2
    assert page.locator('.piece').count()<30
