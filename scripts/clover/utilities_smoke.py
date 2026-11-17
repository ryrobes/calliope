#!/usr/bin/env python3
"""Test the CPU/web and hosted SQL transcription bridges. Makes paid audio calls."""
import argparse
import base64
import json
from pathlib import Path
import shlex
import subprocess
import time
import urllib.error
import urllib.request


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', type=Path, required=True)
    ap.add_argument('--url', default='http://127.0.0.1:18090')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--psql-command')
    ap.add_argument('--sql-audio-fixture', type=Path, help='Equivalent quick-brown-fox WAV if the legacy hosted fixture is unavailable')
    args = ap.parse_args()
    key = json.loads((args.config/'local-keys.json').read_text())['service_key']
    report = {'checks': {}, 'paid_transcription': True}

    def check(name, action):
        started=time.monotonic()
        try:
            result=action()
            report['checks'][name]={'passed':True,'seconds':round(time.monotonic()-started,3),'result':result}
        except Exception as error:
            report['checks'][name]={'passed':False,'error':str(error).replace(key,'[redacted]')[:400]}
        print(name, 'PASS' if report['checks'][name]['passed'] else 'FAIL',flush=True)

    def predict(route, inputs, status=200):
        req=urllib.request.Request(args.url+'/b/'+route+'/predict',data=json.dumps({'inputs':inputs}).encode(),
              headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=250) as response:
                actual,body=response.status,response.read()
        except urllib.error.HTTPError as error:
            actual,body=error.code,error.read()
        assert actual == status, f'{route}: HTTP {actual}, expected {status}'
        return json.loads(body).get('outputs') if status == 200 else {'status':status}

    def language():
        outputs=predict('language',['bonjour mes amis','guten morgen alle zusammen','buenos dias, como estas hoy','The compressor needs repair','请明天下午来维修空调'])
        assert [o['language'] for o in outputs] == ['fr','de','es','en','zh']
        assert all(o['usage']['cost'] == 0 for o in outputs)
        return outputs

    def web():
        outputs=predict('web_scrape',['https://example.com','http://127.0.0.1/private','file:///etc/passwd'])
        assert outputs[0]['ok'] and outputs[0]['metadata']['title']=='Example Domain'
        assert 'documentation examples' in outputs[0]['markdown']
        assert not outputs[1]['ok'] and not outputs[2]['ok']
        assert outputs[0]['sha256'] and outputs[0]['source']
        assert outputs[0]['usage']['cost'] == 0
        return [{'ok':o['ok'],'source':o.get('source'),'sha256':o.get('sha256'),'error':o.get('error'),'usage':o['usage']} for o in outputs]

    def transcription():
        output=predict('transcribe',[{'audio':'https://raw.githubusercontent.com/ggerganov/whisper.cpp/master/samples/jfk.wav','language':'en'}])[0]
        assert 'ask not' in output['text'].lower()
        assert output['model'] and output['contract']=='clover-transcribe-v1'
        assert 'cost' not in output['usage'] or output['usage']['cost'] >= 0
        return output

    def inline_audio():
        with urllib.request.urlopen('https://raw.githubusercontent.com/ggerganov/whisper.cpp/master/samples/jfk.wav',timeout=30) as response:
            audio=response.read(2_000_000)
        output=predict('transcribe',['data:audio/wav;base64,'+base64.b64encode(audio).decode()])[0]
        assert 'ask not' in output['text'].lower()
        return {'text':output['text'],'model':output['model'],'usage':output['usage']}

    check('language',language)
    check('web_and_public_network_boundary',web)
    check('sql_audio_url',transcription)
    check('sql_audio_data_uri',inline_audio)
    check('invalid_audio_fails',lambda:predict('transcribe',[{'audio':'data:audio/wav;base64,notbase64!'}],502))
    check('private_audio_fails',lambda:predict('transcribe',[{'audio':'http://127.0.0.1/private.wav'}],502))

    if args.psql_command:
        command=shlex.split(args.psql_command)+['-q','-v','ON_ERROR_STOP=1']
        def sql(query):
            run=subprocess.run(command,input=query,text=True,capture_output=True,timeout=300)
            if run.returncode:
                raise RuntimeError('SQL check failed: '+run.stderr[-400:])
            return run.stdout.strip()
        fixtures=json.loads(sql("SELECT jsonb_agg(jsonb_build_object('name',name,'tests',tests)) FROM rvbbit.operators WHERE name IN ('clover_language','clover_language_info','clover_transcribe');"))
        assert len(fixtures)==3
        for op in fixtures:
            for test in op['tests']:
                def run(test=test):
                    query=test['sql']
                    override=None
                    if op['name']=='clover_transcribe' and args.sql_audio_fixture:
                        uri='data:audio/wav;base64,'+base64.b64encode(args.sql_audio_fixture.read_bytes()).decode()
                        query=query.replace('https://rvbbit.ai/data/clover-tests/speech.wav',uri)
                        override='Equivalent quick-brown-fox WAV supplied; stored SQL fixture unchanged'
                    actual=sql("SET rvbbit.cache_bypass='on';\n"+query)
                    wanted,kind=str(test['expect']['value']),test['expect']['type']
                    assert (actual == wanted if kind=='exact' else wanted in actual)
                    return {'actual':actual,'expected':test['expect'],'fixture_override':override}
                check(op['name']+'/'+test['name'],run)
        def web_sql():
            actual=sql("SELECT rvbbit.clover_web_markdown('https://example.com');")
            assert 'documentation examples' in actual
            return {'passed':True}
        check('sql_web_markdown',web_sql)
        def audio_sql():
            actual=sql("SET rvbbit.cache_bypass='on'; SELECT rvbbit.clover_transcribe('https://raw.githubusercontent.com/ggerganov/whisper.cpp/master/samples/jfk.wav');")
            assert 'ask not' in actual.lower()
            return {'text':actual}
        check('sql_public_audio_url',audio_sql)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    raise SystemExit(0 if all(c['passed'] for c in report['checks'].values()) else 1)


if __name__ == '__main__':
    main()
