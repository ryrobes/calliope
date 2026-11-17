import copy
import pytest
import ui_apps
import ui_composition


def document():
    return {'schema':'calliope.app.v1','title':'Connected work',
        'components':[{'id':'root','component':'CStack','children':['link']},
                      {'id':'link','component':'CRecordLink','label':'Open visit','reference':{'path':'/target'}}],
        'data':{'state':{},'target':{'slug':'visits','record':'visit','id':'HV-2'}}}


def test_record_links_accept_live_bindings_literals_and_unavailable_targets():
    doc=document();ui_apps.validate(doc)
    doc['components'][1]['reference']=doc['data']['target'];ui_apps.validate(doc)
    doc['components'][1]['reference']=None;ui_apps.validate(doc)


@pytest.mark.parametrize('extra',[{'state':{'admin':True}},{'sql':'SELECT * FROM private'},{'url':'https://other.test'}])
def test_record_links_carry_only_identity(extra):
    doc=document();doc['data']['target'].update(extra)
    with pytest.raises(ValueError):ui_apps.validate(doc)


def test_repeated_record_links_keep_independent_destinations():
    doc=document();link=doc['components'].pop()
    doc['components'][0]['children']=[]
    link['reference']={'item':'/target'}
    doc['fragments']={'row':{'root':'link','components':[link]}}
    doc['repeat']={'root':{'path':'/rows','fragment':'row','key':'id'}}
    doc['data']['rows']=[{'id':i,'target':{**doc['data']['target'],'id':i}} for i in ['HV-1','HV-2']]
    ui_apps.validate(doc)
    links=[n for n in ui_composition.expand(doc,doc['data']) if n['component']=='CRecordLink']
    assert [n['reference']['id'] for n in links]==['HV-1','HV-2']
    assert links[0]['id']!=links[1]['id']
