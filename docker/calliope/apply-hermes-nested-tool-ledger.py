"""Keep outer hook ownership from suppressing nested tool completion events."""
import os
from pathlib import Path
root=Path(os.environ.get('HERMES_SOURCE_ROOT','/opt/hermes'))
def patch(file,old,new):
    p=root/file;s=p.read_text()
    if s.count(old)!=1:raise SystemExit('Nested tool ledger seam changed: '+file)
    p.write_text(s.replace(old,new))
patch('model_tools.py',
      '_post_tool_call_hook_suppressed: ContextVar[bool] = ContextVar("post_tool_call_hook_suppressed", default=False)',
      '_post_tool_call_hook_suppressed: ContextVar[Optional[str]] = ContextVar("post_tool_call_hook_suppressed", default=None)')
patch('model_tools.py','def suppress_post_tool_call_hook():','def suppress_post_tool_call_hook(tool_call_id=None):')
patch('model_tools.py','    token = _post_tool_call_hook_suppressed.set(True)',
      '    token = _post_tool_call_hook_suppressed.set(tool_call_id)')
patch('model_tools.py','    if _post_tool_call_hook_suppressed.get():\n        return',
      '    if tool_call_id and _post_tool_call_hook_suppressed.get() == tool_call_id:\n        return')
patch('agent/tool_executor.py','with model_tools.suppress_post_tool_call_hook():',
      'with model_tools.suppress_post_tool_call_hook(tool_call_id):')
patch('model_tools.py','    ids = _CallIds(task_id, session_id, tool_call_id, turn_id, api_request_id)\n',
      '''    if not tool_call_id:
        import uuid
        tool_call_id = 'nested-' + uuid.uuid4().hex
    ids = _CallIds(task_id, session_id, tool_call_id, turn_id, api_request_id)
''')
# Cancelled nested tools must close their own receipt and still propagate cancellation.
patch('model_tools.py','    except Exception as e:\n        error_msg = f"Error executing {function_name}: {str(e)}"',
      '''    except BaseException as e:
        if not isinstance(e, Exception):
            _emit(None, duration_ms=_elapsed_ms(start), status="interrupted", error_type=type(e).__name__)
            raise
        error_msg = f"Error executing {function_name}: {str(e)}"''')
