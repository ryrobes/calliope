"""Preview updates through the real polling module and full Floor stylesheet."""
import struct
import zlib
from test_zen_chat_layout import zen_page


def png(rgb):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',8,8,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes(rgb)*8)*8))+chunk(b'IEND',b'')


def setup_preview(page):
    item={'key':'draft','turn_id':'one','agent_key':'calliope','filename':'draft.html','phase':'preview',
          'revision':1,'updated_at':1,'lines':[40]*72,'preview_html':'<h1>First draft</h1>'}
    page.route('**/api/calliope/sessions/notebook/artifact-work',lambda r:r.fulfill(json={'items':[item]}))
    page.route('**/draft-one.png',lambda r:r.fulfill(body=png((220,30,40)),content_type='image/png'))
    page.route('**/draft-two.png',lambda r:r.fulfill(body=png((30,40,220)),content_type='image/png'))
    page.evaluate("qa.state.surfaces=[];qa.renderChat();CalliopeArtifactWork.setSession('notebook')")
    page.locator('.artifact-build-preview[data-transition=idle] iframe').wait_for()
    return item


def test_html_previews_wait_then_fade_in_card_and_dialog(zen_page):
    page=zen_page
    item=setup_preview(page)
    page.get_by_role('button',name='Preview',exact=True).click()
    page.locator('.artifact-build-dialog-preview[data-transition=idle] iframe').wait_for()
    # Hold the browser's iframe-load notification: sandboxed documents use an
    # opaque origin and their network interception varies across Chromium builds.
    page.evaluate("""() => {
      window.pendingDraftLoads=[];
      window.holdDraftLoad=e=>{if(e.target.matches?.('.artifact-build-frame')){
        e.stopImmediatePropagation();pendingDraftLoads.push(e.target);
      }};
      document.addEventListener('load',holdDraftLoad,true);
    }""")
    item.update(revision=2,preview_html='<h1>Second draft</h1>')
    page.wait_for_function('pendingDraftLoads.length===2')
    assert page.locator('.artifact-build-preview').get_attribute('data-transition')=='loading'
    assert page.locator('.artifact-build-frame').count()==4
    # Both prior frames remain visible while their successors load.
    assert page.locator('.artifact-build-preview iframe').first.evaluate('el=>getComputedStyle(el).opacity')=='1'
    assert page.locator('.artifact-build-dialog-preview iframe').first.evaluate('el=>getComputedStyle(el).opacity')=='1'
    page.evaluate("""() => {
      document.removeEventListener('load',holdDraftLoad,true);
      pendingDraftLoads.forEach(frame=>frame.dispatchEvent(new Event('load')));
    }""")
    page.locator('.artifact-build-preview[data-transition=fade]').wait_for()
    page.locator('.artifact-build-dialog-preview[data-transition=fade]').wait_for()
    page.wait_for_function("document.querySelectorAll('.artifact-build-frame').length===2")
    assert page.frame_locator('.artifact-build-preview iframe').get_by_text('Second draft').count()==1
    page.get_by_role('button',name='Close',exact=True).click()
    page.locator('.artifact-build-dialog').wait_for(state='detached')


