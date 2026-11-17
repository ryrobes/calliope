"""Named captures stay immutable and usable by the real Stage image route."""
from pathlib import Path
import pytest
import server
import calliope
from test_zen_chat_layout import zen_page


@pytest.mark.parametrize('absolute', [False, True])
def test_named_captures_are_retained_even_after_export_overwrite(tmp_path, monkeypatch, absolute):
    monkeypatch.chdir(tmp_path)
    root=tmp_path/'renderer'
    monkeypatch.setenv('WAREHOUSE_LIVE_APP_CAPTURE_DIR',str(root))
    monkeypatch.setattr(server,'_load_live_app_version',lambda slug:({'runtime_kind':'html'}, {'version':1,'html':'<html/>','manifest':{}}))
    monkeypatch.setattr(server,'_active_calliope_design_profile',lambda:None)
    monkeypatch.setattr(server,'_capturable_dashboard_html',lambda *a:'<html/>')
    images=iter([b'\x89PNG\r\n\x1a\nfirst',b'\x89PNG\r\n\x1a\nsecond'])
    def capture(html,out,*args,**kwargs):
        out.write_bytes(next(images))
        return {'queries':[], 'readiness':{'ready':True},'console_errors':[],'page_errors':[]}
    monkeypatch.setattr(server,'_capture_html_with_playwright',capture)
    export=tmp_path/'review.png' if absolute else Path('review.png')
    first=server.tool_capture_live_app('example',path=str(export))
    second=server.tool_capture_live_app('example',path=str(export))
    assert first['path']!=second['path']
    assert export.read_bytes().endswith(b'second')
    config=calliope.CalliopeConfig(hermes_url='http://unused', hermes_api_key='', memory_key='', max_image_bytes=1000000, file_root=tmp_path/'durable')
    for result,expected in [(first,b'first'),(second,b'second')]:
        retained=Path(result['path'])
        assert retained.is_relative_to(root)
        assert retained.read_bytes().endswith(expected)
        published=calliope._copy_capture_file(result['path'],config,'session')
        assert published and Path(published['storage_path']).read_bytes().endswith(expected)
        retained.unlink()
        assert Path(published['storage_path']).read_bytes().endswith(expected)


def test_missing_historical_capture_has_open_revision_fallback(zen_page):
    page=zen_page
    page.route('**/missing-capture.png',lambda r:r.fulfill(status=404))
    page.evaluate('''() => {
      qa.state.surfaces=[
        {id:'old',turn_id:'one',kind:'artifact',title:'Old revision',artifact_slug:'example',artifact_version:1,payload:{url:'/apps/example?v=1'}},
        {id:'new',turn_id:'one',kind:'artifact',title:'New revision',artifact_slug:'example',artifact_version:2,payload:{url:'/apps/example?v=2'}},
        {id:'capture',turn_id:'one',kind:'image',title:'Capture',tool_name:'capture_live_app',artifact_slug:'example',artifact_version:1,payload:{image_url:'/missing-capture.png'}}
      ];qa.renderStage();
    }''')
    card=page.locator('[data-surface-id="old"]')
    fallback=card.locator('[data-image-preview-fallback]')
    fallback.wait_for(state='visible')
    assert 'Saved screenshot unavailable' in fallback.text_content()
    assert 'v=1' in fallback.get_attribute('href')
    assert card.locator('img').is_hidden()


@pytest.mark.parametrize('case,expected', [('valid','recoverable'), ('overwritten','overwritten_after_receipt'), ('wrong_size','size_mismatch'), ('escape','outside_named_capture_scope'), ('symlink','symlink')])
def test_legacy_recovery_does_not_substitute_newer_or_unrelated_files(tmp_path,case,expected):
    import importlib.util
    from datetime import datetime,timezone
    from PIL import Image
    source=Path(__file__).parents[2]/'scripts/migrations/repair-named-stage-captures.py'
    spec=importlib.util.spec_from_file_location('repair_captures',source)
    repair=importlib.util.module_from_spec(spec);spec.loader.exec_module(repair)
    path=tmp_path/'review.png'
    Image.new('RGB',(4,4)).save(path)
    timestamp=path.stat().st_mtime
    row={'payload':{'path':'review.png','bytes':path.stat().st_size}, 'created_at':datetime.fromtimestamp(timestamp+1,timezone.utc)}
    if case=='overwritten':row['created_at']=datetime.fromtimestamp(timestamp-1,timezone.utc)
    if case=='wrong_size':row['payload']['bytes']+=1
    if case=='escape':row['payload']['path']='../review.png'
    if case=='symlink':
        path.rename(tmp_path/'actual.png');path.symlink_to(tmp_path/'actual.png')
    _,status=repair.candidate(row,tmp_path,100000)
    assert status==expected
