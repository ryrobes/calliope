#!/usr/bin/env python3
"""Small live canaries inside configured Hermes; synthetic data, no agent/chat work.

Makes two billable requests per explicitly selected alias. Credentials stay in
the existing environment. Outputs separate telemetry, never the main run ledger.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.environ['CALLIOPE_LEDGER_DIR'] = str(args.output / 'ledger')
    from openai import OpenAI
    from agent.api_request_hooks import ApiRequestHooksMixin
    from agent.transports.chat_completions import ChatCompletionsTransport
    from gateway.calliope_ledger import observe
    from gateway.calliope_run_context import bind_run_id
    from providers import get_provider_profile

    class Probe(ApiRequestHooksMixin):
        provider = 'custom'
        api_mode = 'chat_completions'

    base = os.environ['RVBBIT_CLOVER_OPENAI_BASE_URL']
    key = os.environ['RVBBIT_CLOVER_KEY']
    probe_id = 'cache-canary-' + uuid.uuid4().hex
    prefix = 'Synthetic cache verification ' + probe_id + '. These are reference records, not tasks.\n'
    prefix += '\n'.join(f'Record {i}: warehouse shelf {i % 37}; expected quality stable; verification sample {(i * 7919) % 104729}.' for i in range(600))
    profile = get_provider_profile('custom')

    def check(model):
        session = probe_id + ':' + model
        records = []
        client = OpenAI(base_url=base, api_key=key, max_retries=0, timeout=args.timeout)
        probe = Probe()
        try:
            for number in range(2):
                request = ChatCompletionsTransport().build_kwargs(
                    model, [{'role': 'system', 'content': prefix}, {'role': 'user', 'content': 'Reply with just OK.'}],
                    base_url=base, session_id=session, cache_scope_id=session, provider_profile=profile,
                    max_tokens=1024, max_tokens_param_fn=lambda n: {'max_tokens': n},
                    request_overrides={'extra_body': {'reasoning': {'effort': 'low'}}},
                )
                request_id = str(number + 1)
                metadata = dict(session_id=session, api_request_id=request_id, model=model, provider='custom', base_url=base)
                started = time.monotonic()
                with bind_run_id(session):
                    observe('pre_api_request', **metadata, request=probe._api_request_payload_for_hook(request))
                    try:
                        response = client.chat.completions.with_raw_response.create(**request)
                        value = response.parse()
                        observe('post_api_request', **metadata,
                                response=probe._api_response_payload_for_hook(value, value.choices[0].message, finish_reason=value.choices[0].finish_reason),
                                usage=probe._usage_summary_for_api_request_hook(value))
                        records.append({'request': number + 1, 'request_id': value.id,
                            'duration_ms': round((time.monotonic() - started) * 1000),
                            'affinity': response.headers.get('x-hutch-cache-affinity'),
                            'upstream_model_version': response.headers.get('x-hutch-model-version'),
                            'usage': value.usage.model_dump() if value.usage else None})
                    except Exception as exc:
                        observe('api_request_error', **metadata, error_type=type(exc).__name__)
                        records.append({'request': number + 1, 'error_type': type(exc).__name__,
                                        'status_code': getattr(exc, 'status_code', None),
                                        'duration_ms': round((time.monotonic() - started) * 1000)})
                        break
        finally:
            client.close()
        return {'model': model, 'requests': records}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(check, dict.fromkeys(args.models)))
    spec = importlib.util.spec_from_file_location('cache_export', '/opt/hermes/calliope/export-run-ledger.py')
    exporter = importlib.util.module_from_spec(spec); spec.loader.exec_module(exporter)
    rows = [json.loads(line) for p in (args.output / 'ledger').glob('*.jsonl') for line in p.read_text().splitlines()]
    report = {'probe_id': probe_id, 'results': results, 'cache': exporter.summarize_cache(rows)}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
