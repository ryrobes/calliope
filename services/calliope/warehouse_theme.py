"""Shared browser-side appearance assets for the Warehouse UI.

The login page, artifact hub, and optional Calliope notebook are intentionally
framework-free, but they should still feel like one application.  This module
owns the public theme asset routes and the small HTML include all three shells
use. Published HTML/JS artifacts never inherit the Warehouse theme; the
system-owned Artifact Lens mounts its own isolated Shadow DOM when requested by
the serving shim. The explicit Adaptive Calliope Design Profile is the sole
opt-in exception: its artifact runtime receives a sanitized viewer snapshot.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

from starlette.responses import FileResponse, Response


_ASSET_DIR = Path(__file__).resolve().parent / "theme"
_IMAGE_DIR = _ASSET_DIR / "images"
_FONT_DIR = _ASSET_DIR / "fonts"
_FAVICON = _ASSET_DIR / "datarabbit.svg"
_ARTIFACT_LENS_JS = _ASSET_DIR / "artifact-lens.js"
_ARTIFACT_LENS_CSS = _ASSET_DIR / "artifact-lens.css"
_ADAPTIVE_ARTIFACT_JS = _ASSET_DIR / "adaptive-artifact.js"
_CHARTS_DIR = Path(__file__).resolve().parent / "charts"
TANSTACK_CHARTS_VERSION = "0.16.2"
TANSTACK_CHARTS_SRC = f"/charts/rvbbit-tanstack-charts-{TANSTACK_CHARTS_VERSION}.js"
_TANSTACK_CHARTS_JS = _CHARTS_DIR / f"rvbbit-tanstack-charts-{TANSTACK_CHARTS_VERSION}.js"
TANSTACK_TABLE_VERSION = "9.2.4"
TANSTACK_VIRTUAL_VERSION = "3.17.10"
TANSTACK_TABLE_SRC = f"/charts/rvbbit-tanstack-table-core-{TANSTACK_TABLE_VERSION}.js"
TANSTACK_VIRTUAL_SRC = f"/charts/rvbbit-tanstack-virtual-core-{TANSTACK_VIRTUAL_VERSION}.js"
# Exact immutable assets only. Old publications keep their original grammar.
_TANSTACK_ASSETS = {
    "/charts/rvbbit-dashboard-1.0.0.js": (_CHARTS_DIR / "rvbbit-dashboard-1.0.0.js", "1.0.0"),
    "/charts/rvbbit-dashboard-1.0.1.js": (_CHARTS_DIR / "rvbbit-dashboard-1.0.1.js", "1.0.1"),
    TANSTACK_CHARTS_SRC: (_TANSTACK_CHARTS_JS, TANSTACK_CHARTS_VERSION),
    "/charts/rvbbit-tanstack-charts-0.3.1.js": (_CHARTS_DIR / "rvbbit-tanstack-charts-0.3.1.js", "0.3.1"),
    TANSTACK_TABLE_SRC: (_CHARTS_DIR / Path(TANSTACK_TABLE_SRC).name, TANSTACK_TABLE_VERSION),
    TANSTACK_VIRTUAL_SRC: (_CHARTS_DIR / Path(TANSTACK_VIRTUAL_SRC).name, TANSTACK_VIRTUAL_VERSION),
}
_THEME_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,160}$")
_FONT_FILE = re.compile(r"^LTSuperior(?:Serif|Mono)?-[A-Za-z]+\.otf$")
_ACCOUNT_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}$")
_THEME_LABELS = {
    "callie-bg-bright": "Callie Bright",
    "callie-bg-dark": "Callie Dark",
}
_TANSTACK_SCRIPT_TAG = re.compile(
    r"<script\b[^>]*\bsrc\s*=\s*(['\"])"
    + "(" + "|".join(re.escape(src) for src in _TANSTACK_ASSETS) + ")"
    + r"\1[^>]*>\s*</script\s*>",
    re.IGNORECASE,
)
_ADAPTIVE_SCRIPT_TAG = re.compile(
    r"<script\b[^>]*\bsrc\s*=\s*(['\"])"
    + re.escape("/theme/adaptive-artifact.js")
    + r"\1[^>]*>\s*</script\s*>",
    re.IGNORECASE,
)


@lru_cache(maxsize=4)
def _tanstack_runtime_source(src: str = TANSTACK_CHARTS_SRC) -> str:
    return _TANSTACK_ASSETS[src][0].read_text(encoding="utf-8")


def inline_chart_runtime(html: str) -> str:
    """Inline exact hosted TanStack assets for originless capture/PDF renders.

    Each script retains its authored version, including legacy Charts. Table
    and Virtual are optional independent cores; unrelated scripts stay intact.
    """
    document = html or ""
    if not any(src in document for src in _TANSTACK_ASSETS):
        return document

    def replace(match):
        src = match.group(2)
        try:
            source = re.sub(r"</script", r"<\\/script", _tanstack_runtime_source(src), flags=re.IGNORECASE)
        except OSError:
            return match.group(0)
        version = _TANSTACK_ASSETS[src][1]
        return f'<script data-rvbbit-chart-runtime="{version}">' + source + "</script>"

    return _TANSTACK_SCRIPT_TAG.sub(replace, document)


@lru_cache(maxsize=1)
def _adaptive_runtime_source() -> str:
    return _ADAPTIVE_ARTIFACT_JS.read_text(encoding="utf-8")


def inline_adaptive_runtime(html: str) -> str:
    """Inline the first-party adaptive runtime for originless captures."""
    document = html or ""
    if "/theme/adaptive-artifact.js" not in document:
        return document
    try:
        source = re.sub(r"</script", r"<\/script", _adaptive_runtime_source(), flags=re.IGNORECASE)
    except OSError:
        return document
    return _ADAPTIVE_SCRIPT_TAG.sub(
        lambda _match: '<script data-rvbbit-adaptive-runtime="1">' + source + "</script>",
        document,
    )


def inline_capture_runtimes(html: str) -> str:
    """Inline every system-owned runtime needed by Playwright set_content."""
    return inline_adaptive_runtime(inline_chart_runtime(html))


def metric_card_assets() -> str:
    """Shared cards; data and actions keep their existing authenticated APIs."""
    return (
        '<link rel="stylesheet" href="/theme/metric-cards.css">'
        '<script src="/theme/metric-cards.js"></script>'
    )


def head_assets() -> str:
    """The shared, public include used in each first-party Warehouse page."""
    return (
        '<link rel="icon" href="/calliope/callie-favicon.png" type="image/png">'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        'family=Homemade+Apple&display=swap">'
        '<link rel="stylesheet" href="/theme/superior-fonts.css">'
        '<link rel="stylesheet" href="/theme/warehouse-theme.css">'
        '<script src="/theme/warehouse-theme.js"></script>'
    )


def _account_initials(name: str, identity: str) -> str:
    source = name or identity.split("@", 1)[0]
    words = [word for word in re.split(r"[^A-Za-z0-9]+", source) if word]
    if len(words) > 1:
        initials = words[0][0] + words[-1][0]
    elif words:
        initials = words[0][:2]
    else:
        initials = "?"
    return initials.upper()


def workspace_navigation(active: str = "", *, can_administer: bool = False) -> str:
    """One navigation catalog for the first-party workspace shells.

    The caller resolves administration from the authenticated principal; URL
    flags and client state never grant access to the System workspace.
    """
    links = [("floor", "/floor", "Floor"), ("gallery", "/gallery", "Gallery"),
             ("meaning", "/meaning", "Meaning"), ("team", "/team", "Team")]
    if can_administer:
        links.append(("system", "/calliope?system=1", "System"))
    return "".join(
        f'<a href="{href}"' + (' id="system-workspace-open"' if key == "system" else "")
        + (' aria-current="page"' if active == key else "") + f'>{label}</a>'
        for key, href, label in links
    )


def workspace_header(session, *, active="", can_administer=False) -> str:
    """Shared page chrome for account settings and the app review inbox."""
    return (
        '<nav class="workspace-rail" data-warehouse-header aria-label="Calliope navigation">'
        '<a class="calliope-brand" href="/calliope?view=zen" aria-label="Open Calliope">'
        '<span class="calliope-brand-name">Calliope</span>'
        '<span class="calliope-brand-provenance"><img class="calliope-brand-rabbit" '
        'src="/theme/datarabbit-mark.svg" alt=""><span class="calliope-brand-byline">by RVBBIT.AI</span></span></a>'
        '<div class="workspace-nav" aria-label="Workspace views">'
        + (workspace_navigation(active, can_administer=can_administer) if session else '')
        + '</div><div class="workspace-account"><span data-warehouse-theme-anchor></span>'
        + account_control(session, workspace_menu=True, can_administer=can_administer)
        + '</div></nav>'
    )


def _workspace_account_menu(can_administer: bool) -> str:
    admin_actions = (
        '<div class="warehouse-account-section warehouse-account-admin" '
        'data-warehouse-account-admin role="group" aria-label="Administration">'
        '<span class="warehouse-account-section-label">Administration</span>'
        '<a class="warehouse-account-action" href="/account?view=people" role="menuitem">'
        '<span><strong>People &amp; access</strong><small>Accounts, groups, and sign-in</small></span><i aria-hidden="true">→</i></a>'
        '<a class="warehouse-account-action" href="/calliope?view=zen&amp;panel=workspace" '
        'role="menuitem" data-warehouse-account-action="workspace">'
        '<span><strong>Workspace settings</strong><small>Default appearance for your company</small></span>'
        '<i aria-hidden="true">→</i></a>'
        '<a class="warehouse-account-action" href="/calliope?view=zen&amp;panel=library" '
        'role="menuitem" data-warehouse-account-action="library">'
        '<span><strong>Library</strong><small>Capabilities and global permissions</small></span>'
        '<i aria-hidden="true">→</i></a>'
        '<a class="warehouse-account-action" href="/calliope?view=zen&amp;panel=instruments" '
        'role="menuitem" data-warehouse-account-action="instruments">'
        '<span><strong>Instruments</strong><small>Reusable guided actions</small></span>'
        '<i aria-hidden="true">→</i></a>'
        '<a class="warehouse-account-action" href="/calliope?view=zen&amp;panel=workflows" '
        'role="menuitem" data-warehouse-account-action="workflows">'
        '<span><strong>Workflows</strong><small>Scheduled and headless work</small></span>'
        '<i aria-hidden="true">→</i></a>'
        '<a class="warehouse-account-action" href="/ops" role="menuitem">'
        '<span><strong>Ops</strong><small>Work observed across conversations</small></span>'
        '<i aria-hidden="true">→</i></a>'
        '<a class="warehouse-account-action" href="/calliope?system=1" role="menuitem">'
        '<span><strong>System</strong><small>Settings, capabilities, and usage</small></span>'
        '<i aria-hidden="true">→</i></a>'
        '</div>'
        if can_administer else ""
    )
    return (
        '<div class="warehouse-account-actions" data-warehouse-account-actions>'
        '<div class="warehouse-account-section" role="group" aria-label="Your workspace">'
        '<span class="warehouse-account-section-label">Your workspace</span>'
        '<a class="warehouse-account-action" href="/calliope?view=zen&amp;panel=design-profiles" '
        'role="menuitem" data-warehouse-account-action="design-profiles">'
        '<span><strong>Design profiles</strong><small>Visual direction and reusable styles</small></span>'
        '<i aria-hidden="true">→</i></a>'
        '<a class="warehouse-account-action" href="/data" role="menuitem">'
        '<span><strong>Connected data</strong><small>Sources, tables, and services in the brain</small></span>'
        '<i aria-hidden="true">→</i></a>'
        '</div>'
        f'{admin_actions}</div>'
    )


def account_control(
    session: dict[str, Any] | None,
    *,
    workspace_menu: bool = False,
    can_administer: bool = False,
) -> str:
    """Render the shared right-aligned account menu for authenticated shells."""
    session = session or {}
    identity = str(session.get("identity") or session.get("sub") or "").strip()
    if not identity:
        return ""
    name = re.sub(r"\s+", " ", str(session.get("name") or "")).strip()[:160]
    via = str(session.get("via") or "password").strip().lower()
    provider = {
        "google": "Google",
        "pg": "Calliope account",
        "password": "Calliope account",
    }.get(via, "Calliope account")
    initials = _account_initials(name, identity)
    raw_email_avatar = (
        via in {"password", "pg"}
        and len(identity) <= 254
        and _ACCOUNT_EMAIL.fullmatch(identity) is not None
    )
    avatar = (
        '<img class="warehouse-account-avatar-image" src="/auth/avatar" alt="" '
        'width="28" height="28" decoding="async" referrerpolicy="no-referrer">'
        if session.get("picture") or raw_email_avatar else ""
    )
    heading = name or identity
    email_line = (
        f'<small class="warehouse-account-menu-email">{escape(identity)}</small>'
        if name and name.casefold() != identity.casefold() else ""
    )
    workspace_actions = _workspace_account_menu(can_administer) if workspace_menu else ""
    menu_class = (
        "warehouse-account-menu workspace-account-menu"
        if workspace_menu else "warehouse-account-menu"
    )
    return (
        '<div class="warehouse-account" data-warehouse-account '
        f'data-warehouse-owner="{escape(identity.lower(), quote=True)}">'
        '<button class="warehouse-account-trigger" type="button" '
        'aria-haspopup="menu" aria-expanded="false" '
        f'aria-label="Open account menu for {escape(identity, quote=True)}">'
        f'<span class="warehouse-account-email">{escape(identity)}</span>'
        '<span class="warehouse-account-avatar" aria-hidden="true">'
        f'<span class="warehouse-account-initials">{escape(initials)}</span>{avatar}'
        '</span></button>'
        f'<div class="{menu_class}" role="menu" aria-label="Account options" hidden>'
        '<div class="warehouse-account-menu-profile" role="none">'
        f'<span>Signed in with {escape(provider)}</span>'
        f'<strong>{escape(heading)}</strong>{email_line}'
        '</div>'
        '<a class="warehouse-account-action" href="/account" role="menuitem">'
        '<span><strong>My account</strong><small>Profile, password, and memberships</small></span><i aria-hidden="true">→</i></a>'
        f'{workspace_actions}'
        '<a class="warehouse-account-signout" href="/auth/logout" role="menuitem">'
        '<span>Sign out</span><span aria-hidden="true">→</span></a>'
        '</div></div>'
    )


def artifact_not_found_document(
    identity: str = "",
    session: dict[str, Any] | None = None,
) -> str:
    """Return the generic, viewer-adaptive browser page for missing artifacts.

    This is first-party system chrome, not a published dashboard. It therefore
    has no artifact row or version, while still using the same browser-stored
    palette, wallpaper, typography, and glass material as Adaptive Calliope.
    The copy intentionally does not distinguish missing, archived, or denied.
    """
    import auth

    identity = str(identity or "").strip().lower()
    background = auth.background_layer(
        0.50,
        "linear-gradient(135deg,"
        "color-mix(in oklch,var(--void,#0b1218) 70%,transparent),"
        "color-mix(in oklch,var(--void,#0b1218) 91%,transparent))",
        scene_key=identity or None,
    )
    account = account_control(
        session or ({"identity": identity, "via": "password"} if identity else {})
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="color-scheme" content="dark light">
<title>Artifact unavailable · Calliope</title>
<style>
*{{box-sizing:border-box}}
html{{background:var(--void,#0b1218)}}
body{{min-height:100vh;margin:0;background:transparent;color:var(--bone,#edf2f4);
  font-family:var(--font-superior-sans);-webkit-font-smoothing:antialiased}}
a{{color:inherit;text-decoration:none}}
.artifact-not-found-nav{{position:relative;z-index:3;display:flex;align-items:center;gap:14px;
  min-height:58px;padding:8px clamp(18px,4vw,62px);border-bottom:1px solid var(--line,rgba(237,242,244,.15));
  background:color-mix(in oklch,var(--panel,#111d25) 46%,transparent);
  -webkit-backdrop-filter:blur(22px) saturate(1.18);backdrop-filter:blur(22px) saturate(1.18)}}
.artifact-not-found-nav .artifact-not-found-account{{margin-left:auto;display:flex;align-items:center;gap:12px}}
.artifact-not-found-main{{position:relative;isolation:isolate;min-height:calc(100vh - 58px);display:grid;
  place-items:center;padding:clamp(28px,6vw,88px)}}
.artifact-not-found-main::before{{content:"";position:absolute;z-index:-1;inset:0;pointer-events:none;
  background-image:linear-gradient(var(--line,rgba(237,242,244,.12)) 1px,transparent 1px),
    linear-gradient(90deg,var(--line,rgba(237,242,244,.12)) 1px,transparent 1px);
  background-size:72px 72px;opacity:.28;
  -webkit-mask-image:radial-gradient(ellipse at center,#000,transparent 72%);mask-image:radial-gradient(ellipse at center,#000,transparent 72%)}}
.artifact-not-found-panel{{position:relative;width:min(920px,100%);overflow:hidden;
  border:1px solid var(--line,rgba(237,242,244,.15));
  background:color-mix(in oklch,var(--panel,#111d25) 79%,transparent);
  -webkit-backdrop-filter:blur(22px) saturate(1.15);backdrop-filter:blur(22px) saturate(1.15);
  box-shadow:0 26px 90px color-mix(in oklch,var(--void,#0b1218) 58%,transparent)}}
.artifact-not-found-panel::before{{content:"";position:absolute;inset:0 0 auto;height:2px;
  background:linear-gradient(90deg,var(--main,#68c7b2),var(--rvbbit-accent,#f5b446),transparent 82%)}}
.artifact-not-found-masthead{{display:flex;align-items:center;justify-content:space-between;gap:20px;
  padding:18px clamp(22px,4vw,46px);border-bottom:1px solid var(--line,rgba(237,242,244,.15));
  color:var(--fog,#9aa8ae);font:600 10px/1 var(--font-superior-mono);
  letter-spacing:.16em;text-transform:uppercase}}
.artifact-not-found-status{{display:inline-flex;align-items:center;gap:9px}}
.artifact-not-found-status::before{{content:"";width:7px;height:7px;border:1px solid var(--main,#68c7b2);
  background:color-mix(in oklch,var(--main,#68c7b2) 24%,transparent);box-shadow:0 0 14px color-mix(in oklch,var(--main,#68c7b2) 38%,transparent)}}
.artifact-not-found-content{{display:grid;grid-template-columns:minmax(0,1fr) minmax(190px,.38fr);
  gap:clamp(28px,5vw,72px);align-items:end;padding:clamp(34px,6vw,72px) clamp(24px,6vw,68px)}}
.artifact-not-found-kicker{{margin-bottom:14px;color:var(--main,#68c7b2);
  font:600 10px/1.2 var(--font-superior-mono);letter-spacing:.16em;text-transform:uppercase}}
.artifact-not-found-copy h1{{max-width:680px;margin:0;color:var(--bone-bright,#fff);
  font:500 clamp(38px,6.2vw,76px)/.96 var(--font-superior-serif);letter-spacing:-.035em}}
.artifact-not-found-copy p{{max-width:620px;margin:24px 0 0;color:var(--fog,#9aa8ae);
  font-size:clamp(15px,1.65vw,19px);line-height:1.55}}
.artifact-not-found-code{{align-self:start;color:color-mix(in oklch,var(--bone,#edf2f4) 16%,transparent);
  font:500 clamp(72px,13vw,150px)/.72 var(--font-superior-sans);
  font-variant-numeric:tabular-nums;letter-spacing:-.08em;text-align:right;user-select:none}}
.artifact-not-found-actions{{display:flex;flex-wrap:wrap;gap:10px;padding:0 clamp(24px,6vw,68px) clamp(30px,5vw,52px)}}
.artifact-not-found-actions a{{display:inline-flex;align-items:center;justify-content:center;min-height:38px;padding:0 17px;
  border:1px solid var(--line,rgba(237,242,244,.15));color:var(--fog,#9aa8ae);
  background:color-mix(in oklch,var(--panel-raised,#172731) 62%,transparent);
  font:600 10px/1 var(--font-superior-mono);letter-spacing:.1em;text-transform:uppercase;
  transition:transform .18s ease,border-color .18s ease,color .18s ease,background .18s ease}}
.artifact-not-found-actions a:first-child{{border-color:color-mix(in oklch,var(--main,#68c7b2) 54%,var(--line));
  color:var(--bone-bright,#fff);background:color-mix(in oklch,var(--main,#68c7b2) 12%,transparent)}}
.artifact-not-found-actions a:hover{{transform:translateY(-2px);border-color:var(--main,#68c7b2);color:var(--bone-bright,#fff)}}
.artifact-not-found-actions a:focus-visible{{outline:2px solid var(--main,#68c7b2);outline-offset:3px}}
@media (max-width:680px){{
  .artifact-not-found-content{{grid-template-columns:1fr}}
  .artifact-not-found-code{{position:absolute;right:22px;top:92px;font-size:86px;opacity:.58}}
  .artifact-not-found-copy h1{{max-width:82%}}
}}
@media (prefers-reduced-motion:reduce){{.artifact-not-found-actions a{{transition:none}}}}
</style>
{head_assets()}
</head><body data-warehouse-page="artifact-not-found">
{background}
<nav class="artifact-not-found-nav" data-warehouse-header>
  <a class="calliope-brand" href="/gallery" aria-label="Calliope Gallery">
    <span class="calliope-brand-name">Calliope</span>
    <span class="calliope-brand-provenance">
      <img class="calliope-brand-rabbit" src="/theme/datarabbit-mark.svg" alt="">
      <span class="calliope-brand-byline">by RVBBIT.AI</span>
    </span>
  </a>
  <div class="artifact-not-found-account"><span data-warehouse-theme-anchor></span>{account}</div>
</nav>
<main class="artifact-not-found-main">
  <article class="artifact-not-found-panel" aria-labelledby="artifact-not-found-title">
    <header class="artifact-not-found-masthead">
      <span class="artifact-not-found-status">Artifact route</span>
      <span>Not available</span>
    </header>
    <div class="artifact-not-found-content">
      <div class="artifact-not-found-copy">
        <div class="artifact-not-found-kicker">Nothing to render</div>
        <h1 id="artifact-not-found-title">This page isn&rsquo;t available here.</h1>
        <p>This does not exist or you might not have permission.</p>
      </div>
      <div class="artifact-not-found-code" aria-hidden="true">404</div>
    </div>
    <footer class="artifact-not-found-actions">
      <a href="/gallery">Browse your Gallery <span aria-hidden="true">&nbsp;→</span></a>
      <a href="/calliope">Ask Calliope</a>
    </footer>
  </article>
</main>
</body></html>"""


