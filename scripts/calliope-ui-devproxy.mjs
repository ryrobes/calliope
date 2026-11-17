// Local dev proxy for the Calliope UI.
//
// Iterate on the Calliope notebook, Floor, and Gallery against a live appliance without
// rebuilding its image: this serves the working-tree assets under /calliope/*,
// /floor/*, and /theme/*, renders the local page templates with the live
// Gallery's background/account chrome, overlays Gallery's working-tree CSS
// and primary navigation, and streams every other request — API, SSE turn
// streams, images — to the upstream with the browser's headers intact.
//
//   node scripts/calliope-ui-devproxy.mjs            # → http://127.0.0.1:3011/calliope
//   UPSTREAM_PORT=3003 PORT=3011 ASSET_DIR=... node scripts/calliope-ui-devproxy.mjs
//
// Sign in once at http://127.0.0.1:<UPSTREAM_PORT>; cookies are per host, not
// per port, so the proxy origin is signed in too. Use 127.0.0.1 on both, not
// localhost.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const UPSTREAM = { host: "127.0.0.1", port: Number(process.env.UPSTREAM_PORT || 3003) };
const PORT = Number(process.env.PORT || 3011);
const ASSET_DIR = process.env.ASSET_DIR
  || path.resolve(path.dirname(new URL(import.meta.url).pathname), "../services/calliope/calliope");
const SERVER_SOURCE = path.resolve(ASSET_DIR, "../server.py");
const TYPES = {
  ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".html": "text/html; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".json": "application/json",
  ".webp": "image/webp", ".woff2": "font/woff2",
};

function upstreamRequest(req, bodyChunks, requestPath = req.url) {
  return new Promise((resolve, reject) => {
    const headers = { ...req.headers, host: `${UPSTREAM.host}:${UPSTREAM.port}` };
    delete headers["accept-encoding"];
    const up = http.request({ ...UPSTREAM, method: req.method, path: requestPath, headers }, resolve);
    up.on("error", reject);
    if (bodyChunks) up.end(Buffer.concat(bodyChunks)); else req.pipe(up);
  });
}

function readAll(stream) {
  return new Promise((resolve) => { const c = []; stream.on("data", (d) => c.push(d)); stream.on("end", () => resolve(Buffer.concat(c))); });
}

function between(text, start, end) {
  const i = text.indexOf(start); if (i < 0) return null;
  const j = text.indexOf(end, i + start.length); if (j < 0) return null;
  return text.slice(i + start.length, j);
}

function accountControl(text) {
  const match = text.match(/(<div class="warehouse-account"[\s\S]*?<a class="warehouse-account-signout"[\s\S]*?<\/a>\s*<\/div>\s*<\/div>)/);
  return match ? match[1] : "";
}

function workspaceAccountActions(canAdminister) {
  const admin = canAdminister ? `<div class="warehouse-account-section warehouse-account-admin" data-warehouse-account-admin role="group" aria-label="Administration">
    <span class="warehouse-account-section-label">Administration</span>
    <a class="warehouse-account-action" href="/calliope?view=zen&amp;panel=library" role="menuitem" data-warehouse-account-action="library"><span><strong>Library</strong><small>Capabilities and global permissions</small></span><i aria-hidden="true">→</i></a>
    <a class="warehouse-account-action" href="/calliope?view=zen&amp;panel=instruments" role="menuitem" data-warehouse-account-action="instruments"><span><strong>Instruments</strong><small>Reusable guided actions</small></span><i aria-hidden="true">→</i></a>
    <a class="warehouse-account-action" href="/calliope?view=zen&amp;panel=workflows" role="menuitem" data-warehouse-account-action="workflows"><span><strong>Workflows</strong><small>Scheduled and headless work</small></span><i aria-hidden="true">→</i></a>
    <a class="warehouse-account-action" href="/calliope?view=zen&amp;panel=models" role="menuitem" data-warehouse-account-action="models"><span><strong>Models &amp; agents</strong><small>Providers, routing, and specialists</small></span><i aria-hidden="true">→</i></a>
  </div>` : "";
  return `<div class="warehouse-account-actions" data-warehouse-account-actions>
    <div class="warehouse-account-section" role="group" aria-label="Your workspace">
      <span class="warehouse-account-section-label">Your workspace</span>
      <a class="warehouse-account-action" href="/calliope?view=zen&amp;panel=design-profiles" role="menuitem" data-warehouse-account-action="design-profiles"><span><strong>Design profiles</strong><small>Visual direction and reusable styles</small></span><i aria-hidden="true">→</i></a>
    </div>${admin}
  </div>`;
}

async function upstreamCanAdminister(req) {
  try {
    const up = await upstreamRequest(req, [], "/api/calliope/config");
    const data = JSON.parse((await readAll(up)).toString("utf8"));
    return up.statusCode === 200 && data?.can_administer === true;
  } catch {
    return false;
  }
}

