import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('trial',Path(__file__).with_name('jev_retrieval_trial.py'))
trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)


def test_pack_enforces_shared_budget_and_preserves_source_order():
    pool=[{'id':f'C{i:02}','chunk':'x'*100} for i in range(6)]
    rows=trial.pack(pool,[5,3,1,0,2,4],limit=3,budget=400)
    assert [r['id'] for r in rows]==['C01','C03','C05']
    assert sum(len(trial.json.dumps(r,ensure_ascii=False)) for r in rows)<=400


def test_missing_decisions_fail_loudly_instead_of_silent_omission():
    with pytest.raises(ValueError,match='Missing/extra'):trial.ranking({'answers':{}},2)


@pytest.mark.parametrize('bad',[float('nan'),1.1,True])
def test_invalid_probabilities_are_rejected(bad):
    answer={'type':'choice','choice':'direct','probabilities':{k:0 for k in trial.CRITERIA}}
    answer['probabilities']['direct']=bad
    with pytest.raises(ValueError):trial.ranking({'answers':{'c0':answer}},1)


def test_qualifications_outrank_background():
    def a(label):return {'type':'choice','choice':label,'probabilities':{k:int(k==label) for k in trial.CRITERIA}}
    assert trial.ranking({'answers':{'c0':a('background'),'c1':a('qualification'),'c2':a('direct')}},3)==[2,1,0]
