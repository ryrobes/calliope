import json
import media_ledger
import cost_accounting


def test_server_media_uses_own_writable_directory(tmp_path, monkeypatch):
    shared = tmp_path / 'read-only-hermes'
    shared.write_text('cannot create a ledger under this path')
    owned = tmp_path / 'server-media'
    monkeypatch.setenv('CALLIOPE_LEDGER_DIR', str(shared))
    monkeypatch.setenv('CALLIOPE_MEDIA_LEDGER_DIR', str(owned))
    media_ledger.record(operation='tts.emotion_tags', model='calliope-luna',
                        base_url='https://clover.example.com/v1', usage={'cost': .001})
    row = json.loads(next(owned.glob('*.jsonl')).read_text())
    assert row['operation'] == 'tts.emotion_tags' and row['usage']['cost'] == .001
    assert shared.is_file()

def test_media_receipt_retains_cost_not_content(tmp_path,monkeypatch):
    monkeypatch.setenv('CALLIOPE_LEDGER_DIR',str(tmp_path))
    media_ledger.record(operation='stt.transcribe',model='calliope-stt',base_url='https://user:secret@clover.example.com/v1?key=secret',
        headers={'x-hutch-request-id':'hutch-id','x-request-id':'or-id','x-hutch-upstream-model':'openai/gpt-transcribe','Authorization':'secret'},
        usage={'seconds':4,'cost':.002,'transcript':'secret'},input_bytes=2400,session_id='s')
    text=next(tmp_path.glob('*.jsonl')).read_text();assert 'secret' not in text
    row=json.loads(text);assert row['usage']=={'seconds':4,'cost':.002}
    row.update(call_id='media:'+row['event_id'],category='Speech to text')
    prepared=cost_accounting.prepare(row,{})
    assert prepared['cost_usd']==.002 and prepared['model']=='openai/gpt-transcribe'
    assert prepared['tokens_in'] is None
    assert prepared['evidence']['hutch_request_id']=='hutch-id'
