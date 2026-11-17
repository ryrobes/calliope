from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import computer_dialogs as dialogs


def test_only_accessibility_workers_use_the_isolated_no_prefetch_client(monkeypatch):
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setenv('LD_LIBRARY_PATH','/system/libraries')
    monkeypatch.delenv('ATSPI_NO_CACHE',raising=False)
    monkeypatch.setattr(dialogs.subprocess,'run',lambda *a,**kw:calls.append(kw) or SimpleNamespace(stdout='{}'))
    dialogs.worker('surface',42)
    assert calls[-1]['env']['ATSPI_NO_CACHE']=='1'
    assert calls[-1]['env']['LD_LIBRARY_PATH']=='/opt/calliope-atspi/lib:/system/libraries'
    dialogs.worker('x11')
    assert 'ATSPI_NO_CACHE' not in calls[-1]['env']
    assert calls[-1]['env']['LD_LIBRARY_PATH']=='/system/libraries'


def layer(wid, pid=42, parent=None, modal=False, focused=False, x=100, y=150):
    return {'window_id': wid, 'pid': pid, 'title': str(wid), 'transient_for': parent,
            'modal': modal, 'dialog': modal, 'focused': focused,
            'bounds': {'x': x, 'y': y, 'width': 500, 'height': 300}}


def test_nested_pidless_confirmation_wins_over_parent_and_unrelated_focus():
    windows = [layer(1), layer(2, parent=1, modal=True), layer(3, pid=None, parent=2, modal=True), layer(4, pid=99, focused=True)]
    target = dialogs.target_layer({'windows': windows, 'active_window_id': 4}, 1, 42)
    assert target['window_id'] == 3 and target['owner_pid'] == 42
    assert [w['window_id'] for w in target['related_windows']] == [1, 2, 3]
    assert dialogs.target_layer({'windows': windows}, 999, 42) is None
    assert dialogs.target_layer({'windows': windows}, 1, 99) is None


def test_ocr_screen_coordinates_include_crop_origin_exactly_once():
    tsv = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    tsv += '5\t1\t1\t1\t1\t1\t455\t113\t30\t16\t93\tYes\n'
    rows = dialogs.ocr_rows(tsv, dict(x=200, y=330, width=630, height=145))
    assert rows[0]['click_arguments'] == dict(scope='desktop', delivery_mode='foreground', x=670, y=451)
    assert rows[0]['source'] == 'ocr' and rows[0]['clickability'] == 'unproven'
    assert rows[0]['role'] == 'text'
    assert not dialogs.ocr_rows(tsv, dict(x=0, y=0, width=10, height=10))


def test_new_confirmation_is_a_transition_even_when_parent_remains():
    before = {'related_windows': [layer(1), layer(2, parent=1, modal=True)], 'dialogs': []}
    after = {'related_windows': [*before['related_windows'], layer(3, parent=2, modal=True)], 'dialogs': []}
    delta = dialogs.transition(before, after)
    assert delta['status'] == 'changed' and [w['window_id'] for w in delta['appeared']] == [3]
    assert delta['closed'] == []
    assert dialogs.transition(after, after)['status'] == 'no_observed_change'
    embedded = {**before, 'dialogs': [{'label': 'Are you sure?', 'controls': ['Yes', 'No']}]}
    assert dialogs.transition(before, embedded)['dialogs_changed']


def test_app_dialog_controls_bypass_sparse_parent_and_ocr(monkeypatch):
    windows = [layer(1)]
    control = dict(label='Yes', role='push button', bounds=dict(x=600,y=400,width=100,height=30), enabled=True)
    popup = dict(label='Confirm discard', bounds=dict(x=150,y=300,width=400,height=150), controls=[control], modal=True)
    monkeypatch.setattr(dialogs, 'worker', lambda mode, *args: {'windows': windows, 'active_window_id': 1} if mode == 'x11' else {'dialogs': [popup]})
    class Client:
        def call(self, *args, **kwargs): raise AssertionError('Do not query blocked parent')
    result = dialogs.observe(Client(), pid=42, window_id=1)
    assert result['controls'][0]['click_arguments']['x'] == 650
    assert result['blocking_dialog']['label'] == 'Confirm discard'
    assert 'visual' not in result


