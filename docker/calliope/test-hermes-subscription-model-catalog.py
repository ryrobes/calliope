"""Subscription picker and budget contracts; no inference or credential refresh."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from hermes_cli import codex_models
from agent.model_metadata import (
    _resolve_codex_oauth_context_length_with_source,
    has_codex_context_variant,
    strip_codex_context_variant_suffix,
)
from agent.reasoning_effort import codex_supported_efforts


class SubscriptionCatalogTests(unittest.TestCase):
    def test_newest_catalog_passes_unrecognized_models_without_a_static_allowlist(self):
        response = SimpleNamespace(status_code=200, json=lambda: {'models': [
            {'slug': 'gpt-6.1-sol', 'priority': 1, 'visibility': 'list'},
            {'slug': 'future-model', 'priority': 2, 'visibility': 'list'},
            {'slug': 'internal-model', 'priority': 0, 'visibility': 'hide'},
        ]})
        with patch('httpx.get', return_value=response) as get:
            rows = codex_models._fetch_models_from_api('fixture')
        self.assertEqual(rows, ['gpt-6.1-sol', 'future-model'])
        self.assertIn('client_version=99.0.0', get.call_args.args[0])
        self.assertEqual(get.call_count, 1)

    def test_rejected_or_empty_newest_catalog_falls_back_to_legacy_version(self):
        for response in (SimpleNamespace(status_code=400), SimpleNamespace(status_code=200, json=lambda: {'models': []})):
            legacy = SimpleNamespace(status_code=200, json=lambda: {'models': [{'slug': 'fallback-model'}]})
            with patch('httpx.get', side_effect=[response, legacy]) as get:
                self.assertEqual(codex_models._fetch_models_from_api('fixture'), ['fallback-model'])
            self.assertIn('client_version=0.0.0', get.call_args.args[0])

    def test_context_probe_uses_the_same_newest_catalog(self):
        from agent import model_metadata as meta
        response = SimpleNamespace(status_code=200, json=lambda: {'models': [
            {'slug': 'gpt-6.1-sol', 'context_window': 272_000, 'max_context_window': 872_000},
        ]})
        with patch.object(meta, '_codex_oauth_context_cache', {}), \
             patch.object(meta, '_codex_oauth_max_context_cache', {}), \
             patch('agent.model_metadata.requests.get', return_value=response) as get:
            self.assertEqual(meta._fetch_codex_oauth_context_lengths_with_source('fixture'), ({'gpt-6.1-sol': 272_000}, True))
        self.assertIn('client_version=99.0.0', get.call_args.args[0])

    def test_sol_61_budget_and_effort_do_not_fall_back_to_legacy_models(self):
        self.assertEqual(_resolve_codex_oauth_context_length_with_source('gpt-6.1-sol'), (272_000, 'fallback'))
        self.assertIn('max', codex_supported_efforts('gpt-6.1-sol'))
        self.assertNotIn('none', codex_supported_efforts('gpt-6.1-sol'))

    def test_new_tiers_survive_a_lagging_live_catalog(self):
        rows = codex_models._finalize_codex_models(['gpt-5.6-sol', 'gpt-5.6-luna'])
        for model in ('gpt-6-sol', 'gpt-6-luna'):
            self.assertIn(model, rows)
            self.assertIn(model + '-900k', rows)
        self.assertEqual(rows, codex_models._finalize_codex_models(rows))
        self.assertNotIn('gpt-6-astra', rows)

    def test_offline_fallback_has_new_tiers_without_inventing_astra_entitlement(self):
        with patch.object(codex_models, '_read_default_model', return_value=None), \
             patch.object(codex_models, '_read_cache_models', return_value=['gpt-6-astra']):
            rows = codex_models.get_codex_model_ids()
        self.assertIn('gpt-6-sol', rows)
        self.assertIn('gpt-6-luna', rows)
        self.assertNotIn('gpt-6-astra', rows)

    def test_live_discovery_keeps_future_models_and_hides_internal_models(self):
        rows = codex_models._ranked_slugs([
            {'slug': 'future-model', 'priority': 2, 'visibility': 'list'},
            {'slug': 'gpt-6-astra', 'priority': 1, 'visibility': 'list'},
            {'slug': 'internal-model', 'priority': 0, 'visibility': 'hide'},
        ])
        self.assertEqual(rows, ['gpt-6-astra', 'future-model'])

    def test_context_and_effort_match_the_new_routes(self):
        for base in ('gpt-6-sol', 'gpt-6-luna'):
            for model in (base, base + '-2026-09-22'):
                with self.subTest(model=model):
                    self.assertEqual(_resolve_codex_oauth_context_length_with_source(model), (272_000, 'fallback'))
                    self.assertTrue(has_codex_context_variant(model))
                    variant = model + '-900k'
                    self.assertEqual(_resolve_codex_oauth_context_length_with_source(variant), (900_000, 'fallback'))
                    self.assertEqual(strip_codex_context_variant_suffix(variant), model)
                    self.assertIn('max', codex_supported_efforts(model))
                    self.assertIn('max', codex_supported_efforts('openai/' + variant))
            self.assertFalse(has_codex_context_variant(base + '-pro'))

    def test_live_context_ceiling_still_caps_the_large_variant(self):
        from agent import model_metadata as meta
        for model in ('gpt-6-sol', 'gpt-6-luna'):
            with patch.object(meta, '_fetch_codex_oauth_context_lengths_with_source', return_value=({model: 272_000}, True)), \
                 patch.object(meta, '_codex_oauth_max_context_cache', {meta._codex_oauth_token_fingerprint('fixture'): {model: 872_000}}):
                self.assertEqual(meta._resolve_codex_oauth_context_length_with_source(model + '-900k', 'fixture'), (872_000, 'live'))


if __name__ == '__main__':
    unittest.main()
