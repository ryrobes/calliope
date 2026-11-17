(() => {
"use strict";

// Connected Data: a calm inventory dial, not an explorer. Left: a custom
// one-level tree (schema → tables, service → functions, integrations).
// Right: the same inventory as a constellation — groups as rings, members
// as dots, freshness as color. Hover and click sync the two sides.

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", headers: { accept: "application/json", ...(options.headers || {}) }, ...options });
  let data = {};
  try { data = await response.json(); } catch (_e) {}
  if (response.status === 401) { window.location.assign("/login?next=" + encodeURIComponent(window.location.pathname)); throw new Error("Sign-in required"); }
  if (!response.ok) throw new Error(data?.error?.message || data?.error?.code || ("Request failed · " + response.status));
  return data;
}

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { groups: [], query: "", expanded: new Set(), selected: null, sketch: null, layer: "map" };

function ageSeconds(iso) {
  const t = Date.parse(iso || "");
  return Number.isFinite(t) ? Math.max(0, (Date.now() - t) / 1000) : null;
}

function age(iso) {
  const s = ageSeconds(iso);
  if (s === null) return "—";
  if (s < 90) return "now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  if (s < 604800) return Math.floor(s / 86400) + "d ago";
  return Math.floor(s / 604800) + "w ago";
}

function freshness(iso) {
  const s = ageSeconds(iso);
  if (s === null) return "unknown";
  if (s < 2 * 3600) return "fresh";
  if (s < 48 * 3600) return "day";
  return "old";
}

function compactRows(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M rows";
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "K rows";
  return n.toLocaleString() + (n === 1 ? " row" : " rows");
}

// ── Inventory shaping ────────────────────────────────────────────────────

function buildGroups(data) {
  const groups = [];
  (data.schemas || []).forEach((schema) => {
    groups.push({
      id: "schema:" + schema.name,
      kind: "schema",
      kicker: "schema",
      name: schema.name,
      lastActivity: schema.last_load || schema.last_activity || null,
      detail: [
        `${(schema.tables || []).length} table${(schema.tables || []).length === 1 ? "" : "s"}`,
        compactRows(schema.total_rows),
      ].filter(Boolean).join(" · "),
      members: (schema.tables || []).map((table) => ({
        id: "table:" + schema.name + "." + table.name,
        name: table.name,
        meta: [compactRows(table.rows), table.last_activity ? age(table.last_activity) : ""].filter(Boolean).join(" · "),
        lastActivity: table.last_activity || null,
      })),
    });
  });
  (data.services || []).forEach((service) => {
    groups.push({
      id: "service:" + service.name,
      kind: "service",
      kicker: "service",
      name: service.name,
      lastActivity: service.last_used || null,
      detail: [
        `${(service.functions || []).length} function${(service.functions || []).length === 1 ? "" : "s"}`,
        service.call_count ? Number(service.call_count).toLocaleString() + " calls" : "",
      ].filter(Boolean).join(" · "),
      members: (service.functions || []).map((fn) => ({
        id: "fn:" + service.name + "." + fn.name,
        name: fn.name.replace(/^mcp__[^_]+(?:__)?/, "").replaceAll("_", " "),
        meta: fn.calls ? Number(fn.calls).toLocaleString() + " calls" : "",
        lastActivity: service.last_used || null,
      })),
    });
  });
  (data.integrations || []).forEach((integration) => {
    groups.push({
      id: "integration:" + integration.name,
      kind: "integration",
      kicker: "connected",
      name: integration.name,
      lastActivity: integration.last_activity || null,
      detail: integration.detail || "connected",
      members: [],
    });
  });
  return groups;
}

// ── Left tree ────────────────────────────────────────────────────────────

function matches(group, query) {
  if (!query) return true;
  if (group.name.toLowerCase().includes(query)) return true;
  return group.members.some((member) => member.name.toLowerCase().includes(query));
}

function renderTree() {
  const query = state.query;
  const visible = state.groups.filter((group) => matches(group, query));
  $("dataTree").innerHTML = visible.map((group) => {
    const open = state.expanded.has(group.id) || Boolean(query);
    const dot = freshness(group.lastActivity);
    const members = group.members
      .filter((member) => !query || member.name.toLowerCase().includes(query) || group.name.toLowerCase().includes(query));
    const rows = open && members.length
      ? `<div class="data-tree-members">${members.map((member) => `
          <button type="button" class="data-tree-member ${state.selected === member.id ? "selected" : ""}" data-member="${esc(member.id)}">
            <i class="data-dot ${freshness(member.lastActivity)}"></i>
            <span class="data-tree-member-name">${esc(member.name)}</span>
            <span class="data-tree-member-meta">${esc(member.meta)}</span>
          </button>`).join("")}</div>`
      : "";
    return `<section class="data-tree-group kind-${esc(group.kind)} ${open ? "open" : ""}" data-group="${esc(group.id)}">
      <button type="button" class="data-tree-head" data-toggle="${esc(group.id)}" aria-expanded="${open}">
        <i class="data-dot ${dot}"></i>
        <span class="data-tree-name"><small>${esc(group.kicker)}</small><strong>${esc(group.name)}</strong></span>
        <span class="data-tree-detail">${esc(group.detail)}</span>
        <span class="data-tree-when">${esc(age(group.lastActivity))}</span>
        ${group.members.length ? '<span class="data-tree-chevron" aria-hidden="true">⌄</span>' : ""}
      </button>
      ${rows}
    </section>`;
  }).join("") || '<div class="data-tree-empty">No matches on this floor of the warehouse.</div>';
}

// ── Right constellation ──────────────────────────────────────────────────

const GOLDEN = Math.PI * (3 - Math.sqrt(5));

// The generated Briarpatch scene is the primary layer when it exists; the
// live constellation stays as the always-current alternate. The iframe is
// created once and kept — Excalidraw boots are not cheap.
function syncLayers() {
  const host = $("dataMap");
  const svg = $("dataMapSvg");
  const toggle = $("dataLayerToggle");
  const showSketch = state.layer === "sketch" && state.sketch;
  if (toggle) {
    toggle.hidden = !state.sketch;
    $$0(toggle, "[data-layer]").forEach((button) => {
      button.classList.toggle("on", button.dataset.layer === (showSketch ? "sketch" : "map"));
    });
    const stamp = toggle.querySelector("[data-layer-stamp]");
    if (stamp) stamp.textContent = state.sketch ? `drawn ${age(state.sketch.updated_at)}` : "";
  }
  let frame = host.querySelector(".data-map-sketch");
  if (showSketch) {
    if (!frame) {
      frame = document.createElement("iframe");
      frame.className = "data-map-sketch";
      frame.title = state.sketch.title || "The Briarpatch";
      frame.src = state.sketch.url;
      host.appendChild(frame);
    }
    frame.hidden = false;
    svg.style.visibility = "hidden";
  } else {
    if (frame) frame.hidden = true;
    svg.style.visibility = "";
  }
}

function $$0(root, selector) { return Array.from(root.querySelectorAll(selector)); }

function renderMap() {
  syncLayers();
  const svg = $("dataMapSvg");
  const host = $("dataMap");
  const width = Math.max(480, host.clientWidth);
  const height = Math.max(420, host.clientHeight);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const groups = state.groups.filter((group) => matches(group, state.query));
  $("dataMapEmpty").hidden = Boolean(groups.length);
  if (!groups.length) { svg.innerHTML = ""; return; }
  const cx = width / 2;
  const cy = height / 2;
  const spread = Math.min(width / 2, height / 2) - 70;
  const shapes = [];
  groups.forEach((group, index) => {
    const angle = index * GOLDEN;
    const distance = groups.length === 1 ? 0 : spread * (0.34 + 0.66 * Math.sqrt((index + 1) / groups.length));
    const gx = cx + Math.cos(angle) * distance * (width >= height ? 1.35 : 1);
    const gy = cy + Math.sin(angle) * distance * 0.82;
    const radius = 16 + Math.min(34, Math.sqrt(group.members.length + 1) * 7);
    group._pos = { x: gx, y: gy, r: radius };
    const dim = state.selected && !group.members.some((m) => m.id === state.selected) && state.selected !== group.id;
    shapes.push(`<g class="data-node kind-${esc(group.kind)} ${dim ? "dim" : ""}" data-map-group="${esc(group.id)}">
      <circle class="data-node-ring ${freshness(group.lastActivity)}" cx="${gx}" cy="${gy}" r="${radius}"></circle>
      ${group.members.map((member, memberIndex) => {
        const ma = memberIndex * GOLDEN;
        const mr = radius * (0.3 + 0.62 * Math.sqrt((memberIndex + 1) / (group.members.length + 1)));
        const mx2 = gx + Math.cos(ma) * mr;
        const my2 = gy + Math.sin(ma) * mr;
        return `<circle class="data-node-member ${freshness(member.lastActivity)} ${state.selected === member.id ? "selected" : ""}" data-map-member="${esc(member.id)}" cx="${mx2}" cy="${my2}" r="${state.selected === member.id ? 4.5 : 2.6}"></circle>`;
      }).join("")}
      <text class="data-node-label" x="${gx}" y="${gy + radius + 15}" text-anchor="middle">${esc(group.name)}</text>
    </g>`);
  });
  svg.innerHTML = shapes.join("");
}

// ── Events ───────────────────────────────────────────────────────────────

function bind() {
  $("dataTree").addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-toggle]");
    if (toggle) {
      const id = toggle.dataset.toggle;
      if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id);
      state.selected = state.selected && String(state.selected).startsWith(id) ? state.selected : null;
      renderTree(); renderMap();
      return;
    }
    const member = event.target.closest("[data-member]");
    if (member) {
      state.selected = state.selected === member.dataset.member ? null : member.dataset.member;
      // Locating a table is a live-map gesture; flip layers so the
      // highlight is actually visible.
      if (state.selected) state.layer = "map";
      renderTree(); renderMap();
    }
  });
  $("dataMapSvg").addEventListener("click", (event) => {
    const memberDot = event.target.closest("[data-map-member]");
    if (memberDot) {
      state.selected = state.selected === memberDot.dataset.mapMember ? null : memberDot.dataset.mapMember;
      const owner = state.selected
        ? state.groups.find((group) => group.members.some((member) => member.id === state.selected))
        : null;
      if (owner) state.expanded.add(owner.id);
      renderTree(); renderMap();
      const row = document.querySelector(`[data-member="${CSS.escape(state.selected || "")}"]`);
      if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
      return;
    }
    const groupNode = event.target.closest("[data-map-group]");
    if (groupNode) {
      const id = groupNode.dataset.mapGroup;
      state.expanded.add(id);
      renderTree(); renderMap();
      const section = document.querySelector(`[data-group="${CSS.escape(id)}"]`);
      if (section) section.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  });
  $("search").addEventListener("input", (event) => {
    state.query = String(event.target.value || "").trim().toLowerCase();
    renderTree(); renderMap();
  });
  $("reload").addEventListener("click", () => load());
  $("dataLayerToggle").addEventListener("click", (event) => {
    const button = event.target.closest("[data-layer]");
    if (!button) return;
    state.layer = button.dataset.layer;
    renderMap();
  });
  window.addEventListener("resize", () => renderMap());
}

