"""Install the runaway tool-argument guard (calliope_runaway_tool_arguments).

The guard runs where every accepted Chat Completions / Anthropic chunk is
counted, OUTSIDE the best-effort diagnostics block (that block swallows
errors), and the classifier ends the turn on it instead of retrying."""
import os
from pathlib import Path

root = Path(os.environ.get('HERMES_SOURCE_ROOT', '/opt/hermes'))


def patch(file, old, new):
    path = root / file
    source = path.read_text()
    if source.count(old) != 1:
        raise SystemExit('Runaway tool-argument seam changed: ' + file + ': ' + old[:80])
    path.write_text(source.replace(old, new))


patch('agent/chat_completion_helpers.py',
      '        """Stamp liveness for a real chunk; diagnostics are best-effort."""\n',
      '        """Stamp liveness for a real chunk; diagnostics are best-effort."""\n'
      '        # Calliope: a brief-style tool call whose arguments never end is stopped here.\n'
      '        from gateway.calliope_runaway_tool_arguments import observe as _calliope_runaway_guard\n'
      '        _calliope_runaway_guard(diag, chunk)\n')

patch('agent/error_classifier.py',
      '    """Message patterns when no status code settled it; status-less usage\n'
      '    limits get the same disambiguation as 402."""\n',
      '    """Message patterns when no status code settled it; status-less usage\n'
      '    limits get the same disambiguation as 402."""\n'
      '    # Calliope: the model\'s own tool call ran away; a retry re-runs the same prompt.\n'
      '    if c.error_type == "RunawayToolArguments":\n'
      '        return _V_MALFORMED_TOOL_ARGS\n')
