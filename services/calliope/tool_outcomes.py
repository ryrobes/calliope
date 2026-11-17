"""Application outcome from protocol envelopes, never from arbitrary result rows."""
import json


def application_failure(result, depth=0):
    if depth > 8:
        return None
    if isinstance(result, str):
        if result.startswith('<untrusted_tool_result'):
            start = result.find('\n{')
            end = result.rfind('}')
            if start >= 0:
                result = result[start + 1:end + 1]
        try:
            return application_failure(json.loads(result), depth + 1)
        except (ValueError, TypeError):
            return None
    if not isinstance(result, dict):
        return None
    if result.get('isError') is True or result.get('success') is False or result.get('status') == 'error' or result.get('error'):
        error = result.get('error')
        code = error.get('code') if isinstance(error, dict) else None
        return str(code or 'tool_application_error')[:128]
    # Desktop submissions and subsequent polls have this job envelope. A
    # successful transport/poll can contain a failed, cancelled or timed-out job.
    if result.get('id') and result.get('run_id') and ('exit_code' in result or 'generation' in result):
        status = result.get('status')
        if status in ('failed', 'cancelled', 'canceled', 'timed_out', 'timeout', 'interrupted'):
            return 'desktop_job_' + status
        code = result.get('exit_code')
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return 'desktop_job_nonzero_exit'
    run = result.get('run')
    if isinstance(run, dict) and run.get('id') and run.get('status') in ('failed', 'error', 'cancelled'):
        return 'workspace_run_' + run['status']
    if 'result' in result:
        return application_failure(result['result'], depth + 1)
    if isinstance(result.get('content'), list):
        for part in result['content']:
            if isinstance(part, dict) and part.get('type') == 'text':
                code = application_failure(part.get('text'), depth + 1)
                if code:
                    return code
    if isinstance(result.get('results'), (dict, list)):
        values = result['results'].values() if isinstance(result['results'], dict) else result['results']
        for value in values:
            # Each batch item is a protocol envelope; its rows remain opaque.
            if application_failure(value, depth + 1):
                return 'partial_batch_failure'
    return None