def test_app_renderer_switches_and_iteration_keep_preview_edits_and_card_height(zen_page):
    page = zen_page
    item = setup_preview(page)
    item.update(kind='a2ui', filename='visit.app.json', preview_url='/try-app',
                app_previews={v:{'url':'/draft-one.png','stale':False}
                    for v in ['web-desktop','web-phone','native-desktop','native-phone']})
    page.route('**/try-app', lambda r:r.fulfill(content_type='text/html',body='<label>Notes<input></label>'))
    page.locator('.artifact-app-controls button').get_by_text('Phone',exact=True).wait_for()
    page.locator('.artifact-build-preview[data-transition=idle] img').wait_for()
    before = page.locator('.artifact-build-scene').bounding_box()['height']
    page.get_by_role('button',name='Phone',exact=True).click()
    page.get_by_role('button',name='GPUI',exact=True).click()
    page.locator('.artifact-build-preview[data-transition=idle] img').wait_for()
    assert page.locator('.artifact-build-scene').bounding_box()['height'] == before
    page.get_by_role('button',name='Try app',exact=True).click()
    frame = page.frame_locator('.artifact-app-interactive iframe')
    frame.get_by_label('Notes').fill('Keep my preview edit')
    item.update(revision=3,render_status='rendering')
    item['app_previews']['native-phone']['stale']=True
    page.get_by_text('GPUI · isolated Linux layout test · phone · previous revision · actions simulated',exact=True).wait_for()
    assert frame.get_by_label('Notes').input_value() == 'Keep my preview edit'
    item.update(render_status='failed',preview_errors={'preview':'notes: missing binding /unknown'})
    page.get_by_text('Preview update failed · showing the previous revision · notes: missing binding /unknown',exact=True).wait_for()
    assert page.locator('.artifact-build-preview img').count() == 1
    page.get_by_role('button',name='Load latest draft',exact=True).click()
    page.wait_for_function("document.querySelector('.artifact-app-interactive iframe').contentDocument.querySelector('input')?.value === ''")
    page.get_by_role('button',name='Close',exact=True).click()


def test_screenshot_updates_use_gl_and_mix_real_pixels(zen_page):
    page=zen_page
    item=setup_preview(page)
    item.update(revision=2,render_url='/draft-one.png',render_kind='layout',preview_kind='incoming',phase='composing')
    page.locator('.artifact-build-preview[data-transition=fade]').wait_for()
    page.locator('.artifact-build-preview[data-transition=idle] img').wait_for()
    assert 'updates as code arrives' in page.locator('.artifact-build-caption').inner_text()
    page.get_by_role('button',name='Preview',exact=True).click()
    page.locator('.artifact-build-dialog-preview[data-transition=idle] img').wait_for()
    # A delayed screenshot of the same source still uses the full transition.
    item.update(render_url='/draft-two.png')
    page.locator('.artifact-build-preview[data-transition=gl]').wait_for()
    page.locator('.artifact-build-dialog-preview[data-transition=gl]').wait_for()
    # The overlay must actually blend the two textures, not merely get mounted.
    page.wait_for_function('''() => {
      const canvas=document.querySelector('.artifact-build-preview canvas');if(!canvas)return false;
      const gl=canvas.getContext('webgl'),pixel=new Uint8Array(4);
      gl.readPixels(canvas.width/2,canvas.height/2,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);
      return pixel[0]>65 && pixel[2]>65 && pixel[3]>200;
    }''')
    page.locator('.artifact-build-preview[data-transition=idle] img').wait_for()
    assert page.locator('.artifact-build-preview img').count()==1
    assert page.locator('.artifact-build-preview img').get_attribute('src')=='/draft-two.png'
    assert page.locator('.artifact-build-transition').count()==0


def test_webgl_unavailable_still_crossfades_and_reduced_motion_cleans_up(zen_page):
    page=zen_page
    item=setup_preview(page)
    page.evaluate('''() => {
      const original=HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext=function(kind,...args){return kind==='webgl'?null:original.call(this,kind,...args)};
    }''')
    item.update(revision=2,render_url='/draft-one.png')
    page.locator('.artifact-build-preview[data-transition=idle] img').wait_for()
    item.update(revision=3,render_url='/draft-two.png')
    page.locator('.artifact-build-preview[data-transition=fade]').wait_for()
    assert page.locator('.artifact-build-preview img').count()==2
    page.locator('.artifact-build-preview[data-transition=idle] img').wait_for()
    assert page.locator('.artifact-build-preview img').count()==1
    page.emulate_media(reduced_motion='reduce')
    item.update(revision=4,render_url='/draft-one.png')
    page.locator('.artifact-build-preview[data-transition=idle] img[src="/draft-one.png"]').wait_for()
    assert page.locator('.artifact-build-preview img').count()==1
    page.evaluate("CalliopeArtifactWork.setSession(null)")
    assert page.locator('.artifact-build').count()==0
