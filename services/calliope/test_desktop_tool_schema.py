"""Validate the model-facing contract before a desktop submission runs."""
import sys
from pathlib import Path

import pytest
from mcp.server.fastmcp.tools import Tool
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server


def test_desktop_request_id_is_optional_but_advertises_and_validates_uuid():
    tool = Tool.from_function(server._mcp_desktop_exec)
    schema = tool.parameters
    field = schema['properties']['request_id']
    assert field['format'] == 'uuid'
    assert 'Omit' in field['description']
    assert 'request_id' not in schema['required']
    args = {'session_id': '33bcab2e-1a89-49e9-97a4-e11f2f82f3df', 'code': 'print(1)'}
    model = tool.fn_metadata.arg_model
    assert model.model_validate(args).request_id is None
    rid = '1d1ed3b5-25fa-4aab-a137-e0db83370c1d'
    assert model.model_validate({**args, 'request_id': rid}).request_id == rid
    with pytest.raises(ValidationError, match='request_id'):
        model.model_validate({**args, 'request_id': 'health-check-list-windows'})


def test_desktop_contention_is_a_retryable_busy_result(monkeypatch):
    from contextlib import nullcontext
    import desktop_agents
    import desktop_tools
    from desktop_access import DesktopError
    from types import SimpleNamespace
    monkeypatch.setattr(server, '_application_authorization_context', lambda: SimpleNamespace(client_id='test'))
    monkeypatch.setattr(server, '_is_trusted_hermes_transport', lambda *args: True)
    monkeypatch.setattr(desktop_agents, 'resolve', lambda *args: ('owner@example.com', {}))
    monkeypatch.setattr(desktop_agents, 'guard', lambda *args: nullcontext())
    def busy(*args, **kwargs):
        raise DesktopError('DESKTOP_BUSY', 'Cassandra owns this run; wait and retry.', 409)
    monkeypatch.setattr(desktop_tools, 'open_desktop', busy)
    result = server._computer_operation('open_desktop', 'notebook', goal='Check')
    assert result['status'] == 'busy' and result['retryable']
    assert result['error']['code'] == 'DESKTOP_BUSY'
