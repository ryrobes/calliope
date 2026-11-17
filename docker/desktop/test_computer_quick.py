from copy import deepcopy
import pytest
from computer_quick import act, ActionNeedsAttention


def state(token='s00000001:0', label='Export CSV'):
    return {'app_name':'Report', 'window_title':'Report', 'window_bounds':{'x':0,'y':0,'width':500,'height':300},
            'elements_complete':False, 'elements':[{'element_token':token,'role':'push button','label':label,
            'enabled':True,'actions':['click'],'frame':{'x':10,'y':20,'w':30,'h':20}}]}


class Client:
    def __init__(self, drift=False, verification='satisfied', after_error=False):
        self.calls=[];self.observations=0;self.drift=drift;self.verification=verification;self.after_error=after_error
    def call(self,name,args):
        self.calls.append((name,deepcopy(args)))
        if name=='get_window_state':
            self.observations+=1
            if self.after_error and self.observations==3:raise RuntimeError('Window closed')
            return state(f's0000000{self.observations}:0', 'Discard' if self.drift and self.observations>1 else 'Export CSV')
        if name=='click':return {'effect':'unverifiable','route':'accessibility'}
        if name=='verify_state':return {'status':self.verification}


EXPECT=[{'element':{'selector':{'label_contains':'Export complete'},'exists':True}}]


def test_quick_choice_refreshes_handle_and_requires_actual_verification():
    c=Client(); requests=[]
    def choose(**kwargs):requests.append(kwargs);return {'selected_id':'target_0'}
    result=act(c,choose,goal='Export report',pid=1,window_id=2,expect=EXPECT)
    assert result['status']=='verified' and result['verified'] is True
    clicks=[args for name,args in c.calls if name=='click']
    assert clicks==[{'pid':1,'window_id':2,'element_token':'s00000002:0'}]
    assert len(requests)==1 and requests[0]['options'][-1]['id']=='reobserve'
    assert requests[0]['provider']=='jev'
    assert requests[0]['screenshot'] is None


@pytest.mark.parametrize('selection',['reobserve','unknown'])
def test_abstention_and_bad_choices_never_dispatch(selection):
    c=Client()
    with pytest.raises(ActionNeedsAttention) as e:
        act(c,lambda **k:{'selected_id':selection},goal='Export',pid=1,window_id=2)
    assert e.value.result['dispatch_attempted'] is False
    assert not any(n=='click' for n,a in c.calls)


def test_changed_target_is_not_clicked_or_retried():
    c=Client(drift=True)
    with pytest.raises(ActionNeedsAttention,match='Controls changed'):
        act(c,lambda **k:{'selected_id':'target_0'},goal='Export',pid=1,window_id=2)
    assert not any(n=='click' for n,a in c.calls)


def test_model_outage_does_not_fall_through_to_an_arbitrary_action():
    c=Client()
    def down(**kwargs):raise RuntimeError('unavailable')
    with pytest.raises(ActionNeedsAttention,match='Chooser unavailable'):
        act(c,down,goal='Export',pid=1,window_id=2)
    assert len(c.calls)==1


@pytest.mark.parametrize('verification',['unknown','unsatisfied'])
def test_transport_success_does_not_mean_task_success(verification):
    c=Client(verification=verification)
    with pytest.raises(ActionNeedsAttention,match='not verified') as e:
        act(c,lambda **k:{'selected_id':'target_0'},goal='Export',pid=1,window_id=2,expect=EXPECT)
    assert e.value.result['dispatch_attempted'] and e.value.result['verified'] is False
    assert sum(n=='click' for n,a in c.calls)==1


def test_without_expectation_reports_only_unverified_action():
    r=act(Client(),lambda **k:{'selected_id':'target_0'},goal='Export',pid=1,window_id=2)
    assert r['status']=='acted_unverified' and r['verified'] is None


def test_verified_closed_window_does_not_require_a_post_action_tree():
    r=act(Client(after_error=True),lambda **k:{'selected_id':'target_0'},goal='Close',pid=1,window_id=2,
          expect=[{'window':{'exists':False}}])
    assert r['verified'] and r['after_observation_error']=='Window closed'


def test_large_candidate_sets_are_not_silently_truncated():
    c=Client()
    original=c.call
    def call(name,args):
        value=original(name,args)
        if name=='get_window_state':value['elements']*=16
        return value
    c.call=call
    with pytest.raises(ActionNeedsAttention,match='Narrow query'):
        act(c,lambda **k:pytest.fail('must not ask chooser'),goal='Export',pid=1,window_id=2)


def test_visual_choice_uses_capture_bytes_and_cua_s1(tmp_path):
    p=tmp_path/'screen.png';p.write_bytes(b'current screenshot')
    c=Client();original=c.call;requests=[]
    def call(name,args):
        value=original(name,args)
        if name=='get_window_state' and args.get('include_screenshot'):value['screenshot_file_path']=str(p)
        return value
    c.call=call
    def choose(**kwargs):requests.append(kwargs);return {'selected_id':'target_0'}
    act(c,choose,goal='Export',pid=1,window_id=2,provider='cua-s1',visual=True,expect=EXPECT)
    assert requests[0]['screenshot']==b'current screenshot'
    assert requests[0]['provider']=='cua-s1'


def test_invalid_predicate_is_rejected_before_model_or_input():
    c=Client(); original=c.call
    def call(name,args):
        if name=='verify_state':raise RuntimeError('Invalid element absence predicate')
        return original(name,args)
    c.call=call
    with pytest.raises(ActionNeedsAttention,match='expectation validation failed') as e:
        act(c,lambda **k:pytest.fail('must not ask chooser'),goal='Export',pid=1,window_id=2,
            expect=[{'element':{'selector':{'label_contains':'Export'},'exists':False}}])
    assert not e.value.result['dispatch_attempted']
    assert not c.calls


def test_pre_action_satisfied_predicate_cannot_substitute_for_after_verification():
    c=Client(verification='unsatisfied'); original=c.call
    def call(name,args):
        if name=='verify_state' and args.get('timeout_ms')==0:return {'status':'satisfied'}
        return original(name,args)
    c.call=call
    with pytest.raises(ActionNeedsAttention,match='not verified') as e:
        act(c,lambda **k:{'selected_id':'target_0'},goal='Export',pid=1,window_id=2,expect=EXPECT)
    assert e.value.result['verified'] is False


def test_malformed_chooser_response_never_dispatches():
    c=Client()
    with pytest.raises(ActionNeedsAttention,match='structured decision'):
        act(c,lambda **k:None,goal='Export',pid=1,window_id=2)
    assert not any(n=='click' for n,a in c.calls)
