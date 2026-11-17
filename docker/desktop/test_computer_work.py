from copy import deepcopy
import pytest
import computer_work as work


@pytest.fixture(autouse=True)
def healthy_application(monkeypatch):
    monkeypatch.setattr(work, 'health', lambda **_: {'status': 'responsive', 'responsive': True})


def observation(label='Keep Excel format', *, question='Keep format?', wid=2, modal=True, x=100, enabled=True, source='atspi_dialog'):
    control = {'label': label, 'role': 'push button', 'actions': ['click'], 'enabled': enabled,
               'source': source, 'region': 'dialog', 'bounds': dict(x=x,y=200,width=80,height=25),
               'click_arguments': dict(scope='desktop', x=x+40, y=212.5, delivery_mode='foreground')}
    return {'status':'observed', 'foreground':True, 'target':{'window_id':wid, 'owner_pid':9,
        'title':'sample.xlsx - LibreOffice Calc', 'bounds':dict(x=0,y=0,width=800,height=600)},
        'blocking_dialog': {'label':question,'text':[question]} if modal else None,
        'controls':[control], 'warnings':[]}


class Client:
    def __init__(self, verify_status='unsatisfied'):
        self.calls=[]
        self.verify_status=verify_status
    def call(self, name, args, **kwargs):
        self.calls.append((name,deepcopy(args)))
        return {'status':self.verify_status} if name=='verify_state' else {'effect':'unverifiable'}


def sequence(monkeypatch, rows):
    it=iter(rows)
    last=rows[-1]
    def observe(*args,**kwargs):
        nonlocal last
        last=next(it,last)
        return deepcopy(last)
    monkeypatch.setattr(work,'observe',observe)
    monkeypatch.setattr(work.time,'sleep',lambda *_:None)


def chooser(**kw):
    return {'selected_id':kw['options'][0]['id']}


def test_map_prefers_dialog_and_never_promotes_ocr_text_to_a_button(monkeypatch):
    sequence(monkeypatch,[observation(source='ocr')])
    m=work.screen_map(Client(),pid=9,window_id=1)
    assert m['groups']['active_dialog'][0]['label']=='Keep Excel format'
    assert not m['actions']
    sequence(monkeypatch,[observation(enabled=False)])
    assert not work.screen_map(Client())['actions']


def test_calc_commands_only_in_foreground_unblocked_document(monkeypatch):
    o=observation(modal=False)
    sequence(monkeypatch,[o])
    assert len([a for a in work.screen_map(Client())['actions'] if a['kind']=='shortcut'])==3
    sequence(monkeypatch,[{**o,'foreground':False,'controls':[]}])
    assert not work.screen_map(Client())['actions']
    sequence(monkeypatch,[observation()])
    assert all(a['kind']=='click' for a in work.screen_map(Client())['actions'])


def test_nested_dialog_chain_is_progress_not_task_success(monkeypatch):
    a=observation('Save',question='Save document?')
    b=observation('Keep Excel format',question='Use Excel or ODF?',wid=3)
    c=observation('File',modal=False,wid=1)
    sequence(monkeypatch,[a,a,b,b,c])
    client=Client()
    result=work.work(client,chooser,goal='Save workbook keeping Excel format',pid=9,window_id=1)
    assert result['status']=='progress' and not result['verified']
    assert [s['label'] for s in result['steps']]==['Save','Keep Excel format']
    assert result['screen']['target']['window_id']==1
    assert len(client.calls)==2


def test_exact_unique_label_skips_model_but_not_freshness_check(monkeypatch):
    a=observation();b=observation(modal=False)
    sequence(monkeypatch,[a,a,b])
    result=work.work(Client(),lambda **_:pytest.fail('no model required'),goal='Keep Excel format',pid=9,window_id=1)
    assert result['status']=='progress'
    assert result['decisions']==[{'route':'exact_label','selected_id':'a0'}]


