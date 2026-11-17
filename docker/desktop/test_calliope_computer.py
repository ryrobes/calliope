"""Driver refusal envelopes must not become successful execution receipts."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from calliope_computer import Cua


@pytest.mark.parametrize("result", [
    {"status": "refused", "refusal": {"code": "browser_route_unavailable"}},
    {"effect": "refused", "route": "trusted_input"},
    {"isError": True, "content": [{"type": "text", "text": "failed"}]},
    {"code": "invalid_action_target"},
    {"code": "invalid_arguments", "message": "wrong key"},
    {"ok": False, "code": "foreground_partial_unknown", "effect": "partial"},
])
def test_successful_transport_with_refused_action_raises(monkeypatch, result):
    client = Cua()
    client.schemas["browser_click"] = {"properties": {"session": {}}}
    monkeypatch.setattr(client, "_run", lambda *a, **k: result)
    with pytest.raises(RuntimeError):
        client.call("browser_click", {"ref": "p1:1"})


def catalog_client(monkeypatch):
    import copy
    client, calls = Cua(), []
    definitions = [
        {'name':'get_window_state','description':'Observe controls.', 'input_schema':{'type':'object','required':['pid','window_id'], 'properties':{'pid':{'type':'integer'},'window_id':{'type':'integer'},'session':{'type':'string'},'include_screenshot':{'type':'boolean'},'include_accessibility_tree':{'type':'boolean'},'screenshot_out_file':{'type':'string'}}}},
        {'name':'click','description':'Click controls.', 'input_schema':{'properties':{'session':{},'element_token':{'type':'string'}}}},
        {'name':'page','description':'Deprecated page.', 'input_schema':{'properties':{}}},
    ]
    def run(*args, **kwargs):
        calls.append(args)
        if args[0]=='dump-docs':return {'version':'0.28.2','tools':copy.deepcopy(definitions)}
        return {'elements':[], 'elements_complete':False, 'app_name':'Xfce4-terminal'}
    monkeypatch.setattr(client,'_run',run)
    return client,calls


def test_catalog_is_structured_searchable_and_hides_deprecated(monkeypatch):
    client,calls=catalog_client(monkeypatch)
    index=client.tools()
    assert [x['name'] for x in index['start_here']]==['get_window_state','click','browser']
    assert not any(x['name']=='page' for x in client.tools(query='page'))
    assert any(x['name']=='page' for x in client.tools(full=True))
    assert client.tools(group='input')[0]['name']=='click'
    doc=client.describe('get_window_state')
    assert doc['required']==['pid','window_id'] and doc['example']['query']=='Export'
    assert doc['input_schema']['properties']['include_screenshot']['default'] is False
    assert calls==[('dump-docs','--type','mcp')]
    assert 'same visible Chrome' in client.describe('browser')['summary']


def test_call_defaults_to_text_without_mutating_arguments_or_claiming_absence(monkeypatch):
    import json
    client,calls=catalog_client(monkeypatch)
    args={'pid':1,'window_id':2,'query':'Ubuntu'}
    result=client.call('get_window_state',args)
    sent=json.loads(calls[-1][2])
    assert sent['include_screenshot'] is False and sent['session']==client.session
    assert args=={'pid':1,'window_id':2,'query':'Ubuntu'}
    assert result['coverage']['query_result']=='no_exposed_match'
    assert result['coverage']['visible_content_complete'] is None
    assert result['coverage']['elements_complete'] is False
    assert 'scrollback' in result['coverage']['notes'][0]


def test_explicit_capture_and_embedded_images_become_files(monkeypatch,tmp_path):
    import base64,json
    from computer_observation import normalize_result
    monkeypatch.setenv('CALLIOPE_COMPUTER_CAPTURE_DIR',str(tmp_path))
    client,calls=catalog_client(monkeypatch)
    client.call('get_window_state',{'pid':1,'window_id':2,'include_accessibility_tree':False})
    sent=json.loads(calls[-1][2])
    assert sent['include_screenshot'] is True
    assert sent['screenshot_out_file'].startswith(str(tmp_path))
    blob=base64.b64encode(b'image test bytes').decode()
    value=normalize_result({'screenshot_png_b64':blob,'content':[{'type':'image','data':blob,'mimeType':'image/png'}]}, {})
    assert blob not in json.dumps(value)
    assert Path(value['screenshot_file_path']).read_bytes()==b'image test bytes'
    assert Path(value['content'][0]['path']).read_bytes()==b'image test bytes'


@pytest.mark.parametrize('failure',[False,True])
def test_capture_hides_only_our_overlay_and_restores_it_even_on_error(failure):
    from computer_observation import unobstructed_capture
    class Client:
        schemas={'get_agent_cursor_state':{}}
        calls=[]
        def call(self,name,args=None,**kwargs):
            self.calls.append((name,args))
            return {'enabled':True}
    c=Client()
    try:
        with unobstructed_capture(c,True):
            c.calls.append(('capture',None))
            if failure:raise RuntimeError('capture failed')
    except RuntimeError:pass
    assert c.calls==[('get_agent_cursor_state',None),('set_agent_cursor_enabled',{'enabled':False}),
                     ('capture',None),('set_agent_cursor_enabled',{'enabled':True})]


def test_query_counts_and_empty_warning_are_visible_in_the_result_note():
    from computer_observation import normalize_result
    value=normalize_result({'elements':[], 'element_count':111,'total_element_count':111,
                            'returned_element_count':0,'elements_complete':False}, {'query':'harness-feel.txt'})
    assert 'does not prove' in value['_note'][:250]
    assert value['coverage']['returned_rows']==0
    assert value['coverage']['snapshot_controls_before_query']==111
    value=normalize_result({'elements':[{}]*12,'total_element_count':1206,'elements_complete':False}, {'query':'File'})
    assert 'returned 12 rows' in value['summary'] and '1206 controls' in value['summary']
    assert 'not a query failure' in value['_note']
    assert value['elements_complete'] is False


def test_unverified_action_is_explained_without_rewriting_its_evidence():
    from computer_observation import normalize_result
    value=normalize_result({'route':'accessibility','effect':'unverifiable'}, {})
    assert value['effect']=='unverifiable'
    assert 'not a failure verdict' in value['effect_note']
    refused=normalize_result({'effect':'refused'}, {})
    assert 'effect_note' not in refused


@pytest.mark.parametrize('key,canonical',[('Page_Down','pagedown'),('PgDn','pagedown'),('Page_Up','pageup'),('Enter','Return'),('ArrowLeft','Left'),('-', '-')])
def test_key_aliases_are_normalized_once_without_mutating_caller(monkeypatch,key,canonical):
    import json
    client=Cua();client.schemas['press_key']={'properties':{}}
    calls=[]
    monkeypatch.setattr(client,'_run',lambda *a,**kw:calls.append(a) or {'effect':'unverifiable'})
    args={'key':key,'scope':'desktop','modifiers':['Control']}
    client.call('press_key',args)
    sent=json.loads(calls[0][2])
    assert sent=={'key':canonical,'scope':'desktop','modifiers':['ctrl'],'delivery_mode':'foreground'}
    assert args['key']==key and args['modifiers']==['Control'] and len(calls)==1


def test_unknown_key_text_is_a_typed_error_but_observed_text_is_not(monkeypatch):
    from calliope_computer import DriverError
    client=Cua();client.schemas.update({'press_key':{'properties':{}},'get_window_state':{'properties':{}}})
    monkeypatch.setattr(client,'_run',lambda *a,**kw:'Unknown key: SomethingUnsupported\n')
    with pytest.raises(DriverError) as exc:client.call('press_key',{'key':'SomethingUnsupported'})
    assert exc.value.result['code']=='invalid_key'
    assert client.call('get_window_state',{})=='Unknown key: SomethingUnsupported\n'


def test_raw_tree_limits_are_visible_and_explicit_overrides_survive(monkeypatch):
    import json
    client,calls=catalog_client(monkeypatch)
    client._catalog()
    props=client.schemas['get_window_state']['properties']
    props.update(max_depth={'type':'integer'}, max_elements={'type':'integer'})
    result=client.call('get_window_state',{'pid':1,'window_id':2})
    assert result['coverage']['requested_limits']=={'max_depth':5,'max_elements':256}
    assert json.loads(calls[-1][2])['max_depth']==5
    assert client.describe('get_window_state')['input_schema']['properties']['max_depth']['default']==5
    client.call('get_window_state',{'pid':1,'window_id':2,'max_depth':3,'max_elements':50})
    assert json.loads(calls[-1][2])['max_depth']==3
    assert json.loads(calls[-1][2])['max_elements']==50
