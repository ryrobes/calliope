"""Real registry regression: a parked shared MCP connection must revive its adopters.

Run inside the pinned Hermes image. No network or model requests are made.
"""
import json
import logging
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

logging.disable(logging.CRITICAL)
import model_tools
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from tools.registry import registry
from tools import mcp_tool as core, mcp_tool_registration as registration
from tools.mcp_tool_server_run import MCPServerRunMixin


@contextmanager
def home(path):
    token = set_hermes_home_override(str(path))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


class SharedRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = Path(self.tmp.name) / 'owner'
        self.child = Path(self.tmp.name) / 'cassandra'
        self.other = Path(self.tmp.name) / 'iris'
        for path in (self.owner, self.child, self.other):
            path.mkdir()
        self.config = {'url': 'http://fixture/mcp', 'headers': {'Authorization': 'Bearer fixture'},
                       'tools': {'resources': False, 'prompts': False}}
        self.configs = {str(p): {'Specialist': dict(self.config)} for p in (self.owner, self.child, self.other)}
        for target, value in [
            ('tools.mcp_tool._mcp_registry_scope', lambda: registry.current_scope_key()),
            ('tools.mcp_tool_config._load_mcp_config', lambda: self.configs[registry.current_scope_key()]),
            ('tools.mcp_tool_registration._write_schema_cache', lambda *a: None),
        ]:
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        self.server = SimpleNamespace(name='Specialist', session=object(), _config=self.config,
            tool_timeout=30, _registered_tool_names=[], _is_recycled_stdio=lambda: False,
            _tools=[SimpleNamespace(name=n, description='Fixture '+n,
                     input_schema={'type': 'object', 'properties': {}}, annotations=None)
                    for n in ('run_sql', 'open_desktop', 'desktop_exec')])
        self.key = (str(self.owner), 'Specialist')
        core._servers[self.key] = self.server
        core._server_scope_keys[self.key] = str(self.owner)
        self.addCleanup(self.cleanup_registry)
        with home(self.owner):
            self.server._registered_tool_names = registration._register_server_tools('Specialist', self.server, self.config)
        for path in (self.child, self.other):
            with home(path):
                registration.register_connected_into_current_scope(self.configs[str(path)])
                self.assertIn('mcp__Specialist__open_desktop', self.names())

    def cleanup_registry(self):
        with home(self.owner):
            MCPServerRunMixin._deregister_tools(self.server)
        core._servers.pop(self.key, None)
        core._server_scope_keys.pop(self.key, None)
        core._server_tool_scopes.pop(self.key, None)
        model_tools._clear_tool_defs_cache()

    def names(self):
        return registry.get_tool_names_for_toolset('mcp-Specialist')

    def park(self):
        with home(self.owner):
            MCPServerRunMixin._deregister_tools(self.server)
        self.server.session = None
        for path in (self.owner, self.child, self.other):
            with home(path):
                self.assertEqual(self.names(), [])

    def revive(self):
        self.server.session = object()
        with home(self.owner):
            self.server._registered_tool_names = registration._register_server_tools('Specialist', self.server, self.config)

    def test_revival_restores_search_describe_and_deferred_dispatch_scope(self):
        self.park()
        self.revive()
        from tools.tool_search import scoped_deferrable_names
        for path in (self.owner, self.child, self.other):
            with home(path):
                self.assertIn('mcp__Specialist__open_desktop', self.names())
                defs = model_tools.get_tool_definitions(enabled_toolsets=['Specialist'], quiet_mode=True,
                                                       skip_tool_search_assembly=True)
                self.assertIn('mcp__Specialist__desktop_exec', scoped_deferrable_names(defs))
                result = json.loads(model_tools.handle_function_call('tool_search',
                    {'queries': ['open_desktop']}, enabled_toolsets=['Specialist']))
                self.assertIn('mcp__Specialist__open_desktop', result['tools'])
                result = json.loads(model_tools.handle_function_call('tool_describe',
                    {'names': ['mcp__Specialist__open_desktop']}, enabled_toolsets=['Specialist']))
                self.assertNotIn('not_found', result)

    def test_owner_catalog_survives_transient_reconnect(self):
        from tools.mcp_tool_discovery import reconcile_mcp_servers_with_config
        # A Calliope restart briefly clears the MCP session without parking.
        # Reconciliation must not forget the owner's tool registrations.
        self.server.session = None
        with home(self.owner):
            reconcile_mcp_servers_with_config()
        self.server.session = object()
        with home(self.owner):
            reconcile_mcp_servers_with_config()
            self.assertIn('mcp__Specialist__run_sql', self.names())

    def test_live_owner_missing_catalog_is_repaired(self):
        from tools.mcp_tool_discovery import reconcile_mcp_servers_with_config
        with home(self.owner):
            for name in tuple(self.names()):
                registry.deregister(name, scope=str(self.owner))
            reconcile_mcp_servers_with_config()
            self.assertIn('mcp__Specialist__run_sql', self.names())

    def test_partial_owner_catalog_is_repaired(self):
        from tools.mcp_tool_discovery import reconcile_mcp_servers_with_config
        with home(self.owner):
            registry.deregister('mcp__Specialist__run_sql', scope=str(self.owner))
            reconcile_mcp_servers_with_config()
            self.assertIn('mcp__Specialist__run_sql', self.names())

    def test_changed_owner_credentials_do_not_reuse_live_connection(self):
        from tools.mcp_tool_discovery import reconcile_mcp_servers_with_config
        self.configs[str(self.owner)]['Specialist']['headers'] = {'Authorization': 'Bearer changed'}
        with home(self.owner):
            reconcile_mcp_servers_with_config()
            self.assertEqual(self.names(), [])

    def test_disabled_adopter_stays_disabled(self):
        self.park()
        self.configs[str(self.child)]['Specialist']['enabled'] = False
        self.revive()
        with home(self.child):
            self.assertEqual(self.names(), [])
        with home(self.other):
            self.assertIn('mcp__Specialist__run_sql', self.names())

    def test_changed_credentials_cannot_reborrow_connection(self):
        self.park()
        self.configs[str(self.child)]['Specialist']['headers'] = {'Authorization': 'Bearer different'}
        self.revive()
        with home(self.child):
            self.assertEqual(self.names(), [])
        with home(self.other):
            self.assertIn('mcp__Specialist__run_sql', self.names())

    def test_removed_adopter_stays_removed(self):
        self.park()
        self.configs[str(self.child)] = {}
        self.revive()
        with home(self.child):
            self.assertEqual(self.names(), [])
        with home(self.other):
            self.assertIn('mcp__Specialist__run_sql', self.names())

    def test_repeated_recovery(self):
        for _ in range(3):
            self.park()
            self.revive()
            for path in (self.child, self.other):
                with home(path):
                    self.assertIn('mcp__Specialist__open_desktop', self.names())

    def test_one_broken_profile_does_not_block_other_adopters(self):
        self.park()
        def load():
            scope = registry.current_scope_key()
            if scope == str(self.child):
                raise OSError('fixture: temporarily unreadable config')
            return self.configs[scope]
        with patch('tools.mcp_tool_config._load_mcp_config', load):
            self.revive()
        with home(self.other):
            self.assertIn('mcp__Specialist__open_desktop', self.names())
        # Normal reconciliation heals the missed overlay even though its
        # adopted connection is already live and no configuration changed.
        from tools.mcp_tool_discovery import reconcile_mcp_servers_with_config
        with home(self.child), patch('tools.mcp_tool_discovery.discover_mcp_tools') as discover:
            reconcile_mcp_servers_with_config()
            discover.assert_not_called()
            self.assertIn('mcp__Specialist__open_desktop', self.names())

    def test_toolset_restrictions_still_apply_after_recovery(self):
        self.park()
        self.revive()
        with home(self.child):
            result = json.loads(model_tools.handle_function_call('tool_describe',
                {'names': ['mcp__Specialist__open_desktop']}, enabled_toolsets=['todo']))
            self.assertIn('mcp__Specialist__open_desktop', result['not_found'])


if __name__ == '__main__':
    unittest.main()