async function workspaceAccountControl(req, text) {
  let account = accountControl(text);
  if (!account || account.includes("data-warehouse-account-actions")) return account;
  account = account.replace(
    'class="warehouse-account-menu"',
    'class="warehouse-account-menu workspace-account-menu"',
  );
  return account.replace(
    '<a class="warehouse-account-signout"',
    `${workspaceAccountActions(await upstreamCanAdminister(req))}<a class="warehouse-account-signout"`,
  );
}

function pythonTripleQuoted(source, name) {
  const start = `${name} = \"\"\"`;
  const from = source.indexOf(start);
  if (from < 0) throw new Error(`missing ${name} in ${SERVER_SOURCE}`);
  const body = from + start.length;
  const to = source.indexOf('\"\"\"', body);
  if (to < 0) throw new Error(`unterminated ${name} in ${SERVER_SOURCE}`);
  return source.slice(body, to);
}

function excalifontHashes() {
  try {
    return fs.readdirSync(path.join(ASSET_DIR, "sketch-assets/fonts/Excalifont"))
      .filter((name) => name.startsWith("Excalifont-Regular-") && name.endsWith(".woff2"))
      .map((name) => name.slice("Excalifont-Regular-".length, -".woff2".length))
      .sort()
      .join(",");
  } catch {
    return "";
  }
}

function galleryCss() {
  const source = fs.readFileSync(SERVER_SOURCE, "utf8");
  return pythonTripleQuoted(source, "_LANDING_CSS")
    + pythonTripleQuoted(source, "_GALLERY_METRICS_CSS");
}

function galleryHeader(text, account) {
  const upstreamLeftRail = between(
    text,
    '<nav data-warehouse-header>\n ',
    '\n <span class="who">',
  );
  if (!upstreamLeftRail) throw new Error("could not locate Gallery's brand in upstream header");
  // Newer appliances already carry these links. Strip any upstream copy before
  // injecting the working-tree rail so the proxy is safe across both versions.
  let brand = upstreamLeftRail.replace(
    /\s*<div class="gallery-rail-nav"[\s\S]*?<\/div>\s*/g,
    "\n ",
  );
  if (brand.includes('<span class="calliope-brand">')) {
    brand = brand
      .replace(
        '<span class="calliope-brand">',
        '<a class="calliope-brand" href="/calliope?view=zen" aria-label="Open Calliope">',
      )
      .replace(/<\/span>\s*$/, "</a>");
  } else {
    brand = brand.replace(
      /(<a class="calliope-brand")\s+href="[^"]*"(?:\s+aria-label="[^"]*")?/,
      '$1 href="/calliope?view=zen" aria-label="Open Calliope"',
    );
  }
  return `<nav data-warehouse-header>
 ${brand}
 <div class="gallery-rail-nav" aria-label="Workspace views">
  <a href="/floor">Floor</a><a href="/gallery" aria-current="page">Gallery</a><a href="/team">Team</a>
 </div>
 <span class="who"><span data-warehouse-theme-anchor></span>${account}</span></nav>`;
}

async function renderPage(req, res) {
  const up = await upstreamRequest(req, []);
  const body = (await readAll(up)).toString("utf8");
  if (up.statusCode !== 200 || !/text\/html/.test(up.headers["content-type"] || "")) {
    res.writeHead(up.statusCode, up.headers); res.end(body); return;
  }
  const template = fs.readFileSync(path.join(ASSET_DIR, "index.html"), "utf8");
  const background = between(body, 'data-calliope-mode="chat">\n', '\n<div class="grid-wash"') || "";
  const rabbit = between(body, 'aria-label="Calliope artifacts">\n', '<span class="calliope-brand">') || "";
  const account = await workspaceAccountControl(req, body);
  const html = template
    .replace("__CALLIOPE_BACKGROUND__", background)
    .replace("__CALLIOPE_RABBIT__", rabbit)
    .replace("__CALLIOPE_ACCOUNT__", account)
    .replaceAll("__CALLIOPE_ASSET_VERSION__", `dev${Date.now()}`)
    .replaceAll("__CALLIOPE_EXCALIFONT__", excalifontHashes())
    .replace("__CALLIOPE_PAGE_TITLE__", "Calliope · dev")
    .replace("__CALLIOPE_MODE__", "chat")
    .replace("__CALLIOPE_BRAND_LINK__", 'href="/floor"');
  const headers = { ...up.headers, "content-type": "text/html; charset=utf-8", "cache-control": "no-store" };
  delete headers["content-length"]; delete headers["transfer-encoding"]; delete headers["content-encoding"];
  res.writeHead(200, headers); res.end(html);
}