// ── Load ─────────────────────────────────────────────────────────────────

async function load() {
  $("status").textContent = "Taking inventory…";
  try {
    const data = await api("/api/calliope/data-inventory");
    state.groups = buildGroups(data);
    const hadSketch = Boolean(state.sketch);
    state.sketch = data.sketch || null;
    if (state.sketch && !hadSketch) state.layer = "sketch";
    if (!state.sketch) state.layer = "map";
    const tableCount = (data.schemas || []).reduce((sum, schema) => sum + (schema.tables || []).length, 0);
    const parts = [
      `${(data.schemas || []).length} schema${(data.schemas || []).length === 1 ? "" : "s"}`,
      `${tableCount} table${tableCount === 1 ? "" : "s"}`,
      (data.services || []).length ? `${data.services.length} service${data.services.length === 1 ? "" : "s"}` : "",
      (data.integrations || []).length ? `${data.integrations.length} connected` : "",
    ].filter(Boolean);
    $("summaryLine").innerHTML = `<b>Connected data</b> · ${esc(parts.join(" · "))}`;
    const freshest = state.groups.map((g) => g.lastActivity).filter(Boolean).sort().at(-1);
    $("status").textContent = freshest ? `Live inventory · newest activity ${age(freshest)}` : "Live inventory";
    $("liveDot").className = "live-dot";
    renderTree(); renderMap();
  } catch (error) {
    $("status").textContent = error.message || "Inventory unavailable";
    $("liveDot").className = "live-dot past";
    $("dataTree").innerHTML = `<div class="data-tree-empty">${esc(error.message || "The inventory could not be read.")}</div>`;
    $("dataMapEmpty").hidden = false;
    $("dataMapSvg").innerHTML = "";
  }
}

// The rail's Callie avatar (floor.js owns this on the Floor; here the tab
// is a plain link, so pick day or night art by the local clock).
(() => {
  const img = document.querySelector("[data-floor-launch-avatar]");
  const host = img?.closest("[data-day-src]");
  if (!img || !host) return;
  const hour = new Date().getHours();
  img.src = hour >= 7 && hour < 19 ? host.dataset.daySrc : host.dataset.nightSrc;
})();

bind();
load();
})();
