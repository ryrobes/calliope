"""Build-time regression test for Calliope's Hermes multiplex log routing."""

import logging
import tempfile
from pathlib import Path

import hermes_logging
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def _emit(home: Path, info_marker: str, error_marker: str) -> None:
    token = set_hermes_home_override(home)
    try:
        hermes_logging.setup_logging(hermes_home=home)
        logger = logging.getLogger("agent.calliope_log_routing_test")
        logger.info(info_marker)
        logger.warning(error_marker)
        logging.getLogger("gateway.calliope_log_routing_test").info(info_marker)
    finally:
        reset_hermes_home_override(token)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


with tempfile.TemporaryDirectory(prefix="calliope-hermes-log-test-") as raw_root:
    root = Path(raw_root)
    default = root / "default"
    skeptic = root / "profiles" / "analytical-skeptic"
    scout = root / "profiles" / "integration-scout"

    hermes_logging._reset_queued_handlers()
    hermes_logging._logging_initialized = False
    try:
        default_token = set_hermes_home_override(default)
        try:
            hermes_logging.setup_logging(hermes_home=default, mode="gateway")
            logging.getLogger("agent.calliope_log_routing_test").info("default-info")
        finally:
            reset_hermes_home_override(default_token)

        _emit(skeptic, "skeptic-info", "skeptic-error")
        _emit(scout, "scout-info", "scout-error")
        hermes_logging.flush_log_queue()

        default_agent = _read(default / "logs" / "agent.log")
        skeptic_agent = _read(skeptic / "logs" / "agent.log")
        scout_agent = _read(scout / "logs" / "agent.log")
        skeptic_errors = _read(skeptic / "logs" / "errors.log")
        scout_errors = _read(scout / "logs" / "errors.log")
        # 0.21.4 routes component logs per home too (hermes_logging._adopt_secondary_home),
        # so a profile's gateway lines land in its own gateway.log.
        skeptic_gateway = _read(skeptic / "logs" / "gateway.log")
        scout_gateway = _read(scout / "logs" / "gateway.log")

        assert "default-info" in default_agent
        assert "skeptic-info" not in default_agent
        assert "scout-info" not in default_agent
        assert "skeptic-info" in skeptic_agent and "scout-info" not in skeptic_agent
        assert "scout-info" in scout_agent and "skeptic-info" not in scout_agent
        assert "skeptic-error" in skeptic_errors and "scout-error" not in skeptic_errors
        assert "scout-error" in scout_errors and "skeptic-error" not in scout_errors
        assert "skeptic-info" in skeptic_gateway and "[profile=analytical-skeptic]" in skeptic_gateway
        assert "scout-info" in scout_gateway and "[profile=integration-scout]" in scout_gateway
    finally:
        hermes_logging._reset_queued_handlers()

print("Hermes multiplex profile log routing: ok")