@pytest.mark.parametrize('change',['question','bounds','focus'])
def test_drift_in_question_geometry_or_focus_never_sends_input(monkeypatch,change):
    a=observation()
    b=observation(question='Delete the original?' if change=='question' else 'Keep format?',x=150 if change=='bounds' else 100)
    if change=='focus':b['foreground']=False
    sequence(monkeypatch,[a,b])
    client=Client()
    r=work.work(client,chooser,goal='finish saving',pid=9,window_id=1)
    assert r['status']=='needs_attention' and not client.calls and not r['steps']


def test_no_change_never_retries_input(monkeypatch):
    sequence(monkeypatch,[observation()])
    client=Client()
    r=work.work(client,chooser,goal='finish saving',pid=9,window_id=1)
    assert 'No observed change' in r['reason'] and len(client.calls)==1


@pytest.mark.parametrize('already_focused',[False,True])
def test_editable_field_hands_back_for_typing_without_repeated_focus(monkeypatch,already_focused):
    a=observation('Name:')
    a['controls'][0].update(role='text',actions=[],interaction='focus',editable=True,
                            focused=already_focused,value='original.xlsx')
    b=deepcopy(a);b['controls'][0]['focused']=True
    sequence(monkeypatch,[a,a,b])
    client=Client()
    r=work.work(client,chooser,goal='Enter the filename',pid=9,window_id=1)
    assert r['status']=='progress' and r['handoff']=='caller_input' and not r['verified']
    assert r['field']['focused'] and r['field']['value']=='original.xlsx'
    assert len(client.calls)==(0 if already_focused else 1)
    assert all(name!='type_text' for name,args in client.calls)


def test_hierarchy_covers_all_candidates_and_keeps_each_decision_small(monkeypatch):
    o=observation(modal=False)
    base=o['controls'][0]
    o['controls']=[{**base,'label':f'Button {i}','bounds':dict(x=i*2,y=0,width=2,height=2),
                    'region':f'toolbar {i//20}'} for i in range(100)]
    sequence(monkeypatch,[o])
    scene=work.screen_map(Client());requests=[]
    def choose(**kw):
        requests.append(kw)
        option=next(o for o in kw['options'] if 'Button 99' in o['label'])
        return {'selected_id':option['id']}
    selected,trace=work.select(choose,'press button 99',scene,[],'jev')
    assert selected['label']=='Button 99' and len(trace)==2
    assert all(len(r['options'])<=13 for r in requests)
    assert all(r['options'][-1]['id']=='return_to_callie' for r in requests)


@pytest.mark.parametrize('selected',['return_to_callie','made_up'])
def test_model_abstention_or_invalid_output_does_not_click(monkeypatch,selected):
    sequence(monkeypatch,[observation()]);client=Client()
    r=work.work(client,lambda **_:{'selected_id':selected},goal='finish',pid=9,window_id=1)
    assert r['status']=='needs_attention' and not client.calls


def test_explicit_outcome_is_required_for_verified_and_checked_after_input(monkeypatch):
    sequence(monkeypatch,[observation()]);client=Client()
    original=client.call
    def call(name,args,**kw):
        if name=='verify_state' and any(n=='click' for n,a in client.calls):client.verify_status='satisfied'
        return original(name,args,**kw)
    client.call=call
    r=work.work(client,chooser,goal='finish',pid=9,window_id=1,expect=[{'window':{'exists':True}}])
    assert r['status']=='verified' and r['verified'] and r['screen'] is None
    assert [n for n,a in client.calls]==['verify_state','click','verify_state']


def test_lost_action_response_is_recorded_and_not_retried(monkeypatch):
    sequence(monkeypatch,[observation()]);client=Client()
    def call(*a,**kw):raise RuntimeError('lost response')
    client.call=call
    r=work.work(client,chooser,goal='finish',pid=9,window_id=1)
    assert r['status']=='needs_attention' and len(r['steps'])==1 and r['steps'][0]['dispatch_attempted']


