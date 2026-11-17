"""Compile inline browser JavaScript without executing it or fetching dependencies."""
from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess


class _Scripts(HTMLParser):
    # Treat browser raw-text/RCDATA contents as text, not executable script tags.
    CDATA_CONTENT_ELEMENTS = (
        "script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "noscript",
    )

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts = []
        self.current = None
        self.template_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "template":
            self.template_depth += 1
        if tag != "script":
            return
        attrs = dict(attrs)
        kind = (attrs.get("type") or "").strip().lower()
        classic = not kind or kind in {
            "text/javascript", "application/javascript", "text/ecmascript", "application/ecmascript",
            "application/x-javascript", "application/x-ecmascript", "text/jscript", "text/livescript",
            "text/x-javascript", "text/x-ecmascript",
            *(f"text/javascript1.{n}" for n in range(6)),
        }
        if self.template_depth or "src" in attrs or "nomodule" in attrs or not (classic or kind == "module"):
            return
        self.current = {
            "script": len(self.scripts) + 1,
            "kind": "module" if kind == "module" else "script",
            "start_line": self.getpos()[0] + self.get_starttag_text().count("\n"),
            "source": "",
        }

    def handle_data(self, data):
        if self.current is not None:
            self.current["source"] += data

    def handle_endtag(self, tag):
        if tag == "template":
            self.template_depth = max(0, self.template_depth - 1)
        if tag == "script" and self.current is not None:
            self.scripts.append(self.current)
            self.current = None


_COMPILER = r"""
const fs = require('node:fs'), vm = require('node:vm');
const scripts = JSON.parse(fs.readFileSync(0, 'utf8'));
const errors = [];
for (const item of scripts) {
  try {
    const options = {filename:'artifact.html', identifier:'artifact.html', lineOffset:item.start_line-1};
    // Construction only: never runInContext, link, evaluate, or import.
    if (item.kind === 'module') new vm.SourceTextModule(item.source, options);
    else new vm.Script(item.source, options);
  } catch (error) {
    if (error.name !== 'SyntaxError') throw error;
    const match = String(error.stack || '').match(/artifact\.html:(\d+)/);
    const line = match ? Number(match[1]) : null;
    const lines = item.source.split('\n');
    const index = line === null ? 0 : Math.max(0, line-item.start_line);
    errors.push({script:item.script, kind:item.kind, script_start_line:item.start_line,
      line, message:String(error.message).slice(0,500),
      excerpt:lines[index].slice(0,400) + (lines[index+1] ? '\n'+lines[index+1].slice(0,200) : '')});
    if (errors.length >= 8) break;
  }
}
process.stdout.write(JSON.stringify(errors));
"""


def _node_executable():
    # Playwright already ships Node in every server image. No separate JS runtime
    # or dependency download is needed for this compile-only check.
    import playwright
    bundled = Path(playwright.__file__).parent / "driver" / "node"
    return str(bundled) if bundled.is_file() else shutil.which("node")


def validate_html(html: str) -> dict | None:
    parser = _Scripts()
    parser.feed(html)
    parser.close()
    scripts = [s for s in parser.scripts if s["source"].strip()]
    if not scripts:
        return None
    try:
        node = _node_executable()
        if not node:
            raise RuntimeError("JavaScript compiler is unavailable")
        result = subprocess.run(
            [node, "--experimental-vm-modules", "--no-warnings", "-e", _COMPILER],
            input=json.dumps(scripts), text=True, capture_output=True, timeout=5, check=True,
        )
        errors = json.loads(result.stdout)
    except (ImportError, OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        return {"error": {"code": "HTML_PREFLIGHT_UNAVAILABLE", "message": (
            f"JavaScript preflight could not finish ({type(exc).__name__}). Nothing was published. "
            "This is a validator failure, not evidence that the artifact source is invalid."
        )}}
    if errors:
        first = errors[0]
        location = f"HTML line {first['line']}" if first['line'] else f"script starting at HTML line {first['script_start_line']}"
        return {"error": {
            "code": "INVALID_JAVASCRIPT",
            "message": f"Inline JavaScript syntax error at {location}: {first['message']}. "
                       "Nothing was published. Fix the submitted source or edits and retry; "
                       "the previous published version remains current.",
            "errors": errors,
        }}
    return None
