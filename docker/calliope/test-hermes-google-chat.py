"""Run inside the Hermes image, without credentials or network access."""

import os
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


def no_network(*args, **kwargs):
    raise AssertionError("Google Chat dependency/discovery check attempted network I/O")


with tempfile.TemporaryDirectory(prefix="hermes-gchat-check-") as home:
    # Never read a deployed user's config, credentials, or provider auth store.
    with patch.dict(os.environ, {"HERMES_HOME": home, "HOME": home}, clear=True):
        with patch.object(socket.socket, "connect", no_network), patch.object(
            socket, "create_connection", no_network
        ):
            from plugins.platforms.google_chat import adapter

            assert not adapter._google_modules_loaded, "Discovery must remain lazy"
            assert "google.cloud.pubsub_v1" not in sys.modules
            from gateway.config import load_gateway_config

            config = load_gateway_config()
            assert not any(
                platform.value == "google_chat" and item.enabled
                for platform, item in config.platforms.items()
            ), "Installing dependencies must not enable Google Chat"
            assert adapter.check_google_chat_requirements(), "Google Chat dependencies missing"
            from google.cloud import pubsub_v1

            assert callable(pubsub_v1.SubscriberClient.subscribe)

            # Multiplex specialists inherit process-wide environment variables.
            # A configured root subscription must not become one subscriber per
            # specialist. Exercise upstream discovery with real plugin code.
            spec = importlib.util.spec_from_file_location(
                "calliope_specialist_seed",
                Path(__file__).with_name("reconcile-hermes-specialists.py"),
            )
            seed = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(seed)
            Path(home, "config.yaml").write_text(json.dumps(seed._profile_config("smoke")))
            with patch.dict(os.environ, {
                "GOOGLE_CHAT_PROJECT_ID": "fixture-project",
                "GOOGLE_CHAT_SUBSCRIPTION_NAME": "fixture-subscription",
            }):
                assert adapter._check_for_registry(), "Fixture must trigger env auto-enablement"
                specialist = load_gateway_config()
                assert not any(
                    platform.value == "google_chat" and item.enabled
                    for platform, item in specialist.platforms.items()
                ), "A specialist must not consume the root Google Chat subscription"

print("Google Chat: lazy discovery, disabled default, root-only subscription and Pub/Sub transport OK")
