"""Metadata-only media receipts, shared by the server and Hermes image/STT clients."""
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import json
import logging
import os
import uuid
from urllib.parse import urlsplit


def record(*, operation, model, base_url, headers=None, usage=None, latency_ms=None,
           input_bytes=None, output_bytes=None, session_id=None, status='ok', error_type=None):
    try:
        headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        def numeric(value):
            if isinstance(value, (int, float)) and not isinstance(value, bool): return value
            if isinstance(value, dict): return {k: numeric(v) for k,v in value.items() if isinstance(v, (dict,int,float)) and not isinstance(v,bool)}
            return None
        run_id = None
        try:
            from gateway.calliope_run_context import current_run_id
            from gateway.calliope_ledger import _agent
            run_id = current_run_id()
            session_id = session_id or (_agent.get() or {}).get('session_id')
        except ImportError:
            pass
        now = datetime.now(timezone.utc)
        row = {'schema_version':1,'event':'media.completed','event_id':str(uuid.uuid4()),
               'ts':now.isoformat(),'epoch_seconds':now.timestamp(),'source':'media',
               'operation':operation,'model':model,'response_model':headers.get('x-hutch-upstream-model'),
               'response_provider':headers.get('x-hutch-provider'),
               'provider_request_id':headers.get('x-request-id'),
               'hutch_request_id':headers.get('x-hutch-request-id'),
               'usage_scope':headers.get('x-hutch-usage-scope','request'),
               'endpoint_host':urlsplit(base_url).hostname,'usage':numeric(usage or {}),
               'session_id':session_id,'run_id':run_id,'latency_ms':latency_ms,
               'request_json_bytes':input_bytes,'response_json_bytes':output_bytes,
               'status':status,'error_type':error_type}
        root=Path(os.environ.get('CALLIOPE_MEDIA_LEDGER_DIR') or
                  os.environ.get('CALLIOPE_LEDGER_DIR','/opt/data/calliope-ledger'))
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(root/(now.strftime('%Y-%m-%d')+'.jsonl'),os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        with os.fdopen(fd,'a') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            f.write(json.dumps(row,allow_nan=False)+'\n');f.flush();os.fsync(f.fileno())
    except Exception as exc:
        logging.getLogger(__name__).warning('Media receipt unavailable (%s)',type(exc).__name__)
