"""Regenerate the sketch font and metrics from the UI's canonical font.

Run with Python + fonttools[woff] after changing LT Superior. Outputs are
checked in, so ordinary npm/Docker builds need no Python font tooling.
"""
import hashlib
import json
from pathlib import Path

from fontTools.ttLib import TTFont

service = Path(__file__).resolve().parents[2]
source = service / "theme/fonts/LTSuperior-Regular.otf"
font = TTFont(source, recalcTimestamp=False)
metrics = {
    "family": "Superior", "id": 12,
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "upem": font["head"].unitsPerEm,
    "ascender": font["hhea"].ascent,
    "descender": font["hhea"].descent,
    "lineHeight": 1.25,
    "advances": {chr(code): font["hmtx"][glyph][0]
                 for code, glyph in sorted(font.getBestCmap().items())},
}
(service / "superior_font_metrics.json").write_text(
    json.dumps(metrics, ensure_ascii=False, separators=(",", ":")) + "\n"
)
target = service / "calliope/sketch-assets/fonts/Superior/Superior-Regular.woff2"
target.parent.mkdir(parents=True, exist_ok=True)
font.flavor = "woff2"
font.save(target)
print(f"Prepared Superior ({target.stat().st_size} bytes)")
