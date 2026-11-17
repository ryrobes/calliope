// Desk — the Front Door shell (v0).
// Four views over existing state: Today (decisions ∪ crew ∪ edition),
// Ask (evidence search, ephemeral — nothing persists until escalated),
// Board (the Hermes kanban mirror), Gallery (links out, for now).
// Chat is a panel, not a place: the thread panel opens over any view,
// speaks the notebook's own session/turn/SSE APIs, and closing it returns
// you exactly where you were. Maximize (⛶) escalates into the notebook.
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));

  const state = { view: "today", query: "", askBusy: false };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "content-type": "application/json" },
      credentials: "same-origin",
      ...options,
    });
    if (!response.ok) throw new Error(`${path} → ${response.status}`);
    return response.json();
  }

  function ago(iso) {
    if (!iso) return "";
    const ms = Date.now() - new Date(iso).getTime();
    if (!Number.isFinite(ms)) return "";
    const minutes = Math.round(ms / 60000);
    if (minutes < 1) return "now";
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return `${hours}h`;
    return `${Math.round(hours / 24)}d`;
  }

  // ── views ─────────────────────────────────────────────────────────────
  function setView(view) {
    state.view = view;
    document.querySelectorAll("nav.views button").forEach((tab) => {
      tab.setAttribute("aria-selected", String(tab.dataset.view === view));
    });
    document.querySelectorAll(".view").forEach((panel) => {
      panel.classList.toggle("active", panel.id === `view-${view}`);
    });
  }
  document.querySelectorAll("nav.views button").forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  });

  // ── today ─────────────────────────────────────────────────────────────
  function decisionMarkup(item) {
    const kanban = item.source === "kanban";
    const src = kanban
      ? `<span class="k">card · ${esc(item.block_kind || "blocked")}</span> · ${esc(item.board || "")}/${esc(item.card_id || "")}`
      : `<span class="k">${esc(item.kind || "inbox")}</span> · ${esc(item.urgency || "normal")}`;
    const act = item.session_id
      ? `<a class="primary" href="#" data-thread-session="${esc(item.session_id)}" data-thread-kind="▣ ${esc(item.source === "kanban" ? "Card" : "Decision")}" data-thread-title="${esc(item.title)}">Open the thread</a>`
      : `<a class="primary" href="/calliope">Take it up with Calliope</a>`;
    return `<div class="decision ${kanban ? "blocked" : ""}">
      <div class="src">${src} · <span class="num">${esc(ago(item.created_at))}</span></div>
      <p><b>${esc(item.title)}</b>${item.summary ? ` — ${esc(item.summary)}` : ""}</p>
      <div class="opts">${act}</div>
    </div>`;
  }

  function storyMarkup(item) {
    return `<div class="story">
      <div class="kicker ${item.tone === "warn" ? "warn" : ""}">${esc(item.kicker || "Noted")}</div>
      <h4>${esc(item.title)}</h4>
      ${item.summary ? `<p>${esc(item.summary)}</p>` : ""}
      ${item.session_id
        ? `<a href="#" data-thread-session="${esc(item.session_id)}" data-thread-kind="◆ Edition" data-thread-title="${esc(item.title)}">Talk about this →</a>`
        : ""}
    </div>`;
  }

  function crewMarkup(row) {
    const dot = row.state === "running" ? "run" : row.state === "parked" ? "parked" : "";
    return `<div class="crewrow"><span class="dot ${dot}"></span>
      <span class="who">${esc(row.who)}</span>
      <span class="what">${esc(row.what)}</span>
      <time class="num">${esc(ago(row.at) || "")}</time></div>`;
  }

  async function loadToday() {
    $("#today-date").textContent = new Date().toLocaleDateString(undefined, {
      weekday: "long", month: "short", day: "numeric",
    });
    try {
      const data = await api("/api/calliope/desk/today");
      const decisions = data.decisions || [];
      $("#decision-count").textContent = String(decisions.length);
      $("#decisions").innerHTML = decisions.length
        ? decisions.map(decisionMarkup).join("")
        : '<p class="empty">Nothing needs you right now.</p>';
      const edition = data.edition || [];
      $("#edition").innerHTML = edition.length
        ? edition.map(storyMarkup).join("")
        : '<p class="empty">Quiet so far.</p>';
      const crew = data.crew || [];
      $("#crew").hidden = !crew.length;
      $("#crew-rows").innerHTML = crew.map(crewMarkup).join("");
    } catch (error) {
      $("#decisions").innerHTML = `<p class="empty">Today could not load: ${esc(error.message)}</p>`;
    }
  }

  // ── ask ───────────────────────────────────────────────────────────────
  const GROUP_LABELS = {
    memory: "Company memory",
    data: "Warehouse semantics",
    artifacts: "Artifacts & dashboards",
    answers: "You asked before",
  };

  function evidenceMarkup(item) {
    const title = item.url
      ? `<a href="${esc(item.url)}">${esc(item.title)}</a>`
      : esc(item.title);
    return `<div class="ev">
      <div class="type"><span>${esc(item.kind || "evidence")}${item.subtype ? " · " + esc(item.subtype) : ""}</span>
        <time class="num">${esc(item.occurred_at ? ago(item.occurred_at) : "")}</time></div>
      <h4>${title}</h4>
      ${item.summary ? `<p>${esc(item.summary).slice(0, 260)}</p>` : ""}
      <span class="prov">${esc(item.source || "")}</span>
    </div>`;
  }

  async function runSearch(query) {
    state.query = query;
    setView("ask");
    $("#ask-query").textContent = `“${query}”`;
    $("#ask-meter").textContent = "searching…";
    $("#ask-results").innerHTML = "";
    if (state.askBusy) return;
    state.askBusy = true;
    try {
      const data = await api("/api/calliope/desk/search", {
        method: "POST",
        body: JSON.stringify({ query }),
      });
      const items = data.items || [];
      $("#ask-meter").textContent =
        `${items.length} items · ${data.elapsed_ms ?? "?"} ms`;
      const groups = new Map();
      for (const item of items) {
        const key = item.group || "memory";
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
      }
      const sections = [];
      for (const asset of (data.assets || [])) {
        const promoted = asset.status === "promoted";
        const chips = (asset.metrics || []).map((metric) =>
          `<span class="mchip" title="${esc(metric.formula || "")}">${esc(metric.name)}${metric.unit ? ` · ${esc(metric.unit)}` : ""}</span>`
        ).join("");
        sections.push(`<section class="asset ${promoted ? "" : "proposed"}">
          <div class="asset-head">
            <span class="akind">Value system</span>
            <h3>${esc(asset.name)}</h3>
            <span class="gov ${promoted ? "p" : "r"}">${promoted ? "PROMOTED" : esc(String(asset.status || "proposed").toUpperCase())}</span>
            ${asset.match?.cosine ? `<span class="amatch num">match ${asset.match.cosine}</span>` : ""}
            ${asset.summary ? `<span class="outcome">${esc(asset.summary)}</span>` : ""}
          </div>
          ${chips ? `<div class="mrow">${chips}</div>` : ""}
          <div class="afoot">
            ${asset.source_relations?.length ? `<span class="prov">${esc(asset.source_relations.join(" · "))}</span>` : ""}
            <button type="button" data-thread-seed="${esc(`${asset.name}: ${query}`)}">Ask about this →</button>
          </div>
        </section>`);
      }
      if ((data.past_answers || []).length) {
        groups.set("answers", data.past_answers.map((answer) => ({
          kind: "you asked", title: answer.question,
          summary: answer.answer, occurred_at: answer.at,
          url: answer.session_id ? `/calliope?session=${answer.session_id}` : null,
          source: "from memory · instant",
        })));
      }
      for (const [key, groupItems] of groups) {
        sections.push(`<section class="bundle-group">
          <div class="bundle-head"><span class="kind">${esc(GROUP_LABELS[key] || key)}</span>
            <span class="count num">${groupItems.length}</span></div>
          <div class="evgrid">${groupItems.map(evidenceMarkup).join("")}</div>
        </section>`);
      }
      sections.push(`<div class="askbar">
        <a href="#" data-thread-seed="${esc(query)}">ASK CALLIOPE →</a></div>`);
      $("#ask-results").innerHTML = sections.join("")
        || '<p class="empty">Nothing indexed matches — Calliope can investigate live from the notebook.</p>';
      loadFrame(query, items);
    } catch (error) {
      $("#ask-meter").textContent = "";
      $("#ask-results").innerHTML = `<p class="empty">Search failed: ${esc(error.message)}</p>`;
    } finally {
      state.askBusy = false;
    }
  }

  async function loadFrame(query, items) {
    if (!items.length) return;
    try {
      const response = await fetch("/api/calliope/desk/frame", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          query,
          items: items.slice(0, 20).map((item) => ({
            group: item.group, kind: item.kind,
            title: item.title, summary: item.summary,
          })),
        }),
      });
      if (response.status !== 200) return;          // librarian is decoration
      const frame = await response.json();
      if (state.query !== query) return;            // a newer search superseded us
      const askables = (frame.askables || []).map((question) =>
        `<button type="button" data-thread-seed="${esc(question)}">${esc(question)}</button>`
      ).join("");
      const block = document.createElement("div");
      block.className = "frame";
      block.innerHTML = `${frame.framing
        ? `<p><span class="lbl">Framing</span>${esc(frame.framing)}</p>` : ""}
        ${askables ? `<div class="askables">${askables}</div>` : ""}`;
      $("#ask-results").prepend(block);
    } catch { /* framing is optional, always */ }
  }

  $("#searchform").addEventListener("submit", (event) => {
    event.preventDefault();
    const query = $("#searchinput").value.trim();
    if (query) runSearch(query);
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#searchinput").focus();
    }
  });

  // ── board ─────────────────────────────────────────────────────────────
  const COLUMNS = [
    { key: "asked", label: "Asked", statuses: ["todo"] },
    { key: "ready", label: "Ready", statuses: ["ready", "scheduled"] },
    { key: "run", label: "In motion", statuses: ["running"], cls: "run" },
    { key: "wait", label: "Waiting on you", statuses: ["blocked", "triage"], cls: "waitk", col: "wait" },
    { key: "done", label: "Done", statuses: ["done"], done: true },
  ];

  function cardMarkup(task, done) {
    const meta = [];
    if (task.status === "running" && task.run_started_at) {
      meta.push(`<span class="pulse">● ${esc(ago(task.run_started_at))} in</span>`);
    }
    if (task.block_kind) meta.push(`blocked: ${esc(task.block_kind)}`);
    if (task.child_count) meta.push(`${task.child_count} subtasks`);
    return `<div class="kcard ${task.status === "blocked" || task.status === "triage" ? "blocked" : ""} ${done ? "donec" : ""}">
      <div class="ktitle">${esc(task.title)}</div>
      ${meta.length ? `<div class="kmeta">${meta.join(" · ")}</div>` : ""}
      <div class="kfoot">
        <span class="kassign ${task.assignee ? "" : "none"}">${esc(task.assignee || "unassigned")}</span>
        <time class="num">${esc(ago(task.completed_at || task.created_at))}</time>
      </div>
    </div>`;
  }

  async function loadBoard() {
    try {
      const data = await api("/api/calliope/desk/board");
      const tasks = data.tasks || [];
      $("#board-name").textContent = data.board_label || "Board";
      $("#board-meter").textContent = tasks.length
        ? `${tasks.length} cards · mirrored ${ago(data.synced_at) || "just now"} ago`
        : "";
      if (!tasks.length) {
        $("#kanban").innerHTML =
          '<p class="empty">No work on the board yet. Cards appear here when Calliope or a specialist takes something on.</p>';
        return;
      }
      $("#kanban").innerHTML = COLUMNS.map((column) => {
        const cards = tasks.filter((task) => column.statuses.includes(task.status));
        return `<div class="kcol ${column.col || ""}">
          <div class="khead ${column.cls || ""}">${column.label} <span class="num">${cards.length}</span></div>
          ${cards.map((task) => cardMarkup(task, column.done)).join("")}
        </div>`;
      }).join("");
    } catch (error) {
      $("#kanban").innerHTML = `<p class="empty">Board could not load: ${esc(error.message)}</p>`;
    }
  }

  // ── thread panel — chat is a panel, never a place ─────────────────────
  const thread = { sessionId: null, busy: false };
  const panel = $("#threadpanel");

  function threadMsg(who, text, cls = "") {
    const el = document.createElement("div");
    el.className = "tmsg";
    el.innerHTML = `<div class="twho ${cls}">${esc(who)}</div><div class="body"></div>`;
    el.querySelector(".body").textContent = text;
    $("#thread-stream").appendChild(el);
    el.scrollIntoView({ block: "end" });
    return el;
  }

  function threadNote(text, cls = "tledger") {
    const el = document.createElement("div");
    el.className = cls;
    el.textContent = text;
    $("#thread-stream").appendChild(el);
    el.scrollIntoView({ block: "end" });
    return el;
  }

  function closeThread() {
    panel.classList.remove("open");
    document.body.classList.remove("thread-open");
    const hide = () => { panel.hidden = true; };
    matchMedia("(prefers-reduced-motion: reduce)").matches ? hide() : setTimeout(hide, 260);
  }
  $("#thread-close").addEventListener("click", closeThread);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) closeThread();
  });

  async function openThread({ sessionId = null, kind = "◆ Thread", title = "", seedQuery = null } = {}) {
    panel.hidden = false;
    requestAnimationFrame(() => panel.classList.add("open"));
    document.body.classList.add("thread-open");
    $("#thread-kind").textContent = kind;
    $("#thread-title").textContent = title;
    $("#thread-stream").innerHTML = "";
    thread.sessionId = sessionId;
    try {
      if (!sessionId) {
        const created = await api("/api/calliope/sessions", {
          method: "POST",
          body: JSON.stringify({ title: (seedQuery || title || "Desk thread").slice(0, 110), agent_key: "calliope" }),
        });
        thread.sessionId = created.session.id;
        if (seedQuery) {
          threadNote(`◆ evidence bundle attached · “${seedQuery}”`, "tcontext");
          await api(`/api/calliope/sessions/${encodeURIComponent(thread.sessionId)}/evidence-search`, {
            method: "POST",
            body: JSON.stringify({ query: seedQuery }),
          }).catch(() => {});
        }
      } else {
        const data = await api(`/api/calliope/sessions/${encodeURIComponent(sessionId)}`);
        for (const turn of (data.turns || []).slice(-8)) {
          if (turn.user_message && !String(turn.user_message).startsWith("[")) {
            threadMsg("you", turn.user_message, "you");
          }
          if (turn.assistant_message) {
            threadMsg(turn.author_display_name || "Calliope", turn.assistant_message);
          }
        }
      }
      $("#thread-notebook").href = `/calliope?session=${encodeURIComponent(thread.sessionId)}`;
      $("#thread-input").focus();
    } catch (error) {
      threadNote(`Could not open the thread: ${error.message}`, "tledger");
    }
  }

  async function sendThreadTurn(message) {
    if (!thread.sessionId || thread.busy) return;
    thread.busy = true;
    $("#thread-form .tsend").disabled = true;
    threadMsg("you", message, "you");
    const status = threadNote("Calliope is thinking…", "tstatus");
    let assistant = null;
    let toolCount = 0;
    try {
      const response = await fetch(
        `/api/calliope/sessions/${encodeURIComponent(thread.sessionId)}/turn`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ message, client_turn_id: crypto.randomUUID() }),
        },
      );
      if (!response.ok || !response.body) throw new Error(`turn → ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let eventName = "";
      const handle = (event, data) => {
        if (event === "assistant.delta") {
          if (!assistant) assistant = threadMsg("Calliope", "");
          const body = assistant.querySelector(".body");
          body.textContent += String(data.delta || "");
          assistant.scrollIntoView({ block: "end" });
        } else if (event === "assistant.completed") {
          assistant = null;
        } else if (event === "tool.started") {
          toolCount += 1;
          status.textContent = `Calliope is working · ${toolCount} tool call${toolCount === 1 ? "" : "s"}`;
        } else if (event === "calliope.progress" && data.message) {
          status.textContent = String(data.message).slice(0, 160);
        } else if (event === "calliope.specialist" && data.name) {
          threadNote(`↳ ${data.name} joined`, "tstatus");
        } else if (event === "calliope.turn.completed") {
          status.remove();
          if (toolCount) threadNote(`▸ ${toolCount} tool calls this turn`);
          loadToday();
          loadBoard();
        } else if (event === "calliope.error" || event === "error") {
          status.textContent = `Something broke: ${String(data.message || "unknown")}`;
        }
      };
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          eventName = "";
          let payload = "";
          for (const line of block.split("\n")) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            else if (line.startsWith("data:")) payload += line.slice(5).trim();
          }
          if (!eventName) continue;
          let data = {};
          try { data = JSON.parse(payload || "{}"); } catch { /* keep {} */ }
          handle(eventName, data);
        }
      }
    } catch (error) {
      status.textContent = `Turn failed: ${error.message}`;
    } finally {
      thread.busy = false;
      $("#thread-form .tsend").disabled = false;
      $("#thread-input").focus();
    }
  }

  $("#thread-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const message = $("#thread-input").value.trim();
    if (!message) return;
    $("#thread-input").value = "";
    sendThreadTurn(message);
  });
  $("#thread-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#thread-form").requestSubmit();
    }
  });

  // Delegated entry points: anything carrying data-thread-* opens the panel.
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-thread-session], [data-thread-seed]");
    if (!opener) return;
    event.preventDefault();
    if (opener.dataset.threadSession) {
      openThread({
        sessionId: opener.dataset.threadSession,
        kind: opener.dataset.threadKind || "◆ Thread",
        title: opener.dataset.threadTitle || "",
      });
    } else {
      openThread({
        kind: "◆ From your search",
        title: `“${opener.dataset.threadSeed}”`,
        seedQuery: opener.dataset.threadSeed,
      });
    }
  });

  // ── boot ──────────────────────────────────────────────────────────────
  loadToday();
  loadBoard();
  setInterval(loadToday, 60_000);
  setInterval(loadBoard, 45_000);
  const params = new URLSearchParams(window.location.search);
  if (params.get("q")) {
    $("#searchinput").value = params.get("q");
    runSearch(params.get("q"));
  } else if (params.get("view")) {
    setView(params.get("view"));
  }
})();
