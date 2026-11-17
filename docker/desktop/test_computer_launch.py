from copy import deepcopy
import pytest
import computer_launch as module


def window(pid, wid, title, app='Thunar'):
    return {'pid':pid,'window_id':wid,'title':title,'app_name':app}


class Client:
    def __init__(self, snapshots, receipt=None):
        self.snapshots=snapshots; self.index=0; self.calls=[]
        self.receipt=receipt or {'pid':99,'running':True,'active':False,'name':'thunar','windows':[]}
    def _call_driver(self,name,args,timeout):
        self.calls.append((name,args))
        if name=='launch_app':return deepcopy(self.receipt)
        value=self.snapshots[min(self.index,len(self.snapshots)-1)];self.index+=1
        if isinstance(value,Exception):raise value
        return {'windows':deepcopy(value)}


@pytest.fixture
def env(monkeypatch):
    clock=[0.0]; processes={328:('old','/usr/bin/thunar'),55:('new','/usr/bin/thunar'),77:('other','/usr/bin/firefox')}
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(module.time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
    monkeypatch.setattr(module,'process_identity',lambda pid:processes.get(pid))
    monkeypatch.setattr(module.shutil,'which',lambda name:None)
    return processes


def run(client):
    return module.launch(client,{'name':'thunar','additional_arguments':['/home/kasm-user/Desktop']},90)


@pytest.mark.parametrize('debug', [False, True])
def test_single_instance_handoff_returns_surviving_owner_and_new_window(env, debug):
    home=window(328,1,'Home - Thunar'); desktop=window(328,2,'Desktop - Thunar')
    c=Client([[home],[home,desktop],[home,desktop]])
    args={'name':'thunar', **({'debug':True} if debug else {})}
    r=module.launch(c,args,90)
    assert r['pid']==328 and r['window_id']==2 and r['windows']==[desktop]
    assert r['launcher_pid']==99
    assert ('launch_receipt' in r) is debug
    if debug: assert r['launch_receipt']['pid']==99
    assert args=={'name':'thunar', **({'debug':True} if debug else {})}
    assert next(a for n,a in c.calls if n=='launch_app')=={'name':'thunar'}
    assert not r['window_resolution']['launcher_running']
    assert r['window_resolution']['status']=='ready' and r['running']
    assert sum(n=='launch_app' for n,a in c.calls)==1


def test_direct_launch_still_requires_two_live_observations(env):
    app=window(55,2,'New window')
    c=Client([[],[app],[app]],{'pid':55,'name':'thunar','windows':[app],'running':True})
    r=run(c)
    assert r['pid']==55 and r['window_id']==2
    assert c.index==3


def test_changed_existing_window_wins_over_other_windows_in_same_app(env):
    a=window(328,1,'Home'); b=window(328,2,'Downloads'); changed=window(328,1,'Desktop')
    r=run(Client([[a,b],[changed,b],[changed,b]]))
    assert r['window_id']==1 and r['window_resolution']['basis']=='changed_app_window'


def test_multiple_existing_windows_are_reported_without_guessing(env):
    apps=[window(328,1,'Home'),window(328,2,'Desktop')]
    r=run(Client([apps,apps,apps]))
    assert r['pid']==328 and r['window_id'] is None and r['windows']==apps
    assert r['window_resolution']['status']=='multiple_windows'


def test_unrelated_new_window_does_not_become_launch_target(env):
    other=window(77,3,'New browser','Firefox')
    r=run(Client([[],[other],[other]]))
    assert r['pid'] is None and r['window_id'] is None and r['windows']==[]
    assert r['running'] is None and r['window_resolution']['status']=='unresolved'


def test_transient_window_is_not_reported_as_surviving(env):
    r=run(Client([[],[window(55,4,'Splash')],[],[]]))
    assert r['window_resolution']['status']=='unresolved'


def test_dead_owner_is_not_reported_even_when_driver_lists_its_window(env):
    dead=window(99,5,'Dead app')
    r=run(Client([[],[dead],[dead]]))
    assert r['pid'] is None and not r['windows']


def test_delayed_window_is_resolved_without_relaunching(env):
    app=window(328,2,'Desktop')
    c=Client([[],[],[],[app],[app]])
    assert run(c)['window_id']==2
    assert sum(n=='launch_app' for n,a in c.calls)==1


def test_failed_post_observation_preserves_dispatch_and_reports_uncertainty(env):
    c=Client([[],RuntimeError('driver unavailable')])
    r=run(c)
    assert r['window_resolution']['status']=='unresolved' and r['launcher_pid']==99
    assert r['window_resolution']['observation_notes']
    assert sum(n=='launch_app' for n,a in c.calls)==1


def test_generic_interpreter_does_not_match_unrelated_python_app(env):
    env[77]=('other','/usr/bin/python3')
    c=Client([[],[window(77,5,'Another script','python3')]],{'pid':99,'name':'python3','windows':[]})
    r=module.launch(c,{'name':'python3','additional_arguments':['app.py']},90)
    assert r['pid'] is None


def test_existing_unique_window_can_be_reused(env):
    app=window(328,2,'Desktop')
    r=run(Client([[app],[app],[app]]))
    assert r['window_id']==2 and r['window_resolution']['basis']=='existing_app_window'


def test_real_process_identity_distinguishes_live_and_nonexistent_process():
    import os
    assert module.process_identity(os.getpid()) is not None
    assert module.process_identity(None) is None
    assert module.process_identity(999999999) is None
