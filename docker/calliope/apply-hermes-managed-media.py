"""Guarded integration seams for pinned Hermes; no provider keys embedded."""
from pathlib import Path
root=Path('/opt/hermes')
def patch(file,old,new):
 p=root/file;s=p.read_text()
 if s.count(old)!=1:raise SystemExit('Managed media seam changed: '+file)
 p.write_text(s.replace(old,new))
patch('tools/transcription_cloud.py', '    selected = read_selection("stt")\n', '''    selected = read_selection("stt")
    key_env = openai_cfg.get("key_env")
    if key_env:
        from hermes_cli.config import get_env_var
        key = get_env_var(key_env)
        if not key:
            raise ValueError("Configured STT credential is unavailable")
        return key, openai_cfg.get("base_url") or OPENAI_BASE_URL
'''.replace('from hermes_cli.config import get_env_var','from tools.tool_backend_helpers import resolve_provider_secret').replace('get_env_var(key_env)','resolve_provider_secret(key_env, "")'))
patch('agent/aux_accounting.py','        model = str(getattr(response, "model", "") or "") or "unknown"', '''        requested = str(getattr(response, "model", "") or "") or "unknown"
        model = str(getattr(response, "hutch_upstream_model", None) or requested)
        try:
            from dataclasses import asdict
            from gateway.calliope_ledger import observe, measured_payload
            observe('aux.completed', session_id=session_id, model=requested, response_model=model,
                    provider=provider or '', base_url=base_url or '', task_id=task,
                    usage=asdict(usage), response=measured_payload({}, response))
        except Exception:
            logger.debug('Auxiliary request receipt unavailable', exc_info=True)''')
# Native OpenAI image provider uses raw response headers for authoritative route receipts.
patch('plugins/image_gen/openai/__init__.py','            response = call(**request)', '''            import time
            started = time.monotonic()
            raw_call = client.images.with_raw_response.edit if is_edit else client.images.with_raw_response.generate
            raw_response = raw_call(**request)
            response = raw_response.parse()
            from gateway.calliope_media_ledger import record
            if meta['api_model'] == 'calliope-image':
                record(operation='image.edit' if is_edit else 'image.generate', model=meta['api_model'],
                   base_url=base_url, headers=dict(raw_response.headers),
                   usage=response.usage.model_dump() if getattr(response,'usage',None) else {},
                   input_bytes=len(__import__('json').dumps(request).encode()),
                   output_bytes=len(response.model_dump_json().encode()),
                   latency_ms=(time.monotonic()-started)*1000)''')
# A generation-only managed route must not advertise an unimplemented edits endpoint.
patch('plugins/image_gen/openai/__init__.py', '        return {"modalities": ["text", "image"], "max_reference_images": 16}', '''        if _resolve_model()[0] == 'calliope-image':
            return {"modalities": ["text"], "max_reference_images": 0}
        return {"modalities": ["text", "image"], "max_reference_images": 16}''')
patch('tools/transcription_cloud.py','                return client.audio.transcriptions.create(file=audio_file, **create_kwargs)', '''                import time
                started = time.monotonic()
                try:
                    raw_response = client.audio.transcriptions.with_raw_response.create(file=audio_file, **create_kwargs)
                except Exception as exc:
                    from gateway.calliope_media_ledger import record
                    record(operation='stt.transcribe', model=model_name, base_url=str(client.base_url),
                           headers=dict(getattr(getattr(exc,'response',None),'headers',{}) or {}),
                           input_bytes=Path(path).stat().st_size, latency_ms=(time.monotonic()-started)*1000,
                           status='error', error_type=type(exc).__name__)
                    raise
                result = raw_response.parse()
                from gateway.calliope_media_ledger import record
                usage = getattr(result, 'usage', None)
                record(operation='stt.transcribe', model=model_name, base_url=str(client.base_url),
                       headers=dict(raw_response.headers), usage=usage.model_dump() if hasattr(usage,'model_dump') else usage,
                       input_bytes=Path(path).stat().st_size, latency_ms=(time.monotonic()-started)*1000)
                return result''')

# Managed image costs come from the media receipt; importing the native auxiliary
# aggregate too would count the same provider charge twice.
patch('plugins/image_gen/openai/__init__.py',
      '        record_token_usage(getattr(response, "usage", None), model=meta["api_model"], provider="openai")',
      '        if meta["api_model"] != "calliope-image":\n            record_token_usage(getattr(response, "usage", None), model=meta["api_model"], provider="openai")')
patch('plugins/image_gen/openai/__init__.py','    return openai.OpenAI(**kwargs)', '''    if _resolve_model()[0] == 'calliope-image':
        kwargs['max_retries'] = 0
    return openai.OpenAI(**kwargs)''')
patch('tools/transcription_cloud.py','        from openai import APIStatusError\n', '''        from openai import APIStatusError
        if model_name == 'calliope-stt':
            client = client.with_options(max_retries=0)
''')
patch('tools/transcription_cloud.py','            except APIStatusError as exc:\n', '''            except APIStatusError as exc:
                if model_name == 'calliope-stt':
                    raise
''')

patch('plugins/image_gen/openai/__init__.py',
      '            logger.debug("OpenAI image %s failed", verb, exc_info=True)',
      '''            if meta['api_model'] == 'calliope-image':
                from gateway.calliope_media_ledger import record
                record(operation='image.generate', model=meta['api_model'], base_url=base_url,
                       headers=dict(getattr(getattr(exc,'response',None),'headers',{}) or {}),
                       latency_ms=(time.monotonic()-started)*1000, status='error', error_type=type(exc).__name__)
            logger.debug("OpenAI image %s failed", verb, exc_info=True)''')