def test_sparse_accessibility_uses_localized_fallback_but_can_be_disabled(monkeypatch):
    windows = [layer(1)]
    monkeypatch.setattr(dialogs, 'worker', lambda mode, *args: {'windows': windows, 'active_window_id': 1} if mode == 'x11' else {'error': 'Timeout'})
    seen = []
    monkeypatch.setattr(dialogs, 'visual_read', lambda client, bounds: seen.append(bounds) or {'controls': [{'source':'ocr','label':'Yes'}]})
    class Client:
        def call(self, *args, **kwargs): return {'elements': []}
    assert dialogs.observe(Client(), window_id=1)['controls'][0]['source'] == 'ocr'
    assert seen == [windows[0]['bounds']]
    assert dialogs.observe(Client(), window_id=1, visual_fallback=False)['controls'] == []
    assert len(seen) == 1


def test_occluded_window_never_gets_screen_click_candidates(monkeypatch):
    windows = [layer(1), layer(2, pid=99, focused=True)]
    monkeypatch.setattr(dialogs, 'worker', lambda mode, *args: {'windows':windows,'active_window_id':2} if mode=='x11' else {'dialogs':[]})
    monkeypatch.setattr(dialogs, 'visual_read', lambda *args: (_ for _ in ()).throw(AssertionError('Would OCR another app')))
    class Client:
        def call(self,*args,**kwargs): return {'elements':[]}
    result=dialogs.observe(Client(),window_id=1)
    assert not result['foreground'] and not result['controls']


def test_window_movement_during_observation_withdraws_stale_coordinates(monkeypatch):
    count=[0]
    def worker(mode,*args):
        if mode in ('ax','surface'):return {'dialogs':[]}
        count[0]+=1
        return {'windows':[layer(1,x=100 if count[0]==1 else 300)],'active_window_id':1}
    monkeypatch.setattr(dialogs,'worker',worker)
    monkeypatch.setattr(dialogs,'visual_read',lambda *args:{'controls':[{'label':'Yes','click_arguments':{'x':150,'y':200}}]})
    class Client:
        def call(self,*args,**kwargs):return {'elements':[]}
    result=dialogs.observe(Client(),window_id=1)
    assert result['status']=='needs_reobserve'
    assert 'click_arguments' not in result['controls'][0]


def test_known_modal_with_missing_accessibility_goes_straight_to_ocr(monkeypatch):
    windows=[layer(1,modal=True,focused=True)]
    monkeypatch.setattr(dialogs,'worker',lambda mode,*args:{'windows':windows,'active_window_id':1} if mode=='x11' else {'dialogs':[]})
    monkeypatch.setattr(dialogs,'visual_read',lambda *args:{'controls':[{'label':'Yes','source':'ocr'}]})
    class Client:
        def call(self,*args,**kwargs):raise AssertionError('Do not enqueue another slow daemon tree read for the empty modal')
    assert dialogs.observe(Client(),window_id=1)['controls'][0]['source']=='ocr'


def test_observe_never_falls_back_to_full_driver_tree(monkeypatch):
    modes=[]
    def worker(mode,*args):
        modes.append(mode)
        return {'windows':[layer(1)],'active_window_id':1} if mode=='x11' else {'dialogs':[], 'controls':[], 'visited':1}
    monkeypatch.setattr(dialogs,'worker',worker)
    class Client:
        def call(self,*args,**kwargs):raise AssertionError('Full driver traversal can stall the application')
    result=dialogs.observe(Client(),window_id=1,visual_fallback=False)
    assert result['controls']==[] and modes==['x11','surface','x11']
