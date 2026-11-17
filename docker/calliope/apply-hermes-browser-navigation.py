"""Recognize a Chromium network error in a successful browser script receipt."""
from pathlib import Path
p = Path('/opt/hermes/tools/browser_use_cli.py')
s = p.read_text()
old = '    if workspace:\n        result["workspace"] = workspace\n'
assert s.count(old) == 1, 'Pinned browser result seam changed'
s = s.replace(old, '''    if "chrome-error://chromewebdata/" in result["output"]:
        result["success"] = False
        result["error_type"] = "browser_navigation_failed"
        result["error"] = ("The browser reached a Chromium network error page, not the requested app. "
                           "Undefined page globals do not establish missing runtime APIs. "
                           "Use capture_live_app for authenticated private-app inspection.")
''' + old)
p.write_text(s)