def _label(image_id: str) -> str:
    if image_id in _THEME_LABELS:
        return _THEME_LABELS[image_id]
    if image_id.startswith("grok-"):
        return f"Grok {image_id[5:13]}"
    label = image_id
    if label.startswith("bvictor_"):
        label = label[len("bvictor_"):]
    if label.endswith("-high"):
        label = label[:-len("-high")]
    return " ".join(part.capitalize() for part in label.replace("_", " ").split())


def theme_library() -> list[dict[str, Any]]:
    """Return only pairs that have both a lightweight tile and full backdrop."""
    full_dir = _IMAGE_DIR / "full"
    thumb_dir = _IMAGE_DIR / "thumb"
    if not full_dir.is_dir() or not thumb_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for full in sorted(full_dir.glob("*.webp"), key=lambda p: p.name.casefold()):
        image_id = full.stem
        thumb = thumb_dir / full.name
        if not _THEME_ID.fullmatch(image_id) or not thumb.is_file():
            continue
        items.append(
            {
                "id": image_id,
                "label": _label(image_id),
                "thumb": f"/theme/images/thumb/{image_id}.webp",
                "url": f"/theme/images/full/{image_id}.webp",
                "size_bytes": full.stat().st_size,
            }
        )
    return items


# /vendor/<lib>@<version>/<file> — pinned by vendor/manifest.json, fetched at
# image build time (scripts/vendor-dashboard-assets.py). Versioned paths never
# change content, so they cache forever.
_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
_VENDOR_LIB = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}@[0-9][0-9A-Za-z.+-]{0,31}$")
_VENDOR_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:js|css|map|woff2?|json)$")
_VENDOR_MEDIA = {".js": "text/javascript", ".css": "text/css", ".map": "application/json",
                 ".woff2": "font/woff2", ".woff": "font/woff", ".json": "application/json"}


