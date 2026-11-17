"""Accessibility budget regressions observed in actual LibreOffice."""
import sys
from types import SimpleNamespace
from computer_layers import accessible_dialogs


class Node:
    def __init__(self, role, name='', children=(), showing=True):
        self.role,self.name,self.children,self.showing=role,name,list(children),showing
        self.childCount=len(self.children)
    def __getitem__(self,i):return self.children[i]
    def get_process_id(self):return 42
    def getRoleName(self):return self.role
    def getState(self):return SimpleNamespace(contains=lambda flag:self.showing if flag=='showing' else flag=='enabled')
    def queryComponent(self):return SimpleNamespace(getExtents=lambda _:SimpleNamespace(x=10,y=10,width=100,height=30))
    def queryAction(self):return SimpleNamespace(nActions=1,getName=lambda i:'click')


def bridge(monkeypatch, children):
    app=Node('application',children=children)
    ax=SimpleNamespace(Registry=SimpleNamespace(getDesktop=lambda _:Node('desktop',children=[app])),
        DESKTOP_COORDS=0,STATE_SHOWING='showing',STATE_MODAL='modal',STATE_FOCUSED='focused',
        STATE_ENABLED='enabled',STATE_SELECTED='selected',STATE_CHECKED='checked',
        STATE_SELECTABLE='selectable',STATE_FOCUSABLE='focusable',STATE_EDITABLE='editable',RELATION_LABELLED_BY='labelled_by')
    monkeypatch.setitem(sys.modules,'pyatspi',ax)


def test_sibling_dialog_precedes_dense_document_and_hidden_menus_do_not_exhaust_budget(monkeypatch):
    menus=[Node('menu',str(i),[Node('menu item',showing=False) for _ in range(50)]) for i in range(11)]
    toolbar=Node('tool bar','Formatting',[Node('push button','Bold')])
    document=Node('frame','Calc',[*menus,toolbar])
    dialog=Node('dialog','Keep format?',[Node('push button','Yes')])
    bridge(monkeypatch,[document,dialog])
    result=accessible_dialogs(42,surface=True)
    assert result['dialogs'][0]['controls'][-1]['label']=='Yes'
    assert not result['controls']  # Do not touch the blocked document.
    bridge(monkeypatch,[document])
    result=accessible_dialogs(42,surface=True)
    assert any(c['label']=='Bold' for c in result['controls'])
    assert result['visited']<256 and result['examined']>256


def test_virtual_spreadsheet_cells_are_never_walked(monkeypatch):
    class Spreadsheet(Node):
        def __getitem__(self,i):raise AssertionError('must not enumerate cells')
    sheet=Spreadsheet('document spreadsheet','Sheet1');sheet.childCount=1000000
    bridge(monkeypatch,[Node('frame','Calc',[sheet])])
    result=accessible_dialogs(42,surface=True)
    assert result['regions'][0]['role']=='document spreadsheet'
    assert result['examined']==2


def test_dead_application_bridge_is_explicit_and_dbus_calls_are_bounded(monkeypatch):
    calls=[]
    monkeypatch.setitem(sys.modules,'gi.repository',SimpleNamespace(Atspi=SimpleNamespace(set_timeout=lambda *a:calls.append(a))))
    app=Node('application');app.childCount=-1
    bridge(monkeypatch,[])
    sys.modules['pyatspi'].Registry=SimpleNamespace(getDesktop=lambda _:Node('desktop',children=[app]))
    result=accessible_dialogs(42,surface=True)
    assert result['error']=='application_bridge_unavailable'
    assert calls==[(600,600)]


def test_tabs_and_labelled_fields_survive_without_action_interface(monkeypatch):
    class Component(Node):
        description=''
        def queryAction(self):raise NotImplementedError()
        def getState(self):return SimpleNamespace(contains=lambda f:f in ('showing','enabled','selectable','focusable','editable','focused'))
        def getRelationSet(self):
            return [SimpleNamespace(getRelationType=lambda:'labelled_by',getNTargets=lambda:1,getTarget=lambda i:Node('label','Name'))]
        def queryText(self):return SimpleNamespace(characterCount=9,getText=lambda a,b:'copy.xlsx')
    bridge(monkeypatch,[Node('dialog','Save',[Component('page tab','Options'),Component('text')])])
    tab,field=accessible_dialogs(42,surface=True)['dialogs'][0]['controls'][-2:]
    assert tab['actions']==[] and tab['interaction']=='select' and tab['label']=='Options'
    assert field['label']=='Name' and field['label_source']=='labelled_by'
    assert field['value']=='copy.xlsx' and field['editable'] and field['focused']


def test_noninteractive_labels_are_not_promoted_and_password_values_are_not_read(monkeypatch):
    class Plain(Node):
        def queryAction(self):raise NotImplementedError()
    class Password(Plain):
        def getState(self):return SimpleNamespace(contains=lambda f:f in ('showing','enabled','editable'))
        def queryText(self):raise AssertionError('never read password values')
    bridge(monkeypatch,[Node('dialog','Sign in',[Plain('label','Help'),Password('password text','Password')])])
    controls=accessible_dialogs(42,surface=True)['dialogs'][0]['controls']
    assert not any(c['label']=='Help' for c in controls)
    password=next(c for c in controls if c['label']=='Password')
    assert 'value' not in password and password['interaction']=='focus'