def test_bad_verification_contract_stops_before_observation(monkeypatch):
    monkeypatch.setattr(work,'observe',lambda *a,**k:pytest.fail('no observation'))
    client=Client()
    client.call=lambda *a,**k: (_ for _ in ()).throw(ValueError('invalid predicate'))
    r=work.work(client,chooser,goal='finish',pid=9,window_id=1,expect=[{}])
    assert r['status']=='needs_attention' and not r['steps']


def test_missing_tree_is_not_reported_as_screen_drift(monkeypatch):
    a=observation();missing={**a,'controls':[], 'observation':{'status':'unavailable'}}
    sequence(monkeypatch,[a,missing,missing]);client=Client()
    r=work.work(client,chooser,goal='finish',pid=9,window_id=1)
    assert r['code']=='observation_unavailable' and not client.calls
    assert r['health']['responsive'] is True
    assert 'not evidence the screen changed' in r['reason']


def test_transient_bridge_failure_recovers_without_repeating_choice_or_input(monkeypatch):
    a=observation();missing={**a,'controls':[], 'observation':{'status':'unavailable'}}
    sequence(monkeypatch,[a,missing,a,observation(modal=False)]);client=Client()
    r=work.work(client,chooser,goal='finish',pid=9,window_id=1)
    assert r['status']=='progress' and len(client.calls)==1 and len(r['decisions'])==1


def test_no_effect_with_failed_ping_reports_app_blocker(monkeypatch):
    sequence(monkeypatch,[observation()]);client=Client()
    monkeypatch.setattr(work,'health',lambda **_:{'status':'unresponsive','responsive':False})
    r=work.work(client,chooser,goal='finish',pid=9,window_id=1)
    assert r['code']=='application_unresponsive' and len(client.calls)==1
    assert 'Do not debug X11' in r['next']


def test_failed_input_keeps_driver_code_and_does_not_reobserve_or_retry(monkeypatch):
    from calliope_computer import DriverError
    sequence(monkeypatch,[observation()]);client=Client()
    def fail(name,args,**kw):
        client.calls.append((name,args));raise DriverError(name,{'code':'invalid_action_target'})
    client.call=fail
    r=work.work(client,chooser,goal='finish',pid=9,window_id=1)
    assert r['code']=='invalid_action_target' and len(client.calls)==1 and r['screen'] is None
    assert r['steps'][0]['error']['code']=='invalid_action_target'


def test_navigation_then_chooser_handoff_is_progress_not_failure(monkeypatch):
    a=observation('File',modal=False);b=observation('Save',question='Save as')
    sequence(monkeypatch,[a,a,b]);calls=[]
    def choose(**kw):
        calls.append(kw)
        return {'selected_id': 'a0' if len(calls)==1 else 'return_to_callie'}
    r=work.work(Client(),choose,goal='Open Save As for filename entry',pid=9,window_id=1)
    assert r['status']=='progress' and r['handoff']=='caller_input_or_verification'
    assert len(r['steps'])==1 and not r['verified'] and r['code'] is None


def test_action_budget_reports_observed_progress(monkeypatch):
    a=observation('File',modal=False);b=observation('Save',question='Save as')
    sequence(monkeypatch,[a,a,b])
    r=work.work(Client(),chooser,goal='Open file menu',pid=9,window_id=1,max_actions=1)
    assert r['status']=='progress' and r['handoff']=='action_budget' and not r['verified']


def test_selectable_tab_is_actionable_and_field_state_reaches_caller(monkeypatch):
    o=observation('Font')
    o['controls'][0].update(role='page tab',actions=[],interaction='select',selected=True)
    sequence(monkeypatch,[o])
    scene=work.screen_map(Client());compact=work.compact_map(scene)
    assert compact['actions'][0]['label']=='Font' and compact['actions'][0]['state']['selected']
    assert 'currently selected' in work.choice_label(scene['actions'][0])
