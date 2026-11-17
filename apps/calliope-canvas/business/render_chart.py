"""Compatibility entry point for the shared Calliope Vega converter."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'local/vega/python'))
sys.path.append(str(Path(__file__).resolve().parents[3] / 'services/calliope'))
sys.path.append(str(Path(__file__).resolve().parent))
from ui_chart_worker import expression, instrument, interactions, render, vega

if __name__ == '__main__':
    import json
    render(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2],
           sys.argv[3] or None if len(sys.argv)>3 else None,
           int(sys.argv[4]) if len(sys.argv)>4 else None,
           int(sys.argv[5]) if len(sys.argv)>5 else None)