async function renderFloorPage(req, res, page = "floor") {
  const up = await upstreamRequest(req, [], "/gallery");
  const body = (await readAll(up)).toString("utf8");
  if (up.statusCode !== 200 || !/text\/html/.test(up.headers["content-type"] || "")) {
    res.writeHead(up.statusCode, up.headers); res.end(body); return;
  }
  const template = fs.readFileSync(path.join(ASSET_DIR, `${page}.html`), "utf8");
  const background = between(body, "<body>\n", '\n<div class="wash"') || "";
  const account = await workspaceAccountControl(req, body);
  const html = template
    .replace("__CALLIOPE_BACKGROUND__", background)
    .replace("__CALLIOPE_ACCOUNT__", account)
    .replaceAll("__CALLIOPE_ASSET_VERSION__", `dev${Date.now()}`);
  const headers = { ...up.headers, "content-type": "text/html; charset=utf-8", "cache-control": "no-store" };
  delete headers["content-length"]; delete headers["transfer-encoding"]; delete headers["content-encoding"];
  res.writeHead(200, headers); res.end(html);
}

async function renderDataPage(req, res) {
  const up = await upstreamRequest(req, [], "/gallery");
  const body = (await readAll(up)).toString("utf8");
  if (up.statusCode !== 200 || !/text\/html/.test(up.headers["content-type"] || "")) {
    res.writeHead(up.statusCode, up.headers); res.end(body); return;
  }
  const template = fs.readFileSync(path.join(ASSET_DIR, "data.html"), "utf8");
  const background = between(body, "<body>\n", '\n<div class="wash"') || "";
  const account = accountControl(body);
  const html = template
    .replace("__CALLIOPE_BACKGROUND__", background)
    .replace("__CALLIOPE_ACCOUNT__", account)
    .replaceAll("__CALLIOPE_ASSET_VERSION__", `dev${Date.now()}`);
  const headers = { ...up.headers, "content-type": "text/html; charset=utf-8", "cache-control": "no-store" };
  delete headers["content-length"]; delete headers["transfer-encoding"]; delete headers["content-encoding"];
  res.writeHead(200, headers); res.end(html);
}

async function renderGalleryPage(req, res) {
  const up = await upstreamRequest(req, []);
  const body = (await readAll(up)).toString("utf8");
  if (up.statusCode !== 200 || !/text\/html/.test(up.headers["content-type"] || "")) {
    res.writeHead(up.statusCode, up.headers); res.end(body); return;
  }
  let html = body.replace(
    /<style>\s*\*\{box-sizing:border-box;margin:0;padding:0\}[\s\S]*?<\/style>/,
    `<style>${galleryCss()}</style>`,
  );
  if (html === body) throw new Error("could not locate Gallery's inline style block");
  const account = await workspaceAccountControl(req, body);
  html = html.replace(
    /<nav data-warehouse-header>[\s\S]*?<\/nav>/,
    galleryHeader(body, account),
  );
  const headers = { ...up.headers, "content-type": "text/html; charset=utf-8", "cache-control": "no-store" };
  delete headers["content-length"]; delete headers["transfer-encoding"]; delete headers["content-encoding"];
  res.writeHead(200, headers); res.end(html);
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, "http://x");
    if (req.method === "GET" && (url.pathname === "/calliope" || url.pathname === "/calliope/")) {
      await renderPage(req, res); return;
    }
    if (req.method === "GET" && (url.pathname === "/floor" || url.pathname === "/floor/")) {
      await renderFloorPage(req, res); return;
    }
    if (req.method === "GET" && (url.pathname === "/gallery" || url.pathname === "/gallery/")) {
      await renderGalleryPage(req, res); return;
    }
    if (req.method === "GET" && (url.pathname === "/data" || url.pathname === "/data/")) {
      await renderDataPage(req, res); return;
    }
    if (req.method === "GET" && (url.pathname === "/team" || url.pathname === "/team/")) {
      await renderFloorPage(req, res, "team"); return;
    }
    const localRoots = { "/team/": ASSET_DIR, "/calliope/": ASSET_DIR, "/floor/": ASSET_DIR, "/data/": ASSET_DIR, "/theme/": path.resolve(ASSET_DIR, "../theme") };
    const prefix = Object.keys(localRoots).find((candidate) => url.pathname.startsWith(candidate));
    if (req.method === "GET" && prefix) {
      const rel = decodeURIComponent(url.pathname.slice(prefix.length));
      const file = path.join(localRoots[prefix], rel);
      if (!rel.includes("..") && fs.existsSync(file) && fs.statSync(file).isFile()) {
        res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream", "cache-control": "no-store" });
        fs.createReadStream(file).pipe(res); return;
      }
    }
    const up = await upstreamRequest(req, null);
    res.writeHead(up.statusCode, up.headers);
    up.pipe(res);
  } catch (error) {
    res.writeHead(502, { "content-type": "text/plain" }); res.end(`dev proxy: ${error.message}`);
  }
});
server.listen(PORT, "127.0.0.1", () => console.log(`dev proxy on http://127.0.0.1:${PORT}/{calliope,floor,gallery} → ${UPSTREAM.host}:${UPSTREAM.port}`));
