"""Locate the server's shared app contract in a checkout or worker image."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
contract = root / 'app_contract'
if not contract.is_dir():
    contract = root.parents[2] / 'services/calliope'
sys.path.append(str(contract))
from ui_app_preview import snapshot, studio_snapshot, viewport, VIEWPORTS  # noqa: E402
