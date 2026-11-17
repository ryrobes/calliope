import computer_health as health
import computer_dialogs


def test_failure_history_survives_jobs_but_resets_after_progress(monkeypatch,tmp_path):
    monkeypatch.setenv('CALLIOPE_COMPUTER_RUN','run-one')
    monkeypatch.setenv('CALLIOPE_COMPUTER_PROGRESS_DIR',str(tmp_path))
    assert health.progress_history(999999,1,'no_observed_change')['consecutive_failures']==1
    assert health.progress_history(999999,2,'observation_unavailable')['consecutive_failures']==2
    assert health.progress_history(999999,2)['consecutive_failures']==0
    assert health.progress_history(999999,2,'no_observed_change')['consecutive_failures']==1
    monkeypatch.setenv('CALLIOPE_COMPUTER_RUN','run-two')
    assert health.progress_history(999999,2,'no_observed_change')['consecutive_failures']==1


def test_unsupported_ping_is_unknown_not_a_hung_application(monkeypatch):
    monkeypatch.setattr(computer_dialogs,'worker',lambda mode,*a: {'status':'unsupported'} if mode=='ping' else {'windows':[{'window_id':1,'pid':42}]})
    r=health.health(pid=42,window_id=1)
    assert r['responsive'] is None and r['status']=='unknown'


def test_health_does_not_ping_wrong_owner(monkeypatch):
    def worker(mode,*a):
        assert mode=='x11'
        return {'windows':[{'window_id':1,'pid':99}]}
    monkeypatch.setattr(computer_dialogs,'worker',worker)
    assert health.health(pid=42,window_id=1)['status']=='target_unavailable'


def test_unwritable_progress_history_preserves_failure(monkeypatch,tmp_path):
    monkeypatch.setenv('CALLIOPE_COMPUTER_RUN','run-one')
    root=tmp_path/'file';root.write_text('not a directory')
    monkeypatch.setenv('CALLIOPE_COMPUTER_PROGRESS_DIR',str(root))
    result=health.progress_history(999999,1,'no_observed_change')
    assert result=={'consecutive_failures':1,'persistence':'unavailable'}