def register_theme_routes(mcp: Any) -> None:
    """Register public assets.

    These routes must remain public: the saved appearance is restored on the
    login page, before a Warehouse session exists.  The files are static,
    non-user data and the path parameters are strictly jailed.
    """

    @mcp.custom_route("/theme/datarabbit.svg", methods=["GET"])
    async def datarabbit_favicon(_request):
        return FileResponse(
            _FAVICON,
            media_type="image/svg+xml",
            headers={
                "cache-control": "public, max-age=31536000, immutable",
                "x-content-type-options": "nosniff",
            },
        )

    @mcp.custom_route("/theme/datarabbit-mark.svg", methods=["GET"])
    async def datarabbit_mark(_request):
        return FileResponse(
            _ASSET_DIR / "datarabbit-mark.svg",
            media_type="image/svg+xml",
            headers={
                "cache-control": "public, max-age=31536000, immutable",
                "x-content-type-options": "nosniff",
            },
        )

    @mcp.custom_route("/theme/warehouse-theme.css", methods=["GET"])
    async def warehouse_theme_css(_request):
        return FileResponse(
            _ASSET_DIR / "warehouse-theme.css",
            media_type="text/css",
            headers={"cache-control": "no-cache", "x-content-type-options": "nosniff"},
        )

    @mcp.custom_route("/theme/workspace-page.css", methods=["GET"])
    async def workspace_page_css(_request):
        return FileResponse(
            _ASSET_DIR / "workspace-page.css", media_type="text/css",
            headers={"cache-control": "no-cache", "x-content-type-options": "nosniff"},
        )

    @mcp.custom_route("/theme/superior-fonts.css", methods=["GET"])
    async def superior_fonts_css(_request):
        return FileResponse(
            _ASSET_DIR / "superior-fonts.css",
            media_type="text/css",
            headers={
                # The CSS URL is intentionally stable while individual font files
                # are immutable. Revalidate the manifest so future family additions
                # do not require a cache-busting URL throughout authored artifacts.
                "cache-control": "no-cache",
                "x-content-type-options": "nosniff",
                # Fonts are public static assets. Captures, PDFs, and thumbnails
                # render artifacts from an about:blank document, so @font-face
                # fetches are cross-origin and need CORS to load at all.
                "access-control-allow-origin": "*",
            },
        )

    @mcp.custom_route("/theme/fonts/{font_name}", methods=["GET"])
    async def superior_font(request):
        font_name = request.path_params.get("font_name", "")
        if not _FONT_FILE.fullmatch(font_name):
            return Response("not found", status_code=404)
        path = _FONT_DIR / font_name
        if not path.is_file():
            return Response("not found", status_code=404)
        return FileResponse(
            path,
            media_type="font/otf",
            headers={
                "cache-control": "public, max-age=31536000, immutable",
                "x-content-type-options": "nosniff",
                "access-control-allow-origin": "*",
            },
        )

    @mcp.custom_route("/theme/warehouse-theme.js", methods=["GET"])
    async def warehouse_theme_js(_request):
        return FileResponse(
            _ASSET_DIR / "warehouse-theme.js",
            media_type="text/javascript",
            headers={"cache-control": "no-cache", "x-content-type-options": "nosniff"},
        )

    @mcp.custom_route("/theme/artifact-lens.js", methods=["GET"])
    async def artifact_lens_js(_request):
        return FileResponse(
            _ARTIFACT_LENS_JS,
            media_type="text/javascript",
            headers={"cache-control": "no-cache", "x-content-type-options": "nosniff"},
        )

    @mcp.custom_route("/theme/artifact-lens.css", methods=["GET"])
    async def artifact_lens_css(_request):
        return FileResponse(
            _ARTIFACT_LENS_CSS,
            media_type="text/css",
            headers={"cache-control": "no-cache", "x-content-type-options": "nosniff"},
        )

    @mcp.custom_route("/theme/brain-layout.js", methods=["GET"])
    async def brain_layout_js(_request):
        return FileResponse(_ASSET_DIR / "brain-layout.js", media_type="text/javascript", headers={"cache-control":"no-cache"})

    @mcp.custom_route("/theme/gallery-brain.js", methods=["GET"])
    async def gallery_brain_js(_request):
        return FileResponse(_ASSET_DIR / "gallery-brain.js", media_type="text/javascript", headers={"cache-control":"no-cache"})

    @mcp.custom_route("/theme/gallery-brain.css", methods=["GET"])
    async def gallery_brain_css(_request):
        return FileResponse(_ASSET_DIR / "gallery-brain.css", media_type="text/css", headers={"cache-control":"no-cache"})

    @mcp.custom_route("/theme/gallery-tables.js", methods=["GET"])
    async def gallery_tables_js(_request):
        return FileResponse(_ASSET_DIR / "gallery-tables.js", media_type="text/javascript", headers={"cache-control":"no-cache"})

    @mcp.custom_route("/theme/gallery-tables.css", methods=["GET"])
    async def gallery_tables_css(_request):
        return FileResponse(_ASSET_DIR / "gallery-tables.css", media_type="text/css", headers={"cache-control":"no-cache"})

    @mcp.custom_route("/theme/gallery-jobs.js", methods=["GET"])
    async def gallery_jobs_js(_request):
        return FileResponse(_ASSET_DIR / "gallery-jobs.js", media_type="text/javascript", headers={"cache-control":"no-cache"})

    @mcp.custom_route("/theme/gallery-jobs.css", methods=["GET"])
    async def gallery_jobs_css(_request):
        return FileResponse(_ASSET_DIR / "gallery-jobs.css", media_type="text/css", headers={"cache-control":"no-cache"})

    @mcp.custom_route("/theme/metric-cards.js", methods=["GET"])
    async def metric_cards_js(_request):
        return FileResponse(
            _ASSET_DIR / "metric-cards.js", media_type="text/javascript",
            headers={"cache-control": "no-cache", "x-content-type-options": "nosniff"},
        )

    @mcp.custom_route("/theme/metric-cards.css", methods=["GET"])
    async def metric_cards_css(_request):
        return FileResponse(
            _ASSET_DIR / "metric-cards.css", media_type="text/css",
            headers={"cache-control": "no-cache", "x-content-type-options": "nosniff"},
        )

    @mcp.custom_route("/theme/adaptive-artifact.js", methods=["GET"])
    async def adaptive_artifact_js(_request):
        return FileResponse(
            _ADAPTIVE_ARTIFACT_JS,
            media_type="text/javascript",
            headers={"cache-control": "no-cache", "x-content-type-options": "nosniff"},
        )

    @mcp.custom_route("/vendor/{lib}/{filename}", methods=["GET"])
    async def vendor_asset(request):
        """Pinned dashboard runtime libraries (vendor/manifest.json), immutable."""
        lib = str(request.path_params.get("lib") or "")
        filename = str(request.path_params.get("filename") or "")
        if not _VENDOR_LIB.fullmatch(lib) or not _VENDOR_FILE.fullmatch(filename):
            return Response("not found", status_code=404)
        path = _VENDOR_DIR / lib / filename
        if not path.is_file():
            return Response("not found", status_code=404)
        media = _VENDOR_MEDIA.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(
            path,
            media_type=media,
            headers={
                "cache-control": "public, max-age=31536000, immutable",
                "x-content-type-options": "nosniff",
                "access-control-allow-origin": "*",
            },
        )

    def register_tanstack_asset(src, path):
        async def tanstack_runtime(_request):
            return FileResponse(
                path,
                media_type="text/javascript",
                headers={
                    "cache-control": "public, max-age=31536000, immutable",
                    "x-content-type-options": "nosniff",
                },
            )
        tanstack_runtime.__name__ = "tanstack_" + path.stem.replace("-", "_").replace(".", "_")
        mcp.custom_route(src, methods=["GET"])(tanstack_runtime)

    for src, (path, _version) in _TANSTACK_ASSETS.items():
        register_tanstack_asset(src, path)

    @mcp.custom_route("/theme/library", methods=["GET"])
    async def warehouse_theme_library(_request):
        return Response(
            json.dumps({"items": theme_library()}, separators=(",", ":")),
            media_type="application/json",
            headers={"cache-control": "public, max-age=300", "x-content-type-options": "nosniff"},
        )

    @mcp.custom_route("/theme/images/{variant}/{image_id}.webp", methods=["GET"])
    async def warehouse_theme_image(request):
        variant = request.path_params.get("variant", "")
        image_id = request.path_params.get("image_id", "")
        if variant not in {"thumb", "full"} or not _THEME_ID.fullmatch(image_id):
            return Response("not found", status_code=404)
        path = _IMAGE_DIR / variant / f"{image_id}.webp"
        if not path.is_file():
            return Response("not found", status_code=404)
        return FileResponse(
            path,
            media_type="image/webp",
            headers={
                "cache-control": "public, max-age=31536000, immutable",
                "x-content-type-options": "nosniff",
            },
        )
