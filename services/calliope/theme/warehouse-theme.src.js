import { Vibrant } from "node-vibrant/browser";

/*
 * DataRabbit's image-theme pipeline, adapted to the framework-free Warehouse:
 *
 *   image -> node-vibrant role swatches -> ImagePalette -> dark/light UI tokens
 *
 * Library choices keep a stable source URL in localStorage.  Custom uploads
 * keep the actual Blob in IndexedDB, while their compact palette/tokens live in
 * localStorage so colors can be restored synchronously in <head>.  The script
 * is deliberately included only by first-party Warehouse shells.
 */

const STORAGE_KEY = "rvbbit-warehouse-theme-v1";
const INHERITANCE_STORAGE_KEY = "rvbbit-warehouse-appearance-origin-v1";
const WORKSPACE_SETTINGS_URL = "/api/calliope/admin/workspace";
const MODE_STORAGE_KEY = "rvbbit-warehouse-color-mode";
// Kept only so the authenticated hydration pass can remove the old,
// ownerless cache. It is never applied to a signed-in identity.
const VOICE_STORAGE_KEY = "rvbbit-calliope-voice-v1";
const VOICE_OWNER_CACHE_PREFIX = "rvbbit-calliope-response-preferences-v1:";
const BACKGROUND_BRIDGE_KEY = "rvbbit-warehouse-background-bridge-v1";
const ADAPTIVE_SNAPSHOT_KEY = "rvbbit-warehouse-adaptive-theme-v1";
const SERVER_APPEARANCE_URL = "/api/calliope/appearance";
const SERVER_RESPONSE_PREFERENCES_URL = "/api/calliope/response-preferences";
const DB_NAME = "rvbbit-warehouse-browser";
const DB_VERSION = 1;
const DB_STORE = "appearance";
const DB_KEY = "theme:default";
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const LIBRARY_URL = /^(?:\/theme\/images\/full\/[A-Za-z0-9][A-Za-z0-9_-]{0,160}\.webp|\/api\/desktop\/wallpapers\/[A-Za-z0-9._%+-]{1,220}\?variant=(?:1080|1440|original))$/;
const BACKGROUND_BRIDGE_URL = /^(?:\/bg\/[A-Za-z0-9_-]{1,80}\.jpg|\/theme\/images\/full\/[A-Za-z0-9][A-Za-z0-9_-]{0,160}\.webp|\/api\/desktop\/wallpapers\/[A-Za-z0-9._%+-]{1,220}\?variant=(?:1080|1440|original))$/;
const SOLID_COLOR = /^#[0-9a-f]{6}$/i;
const ROOT = document.documentElement;
const SUN_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3.5"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42"></path></svg>';
const MOON_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.2 15.3A8.5 8.5 0 0 1 8.7 3.8 8.5 8.5 0 1 0 20.2 15.3Z"></path></svg>';

const THEME_KEYS = [
  "--background",
  "--secondary-background",
  "--foreground",
  "--border",
  "--ring",
  "--overlay",
  "--main",
  "--main-foreground",
  "--chart-1",
  "--chart-2",
  "--chart-3",
  "--chart-4",
  "--chart-5",
  "--chart-6",
  "--success",
  "--warning",
  "--danger",
  "--info",
  "--chrome-bg",
  "--chrome-border",
  "--chrome-text",
  "--doc-bg",
  "--block-bg",
  "--block-bg-hover",
  "--block-border",
  "--grid-dot",
  "--grid-outline",
  "--rvbbit-accent",
  "--rvbbit-bg",
  "--wallpaper-overlay-from",
  "--wallpaper-overlay-to",
  "--ambient-1",
  "--ambient-2",
  "--ambient-3",
  "--void",
  "--panel",
  "--panel-raised",
  "--panel-soft",
  "--bone",
  "--bone-bright",
  "--fog",
  "--dim",
  "--faint",
  "--line",
  "--line-hot",
  "--amber",
  "--amber-soft",
  "--jade",
  "--jade-soft",
  "--warehouse-wallpaper",
  "--warehouse-solid-background",
];

let colorMode = readColorMode();
let voicePreferenceOwner = "";
let voice = defaultVoicePreferences();
let appliedTokens = null;
let current = readStoredState();
let selectedBackgroundMode = themeBackground(current).mode;
let selectedSolidColor = themeBackground(current).solidColor;
let solidColorTouched = Boolean(current?.background?.solidColor);
let previewWallpaperUrl = null;
let renderedPreviewBackgroundImage = "";
let previewImageSequence = 0;
let activeObjectUrl = null;
let button = null;
let modeButton = null;
let dialog = null;
let library = null;
let selectedItem = null;
let selectedPalette = null;
let selectedPalettePromise = null;
let previewSequence = 0;
let voicePersonalityTimer = null;
let serverAppearanceHydration = null;
let appearanceSource = "builtin";
let personalMode = null;
let appearanceReady = false;
let appearanceRevision = 0;
let pickerScope = "personal";
let workspaceDraft = null;
let workspaceLoading = false;
let serverResponsePreferenceHydration = null;
let serverResponsePreferenceSaves = Promise.resolve();
let voicePreferenceChangeRevision = 0;
let serverResponsePreferencesDirty = false;
const adaptiveSnapshotFallbackUpdatedAt = new Date().toISOString();

applyColorModeRoot();
applyBackgroundBridgeRoot(preferredBackgroundBridge(current, readBackgroundBridge()));
applyStoredState(current);

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}

function init() {
  captureBackgroundBridge();
  settleBackgroundBridge();
  installAccountControls();
  voicePreferenceOwner = responsePreferenceOwnerFromDom();
  voice = readVoicePreferences();
  installButton();
  installDialog();
  refreshButton();
  persistAdaptiveSnapshot();
  if (current?.source?.kind === "upload") {
    void restoreUploadImage(current);
  }
  window.addEventListener("storage", onStorage);
  window.addEventListener("focus", hydrateServerPreferences);
  document.addEventListener("visibilitychange", onAppearanceVisibility);
  window.addEventListener("pagehide", captureBackgroundBridge);
  window.addEventListener("pagehide", flushVoicePersonalityPreference);
  window.addEventListener("pagehide", releaseObjectUrl);
  window.WarehouseTheme = Object.freeze({
    open: () => openDialog(),
    openWorkspace: openWorkspaceDialog,
    reset: resetTheme,
    getState: () => current,
    getMode: () => colorMode,
    getVoice: () => ({ ...voice }),
    refreshVoice: hydrateServerResponsePreferences,
    getSnapshot: getAdaptiveSnapshot,
    setMode: (mode) => setColorMode(mode),
    // The reading mode is worth reaching without opening a dialog, so the
    // Calliope page can cycle it from the chat header. Persistence, the server
    // sync and the warehouse-voice-change broadcast all stay here.
    setVoiceMode: (mode) => { setVoiceMode(mode); return { ...voice }; },
    cycleVoiceMode: () => {
      setVoiceMode(voice.mode === "off" ? "expressive" : "off");
      return { ...voice };
    },
  });
  void hydrateServerPreferences();
}

function onAppearanceVisibility() {
  if (document.visibilityState === "visible") void hydrateServerPreferences();
}

function hydrateServerPreferences() {
  // Public demo pages have no appearance or voice preferences to load.
  if (window.RVBBIT_DEMO_GUEST) return;
  void hydrateServerAppearance();
  void hydrateServerResponsePreferences();
}

async function saveServerAppearance(state, mode = personalMode) {
  const response = await fetch(SERVER_APPEARANCE_URL, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ appearance: state, mode }),
    keepalive: true,
  });
  if (!response.ok) throw new Error(`Appearance sync failed (${response.status})`);
}

async function saveServerUpload(file) {
  const response = await fetch(`${SERVER_APPEARANCE_URL}/image`, {
    method: "PUT",
    headers: {
      "content-type": file.type || "application/octet-stream",
      "x-rvbbit-filename": encodeURIComponent(file.name || "wallpaper"),
    },
    body: file,
  });
  if (!response.ok) throw new Error(`Wallpaper sync failed (${response.status})`);
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (!value || typeof value !== "object") return JSON.stringify(value);
  return `{${Object.keys(value).sort().map((key) => (
    `${JSON.stringify(key)}:${stableJson(value[key])}`
  )).join(",")}}`;
}

function appearanceFingerprint(state) {
  if (!state || typeof state !== "object") return "";
  const { updatedAt: _updatedAt, ...semantic } = state;
  return stableJson(semantic);
}

function hydrateServerAppearance() {
  if (serverAppearanceHydration) return serverAppearanceHydration;
  serverAppearanceHydration = hydrateServerAppearanceOnce().finally(() => {
    serverAppearanceHydration = null;
  });
  return serverAppearanceHydration;
}

async function hydrateServerAppearanceOnce() {
  const revision = appearanceRevision;
  let response;
  try {
    response = await fetch(SERVER_APPEARANCE_URL, { cache: "no-store" });
  } catch {
    return;
  }
  if (!response.ok) return;
  const remote = await response.json().catch(() => null);
  if (!remote || typeof remote !== "object" || revision !== appearanceRevision) return;
  const remoteMode = remote.mode === "light" || remote.mode === "dark" ? remote.mode : null;
  const remoteState = remote.appearance && remote.appearance.version === 1
    && validStoredSource(remote.appearance.source)
    && remote.appearance.tokens && typeof remote.appearance.tokens === "object"
    ? remote.appearance
    : null;
  if (!remoteMode) return;
  appearanceSource = remote.source || (remoteState ? "personal" : "builtin");
  personalMode = remote.personal_mode === "light" || remote.personal_mode === "dark" ? remote.personal_mode : null;
  appearanceReady = true;
  try { localStorage.setItem(INHERITANCE_STORAGE_KEY, JSON.stringify({ source: appearanceSource, owner: remote.owner, personalMode })); } catch {}
  const unchanged = appearanceFingerprint(remoteState) === appearanceFingerprint(current)
    && remoteMode === colorMode;
  if (unchanged && !(remoteState?.source?.kind === "upload" && remote.has_upload && !activeObjectUrl)) {
    return;
  }
  colorMode = remoteMode;
  current = remoteState;
  try {
    localStorage.setItem(MODE_STORAGE_KEY, colorMode);
    if (current) localStorage.setItem(STORAGE_KEY, JSON.stringify(current));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // The authenticated preference remains canonical when this cache is blocked.
  }
  applyColorModeRoot();
  // Replace tokens/background synchronously. Never expose the stock fallback
  // while an upload or stale browser cache is being reconciled.
  applyStoredState(current, activeObjectUrl);
  if (current?.source?.kind === "upload" && remote.has_upload) {
    try {
      const imageResponse = await fetch(`${SERVER_APPEARANCE_URL}/image`, { cache: "no-store" });
      if (imageResponse.ok) {
        const blob = await imageResponse.blob();
        const file = new File([blob], current.source.name || "wallpaper", {
          type: blob.type || "image/png",
        });
        await saveUploadBlob(file);
        const previousObjectUrl = activeObjectUrl;
        activeObjectUrl = URL.createObjectURL(file);
        applyStoredState(current, activeObjectUrl);
        if (previousObjectUrl) URL.revokeObjectURL(previousObjectUrl);
      }
    } catch {
      // Derived colors still apply if the optional image cannot be restored.
    }
  } else if (!current || current.source?.kind !== "upload") {
    releaseObjectUrl();
    void clearUploadBlob().catch(() => {});
  }
  refreshButton();
  refreshModeButton();
  if (!dialog?.open) { showCurrentPreview(); renderLibrary(); }
  notifyThemeChange();
}

function hydrateServerResponsePreferences() {
  if (serverResponsePreferenceHydration) return serverResponsePreferenceHydration;
  serverResponsePreferenceHydration = hydrateServerResponsePreferencesOnce().finally(() => {
    serverResponsePreferenceHydration = null;
  });
  return serverResponsePreferenceHydration;
}

async function hydrateServerResponsePreferencesOnce() {
  await serverResponsePreferenceSaves.catch(() => null);
  if (serverResponsePreferencesDirty) {
    const saved = await syncServerResponsePreferences().catch(() => null);
    if (!saved || serverResponsePreferencesDirty) return;
  }
  const startingRevision = voicePreferenceChangeRevision;
  let response;
  try {
    response = await fetch(SERVER_RESPONSE_PREFERENCES_URL, { cache: "no-store" });
  } catch {
    return;
  }
  if (!response.ok) return;
  const remote = await response.json().catch(() => null);
  const owner = String(remote?.owner || "").trim().toLowerCase();
  if (!owner || (voicePreferenceOwner && owner !== voicePreferenceOwner)) return;
  voicePreferenceOwner = owner;
  if (voicePreferenceChangeRevision !== startingRevision) {
    cacheVoicePreferences();
    void syncServerResponsePreferences().catch(() => {});
    return;
  }

  let next;
  if (remote.exists === true && remote.preferences) {
    next = browserResponsePreferences(remote.preferences);
  } else {
    // Owner-keyed cache can safely repopulate a restored database. The old
    // unscoped key cannot: it has no proof of which human authored its text.
    const seed = readCachedVoicePreferences(owner) || defaultVoicePreferences();
    const created = await saveServerResponsePreferences(seed, { ifAbsent: true })
      .catch(() => null);
    next = created?.preferences
      ? browserResponsePreferences(created.preferences)
      : seed;
  }
  voice = normalizeVoicePreferences(next);
  cacheVoicePreferences();
  try {
    localStorage.removeItem(VOICE_STORAGE_KEY);
  } catch {
    // The owner-scoped server preference is still authoritative.
  }
  announceVoicePreferences();
}

function installAccountControls() {
  document.querySelectorAll("[data-warehouse-account]").forEach((account) => {
    const trigger = account.querySelector(".warehouse-account-trigger");
    const menu = account.querySelector(".warehouse-account-menu");
    if (!(trigger instanceof HTMLButtonElement) || !(menu instanceof HTMLElement)) return;

    const menuItems = () => [...menu.querySelectorAll('[role="menuitem"]')]
      .filter((item) => item instanceof HTMLElement);
    const setOpen = (open, focusItem = false, focusLast = false) => {
      trigger.setAttribute("aria-expanded", String(open));
      menu.hidden = !open;
      if (open && focusItem) {
        const items = menuItems();
        items[focusLast ? items.length - 1 : 0]?.focus();
      }
    };
    const isOpen = () => trigger.getAttribute("aria-expanded") === "true";

    trigger.addEventListener("click", () => setOpen(!isOpen()));
    trigger.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      setOpen(true, true, event.key === "ArrowUp");
    });
    menu.addEventListener("keydown", (event) => {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      const items = menuItems();
      if (!items.length) return;
      event.preventDefault();
      const current = items.indexOf(document.activeElement);
      let next = 0;
      if (event.key === "End") next = items.length - 1;
      else if (event.key === "ArrowUp") next = current <= 0 ? items.length - 1 : current - 1;
      else if (event.key === "ArrowDown") {
        next = current < 0 || current === items.length - 1 ? 0 : current + 1;
      }
      items[next].focus();
    });
    account.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !isOpen()) return;
      event.preventDefault();
      setOpen(false);
      trigger.focus();
    });
    account.addEventListener("focusout", () => {
      window.setTimeout(() => {
        if (!account.contains(document.activeElement)) setOpen(false);
      }, 0);
    });
    document.addEventListener("pointerdown", (event) => {
      if (isOpen() && !account.contains(event.target)) setOpen(false);
    });

    account.querySelectorAll(".warehouse-account-avatar-image").forEach((image) => {
      if (!(image instanceof HTMLImageElement)) return;
      const avatar = image.closest(".warehouse-account-avatar");
      const reveal = () => {
        if (image.naturalWidth <= 0) return;
        image.classList.add("is-ready");
        avatar?.classList.add("has-image");
      };
      const reject = () => {
        image.classList.remove("is-ready");
        avatar?.classList.remove("has-image");
        image.remove();
      };
      image.addEventListener("load", reveal, { once: true });
      image.addEventListener("error", reject, { once: true });
      if (image.complete) {
        if (image.naturalWidth > 0) reveal();
        else reject();
      }
    });
  });
}

function normalizedBackgroundBridge(value) {
  const url = String(value?.url || "");
  if (!BACKGROUND_BRIDGE_URL.test(url)) return null;
  const rawOpacity = Number(value?.opacity);
  return {
    url,
    opacity: Number.isFinite(rawOpacity) ? clamp(rawOpacity, .18, 1) : .58,
  };
}

function readBackgroundBridge() {
  try {
    return normalizedBackgroundBridge(JSON.parse(sessionStorage.getItem(BACKGROUND_BRIDGE_KEY) || "null"));
  } catch {
    return null;
  }
}

function preferredBackgroundBridge(state, previous) {
  if (state && themeBackground(state).mode === "solid") return null;
  if (state?.source?.kind === "library") {
    return normalizedBackgroundBridge({
      url: state.source.url,
      opacity: previous?.url === state.source.url ? previous.opacity : .64,
    });
  }
  return previous;
}

function applyBackgroundBridgeRoot(value) {
  const bridge = normalizedBackgroundBridge(value);
  if (!bridge) return;
  ROOT.style.setProperty("--warehouse-bridge-wallpaper", cssUrl(bridge.url));
  ROOT.style.setProperty("--warehouse-bridge-opacity", String(bridge.opacity));
  ROOT.dataset.warehouseBackgroundBridge = "active";
}

function captureBackgroundBridge() {
  if (current && themeBackground(current).mode === "solid") {
    try {
      sessionStorage.removeItem(BACKGROUND_BRIDGE_KEY);
    } catch {
      // A solid theme already supplies its own synchronous first paint.
    }
    return;
  }
  const backdrop = document.querySelector(".warehouse-desktop-background");
  const fallback = {
    url: backdrop?.dataset.warehouseBackgroundUrl,
    opacity: backdrop?.dataset.warehouseBackgroundOpacity,
  };
  const bridge = preferredBackgroundBridge(current, normalizedBackgroundBridge(fallback));
  if (!bridge) return;
  try {
    sessionStorage.setItem(BACKGROUND_BRIDGE_KEY, JSON.stringify(bridge));
  } catch {
    // Navigation still works normally when browser storage is unavailable.
  }
}

function settleBackgroundBridge() {
  if (!ROOT.dataset.warehouseBackgroundBridge) return;
  const backdrop = document.querySelector(".warehouse-desktop-background");
  const currentUrl = current?.source?.kind === "library" && themeBackground(current).mode === "image"
    ? current.source.url
    : backdrop?.dataset.warehouseBackgroundUrl;
  if (!BACKGROUND_BRIDGE_URL.test(String(currentUrl || ""))) {
    clearBackgroundBridgeRoot();
    return;
  }
  const image = new Image();
  let settled = false;
  const settle = () => {
    if (settled) return;
    settled = true;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      ROOT.dataset.warehouseBackgroundBridge = "settling";
      window.setTimeout(clearBackgroundBridgeRoot, 240);
    }));
  };
  image.addEventListener("load", settle, { once: true });
  image.addEventListener("error", settle, { once: true });
  image.src = currentUrl;
  if (image.complete) settle();
}

function clearBackgroundBridgeRoot() {
  delete ROOT.dataset.warehouseBackgroundBridge;
  ROOT.style.removeProperty("--warehouse-bridge-wallpaper");
  ROOT.style.removeProperty("--warehouse-bridge-opacity");
}

function installButton() {
  const anchor = document.querySelector("[data-warehouse-theme-anchor]");
  const host = anchor || document.createElement("span");
  if (!anchor) {
    host.className = "warehouse-theme-fallback";
    document.body.append(host);
  }
  button = document.createElement("button");
  button.type = "button";
  button.className = "warehouse-theme-button";
  button.title = "Calliope appearance";
  button.setAttribute("aria-label", "Choose Calliope appearance");
  button.setAttribute("aria-haspopup", "dialog");
  button.setAttribute("aria-expanded", "false");
  button.innerHTML = '<span class="warehouse-theme-button-thumb" aria-hidden="true"></span>';
  button.addEventListener("click", openDialog);

  modeButton = document.createElement("button");
  modeButton.type = "button";
  modeButton.className = "warehouse-theme-mode-button";
  modeButton.addEventListener("click", toggleColorMode);
  refreshModeButton();
  host.append(button, modeButton);
}

function readColorMode() {
  try {
    return localStorage.getItem(MODE_STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

// Two settings: Callie's take (presentation, on by default) and read aloud (mode:
// off or expressive; "fast" was a second kind of take and now means on).
function defaultVoicePreferences() {
  return {
    version: 3,
    mode: "off",
    personality: "",
    progress: false,
    presentation: true,
  };
}

function normalizeVoicePreferences(value) {
  const mode = ["fast", "expressive"].includes(value?.mode) ? "expressive" : "off";
  const personality = typeof value?.personality === "string"
    ? value.personality.replace(/[\u0000-\u001f\u007f]/g, " ").slice(0, 600)
    : "";
  return {
    version: 3,
    mode,
    personality,
    progress: value?.progress === true,
    presentation: value?.presentation === true,
  };
}

function responsePreferenceOwnerFromDom() {
  const account = document.querySelector("[data-warehouse-account]");
  return String(
    account?.dataset.warehouseOwner
      || account?.querySelector(".warehouse-account-email")?.textContent
      || "",
  ).trim().toLowerCase();
}

function responsePreferenceCacheKey(owner = voicePreferenceOwner) {
  const normalized = String(owner || "").trim().toLowerCase();
  return normalized ? `${VOICE_OWNER_CACHE_PREFIX}${encodeURIComponent(normalized)}` : "";
}

function readCachedVoicePreferences(owner = voicePreferenceOwner) {
  const key = responsePreferenceCacheKey(owner);
  if (!key) return null;
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    if (!value || typeof value !== "object") return null;
    // Caches from before Callie's take became the default hold its old "off".
    return normalizeVoicePreferences(value.version === 3 ? value : { ...value, presentation: true });
  } catch {
    return null;
  }
}

function readVoicePreferences() {
  return readCachedVoicePreferences() || defaultVoicePreferences();
}

function serverResponsePreferences(value = voice) {
  const normalized = normalizeVoicePreferences(value);
  return {
    version: 1,
    conversational_answers: normalized.presentation,
    read_aloud_mode: normalized.mode,
    response_personality: normalized.personality,
    speak_working_updates: normalized.progress,
  };
}

function browserResponsePreferences(value) {
  return normalizeVoicePreferences({
    presentation: value?.conversational_answers,
    mode: value?.read_aloud_mode,
    personality: value?.response_personality,
    progress: value?.speak_working_updates,
  });
}

function cacheVoicePreferences() {
  const key = responsePreferenceCacheKey();
  if (!key) return;
  try {
    localStorage.setItem(key, JSON.stringify(voice));
  } catch {
    // The authenticated server preference remains canonical when caching is blocked.
  }
}

function announceVoicePreferences() {
  renderVoicePreferences();
  window.dispatchEvent(new CustomEvent("warehouse-voice-change", {
    detail: { ...voice },
  }));
}

function saveServerResponsePreferences(value = voice, { ifAbsent = false } = {}) {
  if (!voicePreferenceOwner) return Promise.resolve(null);
  const requestedOwner = voicePreferenceOwner;
  const payload = {
    preferences: serverResponsePreferences(value),
    if_absent: ifAbsent,
  };
  const save = async () => {
    const response = await fetch(SERVER_RESPONSE_PREFERENCES_URL, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    });
    if (!response.ok) throw new Error(`Response preference sync failed (${response.status})`);
    const result = await response.json();
    return requestedOwner === voicePreferenceOwner ? result : null;
  };
  serverResponsePreferenceSaves = serverResponsePreferenceSaves
    .catch(() => null)
    .then(save);
  return serverResponsePreferenceSaves;
}

function syncServerResponsePreferences(value = voice) {
  const revision = voicePreferenceChangeRevision;
  return saveServerResponsePreferences(value).then((result) => {
    if (result && revision === voicePreferenceChangeRevision) {
      serverResponsePreferencesDirty = false;
    }
    return result;
  });
}

function stageVoicePreferenceChange({ announce = true } = {}) {
  voice = normalizeVoicePreferences(voice);
  voicePreferenceChangeRevision += 1;
  serverResponsePreferencesDirty = true;
  cacheVoicePreferences();
  if (announce) announceVoicePreferences();
  else renderVoicePreferences();
}

function persistVoicePreferences() {
  stageVoicePreferenceChange();
  void syncServerResponsePreferences().catch(() => {});
}

function setVoiceMode(mode) {
  voice.mode = ["fast", "expressive"].includes(mode) ? "expressive" : "off";
  persistVoicePreferences();
}

function onVoiceProgress(event) {
  voice.progress = event.currentTarget?.checked === true;
  persistVoicePreferences();
}

function onVoicePresentation(event) {
  voice.presentation = event.currentTarget?.checked === true;
  persistVoicePreferences();
}

function onVoicePersonality(event) {
  voice.personality = String(event.currentTarget?.value || "").slice(0, 600);
  stageVoicePreferenceChange({ announce: false });
  clearTimeout(voicePersonalityTimer);
  voicePersonalityTimer = window.setTimeout(() => {
    voicePersonalityTimer = null;
    announceVoicePreferences();
    void syncServerResponsePreferences().catch(() => {});
  }, 220);
}

function flushVoicePersonalityPreference() {
  if (!voicePersonalityTimer) return;
  clearTimeout(voicePersonalityTimer);
  voicePersonalityTimer = null;
  announceVoicePreferences();
  void syncServerResponsePreferences().catch(() => {});
}

function renderVoicePreferences() {
  if (!dialog) return;
  // Read aloud plays Callie's take, so it needs the take on.
  const take = voice.presentation === true;
  const speaking = take && voice.mode !== "off";
  dialog.querySelectorAll("[data-theme-voice-mode]").forEach((control) => {
    const active = control.dataset.themeVoiceMode === (speaking ? "expressive" : "off");
    control.setAttribute("aria-pressed", String(active));
    control.disabled = !take;
  });
  const personality = dialog.querySelector("[data-theme-voice-personality]");
  if (personality && personality.value !== voice.personality) {
    personality.value = voice.personality;
  }
  if (personality) personality.disabled = !take;
  const count = dialog.querySelector("[data-theme-voice-count]");
  if (count) count.textContent = `${voice.personality.length}/600`;
  const section = dialog.querySelector("[data-theme-voice]");
  if (section) {
    section.dataset.mode = speaking ? "expressive" : "off";
    section.dataset.presentation = take ? "on" : "off";
  }
  const presentation = dialog.querySelector("[data-theme-voice-presentation]");
  if (presentation) presentation.checked = take;
  const audioNote = dialog.querySelector("[data-theme-audio-note]");
  if (audioNote) audioNote.hidden = take;
  const progress = dialog.querySelector("[data-theme-voice-progress]");
  if (progress) {
    progress.checked = voice.progress === true;
    progress.disabled = !speaking;
  }
}

function applyColorModeRoot() {
  ROOT.dataset.theme = colorMode;
  ROOT.dataset.warehouseColorMode = colorMode;
  ROOT.style.colorScheme = colorMode;
  ROOT.classList.toggle("dark", colorMode === "dark");
}

function toggleColorMode() {
  setColorMode(colorMode === "dark" ? "light" : "dark");
}

function setColorMode(mode, { persist = true } = {}) {
  const next = mode === "light" ? "light" : "dark";
  if (next === colorMode) {
    refreshModeButton();
    return colorMode;
  }
  colorMode = next;
  if (persist) {
    try {
      localStorage.setItem(MODE_STORAGE_KEY, colorMode);
    } catch {
      // The mode still applies for this page when persistence is blocked.
    }
    personalMode = colorMode;
    appearanceRevision += 1;
    void saveServerAppearance(undefined, personalMode).then(async () => {
      await serverAppearanceHydration;
      await hydrateServerAppearance();
    }).catch(() => {});
  }
  applyColorModeRoot();
  applyStoredState(current, activeObjectUrl);
  refreshButton();
  refreshModeButton();
  if (!dialog?.open) showCurrentPreview();
  notifyThemeChange();
  return colorMode;
}

function refreshModeButton() {
  if (!modeButton) return;
  const toLight = colorMode === "dark";
  const label = toLight ? "Switch to light mode" : "Switch to dark mode";
  modeButton.innerHTML = toLight ? SUN_ICON : MOON_ICON;
  modeButton.title = label;
  modeButton.setAttribute("aria-label", label);
  modeButton.setAttribute("aria-pressed", String(colorMode === "light"));
  modeButton.dataset.mode = colorMode;
}

function installDialog() {
  dialog = document.createElement("dialog");
  dialog.className = "warehouse-theme-dialog";
  dialog.setAttribute("aria-labelledby", "warehouse-theme-title");
  dialog.innerHTML = `
    <div class="warehouse-theme-shell">
      <header class="warehouse-theme-head">
        <div>
          <p class="warehouse-theme-kicker">Your settings</p>
          <h2 id="warehouse-theme-title">Appearance & responses</h2>
        </div>
        <button class="warehouse-theme-close" type="button" aria-label="Close appearance picker">×</button>
      </header>
      <div class="warehouse-theme-content">
        <section class="warehouse-theme-library" aria-label="Theme image library">
          <div class="warehouse-theme-empty" data-theme-status>Loading image library…</div>
          <div class="warehouse-theme-grid" data-theme-grid hidden></div>
        </section>
        <aside class="warehouse-theme-preview">
          <div class="warehouse-theme-preview-image" data-theme-preview-image>
            <span class="warehouse-theme-preview-status" data-theme-preview-status
                  role="status" aria-live="polite" hidden></span>
          </div>
          <h3 data-theme-preview-title>Current Calliope background</h3>
          <div class="warehouse-theme-swatches" data-theme-swatches aria-label="Derived color palette"></div>
          <div class="warehouse-theme-background">
            <div class="warehouse-theme-background-head">
              <b>Background</b>
            </div>
            <div class="warehouse-theme-background-modes" aria-label="Page background style">
              <button type="button" data-theme-background-mode="image" aria-pressed="true">Wallpaper</button>
              <button type="button" data-theme-background-mode="solid" aria-pressed="false">Solid color</button>
            </div>
            <label class="warehouse-theme-color" data-theme-solid-picker hidden>
              <span>Background color</span>
              <input type="color" data-theme-solid-color value="#100d0b">
              <output data-theme-solid-value>#100d0b</output>
            </label>
          </div>
          <label class="warehouse-workspace-mode" data-workspace-mode-row hidden>
            <span>Default color mode</span>
            <select data-workspace-mode aria-label="Default color mode"><option value="dark">Dark</option><option value="light">Light</option></select>
            <small>Used when someone hasn’t chosen light or dark mode.</small>
          </label>
          <section class="warehouse-theme-voice" data-theme-voice data-mode="off" data-presentation="on">
            <div class="warehouse-theme-background-head">
              <b>Responses</b>
            </div>
            <label class="warehouse-theme-presentation">
              <input type="checkbox" data-theme-voice-presentation>
              <span><b>Callie’s take</b><i data-theme-presentation-hint>Shorter, in her own words. The full answer stays one click away</i></span>
            </label>
            <label class="warehouse-theme-voice-personality">
              <span>Personality <output data-theme-voice-count>0/600</output></span>
              <textarea data-theme-voice-personality maxlength="600" rows="2"
                placeholder="Warm, concise, slightly playful…"></textarea>
            </label>
            <div class="warehouse-theme-voice-subhead">
              <b>Read aloud</b>
            </div>
            <div class="warehouse-theme-voice-modes" role="group" aria-label="Read aloud">
              <button type="button" data-theme-voice-mode="off" aria-pressed="true">Off</button>
              <button type="button" data-theme-voice-mode="expressive" aria-pressed="false" title="Read Callie’s take aloud">On</button>
            </div>
            <p data-theme-audio-note hidden>Turn on Callie’s take to hear it.</p>
            <label class="warehouse-theme-voice-progress" title="Read stable progress updates aloud while Callie works">
              <input type="checkbox" data-theme-voice-progress>
              <span><b>Read progress updates</b></span>
            </label>
          </section>
          <div class="warehouse-theme-error" data-theme-error hidden></div>
          <div class="warehouse-theme-actions">
            <label class="warehouse-theme-upload">
              <input type="file" accept="image/*,.avif,.jpg,.jpeg,.png,.webp">
              Upload image
            </label>
            <button class="warehouse-theme-action" type="button" data-theme-reset>Use workspace default</button>
            <button class="warehouse-theme-action primary" type="button" data-theme-apply disabled>Apply</button>
          </div>
          <p class="warehouse-theme-footnote">Appearance is cached here and synced to your account. Published HTML and JavaScript artifacts keep their own styling.</p>
        </aside>
      </div>
      <footer class="warehouse-workspace-footer" hidden></footer>
    </div>`;
  document.body.append(dialog);

  dialog.querySelector(".warehouse-theme-close").addEventListener("click", closeDialog);
  dialog.querySelector("[data-theme-reset]").addEventListener("click", () => void (pickerScope === "workspace" ? saveWorkspaceTheme(null) : resetTheme()));
  dialog.querySelector("[data-theme-apply]").addEventListener("click", () => void applySelectedTheme());
  dialog.querySelector('input[type="file"]').addEventListener("change", onUpload);
  dialog.querySelectorAll("[data-theme-background-mode]").forEach((control) => {
    control.addEventListener("click", () => setBackgroundMode(control.dataset.themeBackgroundMode));
  });
  dialog.querySelector("[data-theme-solid-color]").addEventListener("input", onSolidColor);
  dialog.querySelectorAll("[data-theme-voice-mode]").forEach((control) => {
    control.addEventListener("click", () => setVoiceMode(control.dataset.themeVoiceMode));
  });
  dialog.querySelector("[data-theme-voice-presentation]").addEventListener("change", onVoicePresentation);
  dialog.querySelector("[data-theme-voice-personality]").addEventListener("input", onVoicePersonality);
  dialog.querySelector("[data-theme-voice-progress]").addEventListener("change", onVoiceProgress);
  dialog.querySelector("[data-workspace-mode]").addEventListener("change", () => {
    workspaceDraft.mode = dialog.querySelector("[data-workspace-mode]").value;
    dialog.querySelector("[data-theme-apply]").disabled = false;
  });
  renderVoicePreferences();
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog();
  });
}

function pickerCurrent() { return pickerScope === "workspace" ? workspaceDraft?.appearance : current; }

async function openWorkspaceDialog() {
  if (workspaceLoading) return;
  workspaceLoading = true;
  try {
    const response = await fetch(WORKSPACE_SETTINGS_URL, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || "Workspace settings require administrator access.");
    workspaceDraft = data;
    openDialog("workspace");
  } finally { workspaceLoading = false; }
}

async function saveWorkspaceTheme(appearance) {
  setBusy(true); setError("");
  try {
    const response = await fetch(WORKSPACE_SETTINGS_URL, {
      method: "PUT", headers: { "Content-Type": "application/json", "X-Calliope-Admin": "1" },
      body: JSON.stringify({ appearance: appearance ? { ...appearance, tokens: deriveWarehouseTokens(appearance.palette, workspaceDraft?.mode || "dark") } : null, mode: workspaceDraft?.mode || "dark" }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || "Could not save workspace settings.");
    workspaceDraft = data;
    closeDialog();
    await hydrateServerAppearance();
  } catch (error) { setError(error.message || "Could not save workspace settings."); }
  finally { setBusy(false); }
}

function openDialog(scope = "personal") {
  if (!dialog) return;
  pickerScope = scope;
  const workspace = scope === "workspace";
  dialog.dataset.scope = scope;
  const actions = dialog.querySelector(".warehouse-theme-actions");
  const footer = dialog.querySelector(".warehouse-workspace-footer");
  footer.hidden = !workspace;
  if (workspace) footer.append(actions);
  else dialog.querySelector(".warehouse-theme-preview").insertBefore(actions, dialog.querySelector(".warehouse-theme-footnote"));
  dialog.querySelector(".warehouse-theme-kicker").textContent = workspace ? "Workspace settings · Appearance" : "Your settings";
  dialog.querySelector("#warehouse-theme-title").textContent = workspace ? "A default room for everyone" : "Appearance & responses";
  dialog.querySelector("[data-theme-voice]").hidden = workspace;
  dialog.querySelector(".warehouse-theme-upload").hidden = workspace;
  dialog.querySelector("[data-workspace-mode-row]").hidden = !workspace;
  dialog.querySelector("[data-workspace-mode]").value = workspaceDraft?.mode || "dark";
  dialog.querySelector("[data-theme-reset]").textContent = workspace ? "Use built-in background" : "Use workspace default";
  dialog.querySelector("[data-theme-apply]").textContent = workspace ? "Save workspace default" : "Apply";
  dialog.querySelector(".warehouse-theme-footnote").textContent = workspace
    ? "Shared default. Personal choices take priority."
    : "Saved to your account, across devices.";
  button?.setAttribute("aria-expanded", "true");
  setError("");
  dialog.showModal();
  renderVoicePreferences();
  showCurrentPreview();
  if (library) {
    renderLibrary();
  } else {
    void loadLibrary();
  }
}

function closeDialog() {
  if (dialog?.open) dialog.close();
  button?.setAttribute("aria-expanded", "false");
}

async function loadLibrary() {
  const status = dialog.querySelector("[data-theme-status]");
  try {
    const response = await fetch("/theme/library", { credentials: "same-origin" });
    if (!response.ok) throw new Error(`Image library failed (${response.status})`);
    const payload = await response.json();
    library = Array.isArray(payload.items) ? payload.items.filter(validLibraryItem) : [];
    renderLibrary();
  } catch (error) {
    status.hidden = false;
    status.textContent = error instanceof Error ? error.message : "Image library unavailable.";
  }
}

function validLibraryItem(item) {
  return Boolean(
    item
    && /^[A-Za-z0-9][A-Za-z0-9_-]{0,160}$/.test(String(item.id || ""))
    && LIBRARY_URL.test(String(item.url || ""))
    && /^\/theme\/images\/thumb\/[A-Za-z0-9][A-Za-z0-9_-]{0,160}\.webp$/.test(String(item.thumb || "")),
  );
}

function renderLibrary() {
  const status = dialog.querySelector("[data-theme-status]");
  const grid = dialog.querySelector("[data-theme-grid]");
  status.hidden = Boolean(library?.length);
  grid.hidden = !library?.length;
  if (!library?.length) {
    status.textContent = "No bundled images are available.";
    return;
  }
  const activeId = selectedItem?.id || (pickerCurrent()?.source?.kind === "library" ? pickerCurrent().source.id : "");
  grid.innerHTML = library.map((item) => `
    <button class="warehouse-theme-tile" type="button" data-theme-id="${escapeHtml(item.id)}"
            aria-pressed="${String(item.id === activeId)}" title="${escapeHtml(item.label)}">
      <img src="${escapeHtml(item.thumb)}" alt="" loading="lazy" decoding="async">
      <span>${escapeHtml(item.label)}</span>
    </button>`).join("");
  grid.querySelectorAll("[data-theme-id]").forEach((tile) => {
    tile.addEventListener("click", () => selectLibraryItem(tile.dataset.themeId));
  });
}

function renderLibrarySelection(activeId) {
  const grid = dialog.querySelector("[data-theme-grid]");
  grid.querySelectorAll("[data-theme-id]").forEach((tile) => {
    tile.setAttribute("aria-pressed", String(tile.dataset.themeId === activeId));
  });
}

function selectLibraryItem(id) {
  const item = library?.find((candidate) => candidate.id === id);
  if (!item) return;
  selectedItem = item;
  selectedPalette = pickerCurrent()?.source?.kind === "library" && pickerCurrent().source.id === item.id
    ? pickerCurrent().palette
    : null;
  if (!solidColorTouched && selectedPalette) {
    selectedSolidColor = derivedSolidColor(selectedPalette);
  }
  renderLibrarySelection(item.id);
  renderPreview(item.url, item.label, selectedPalette);
  syncBackgroundControls();
  const apply = dialog.querySelector("[data-theme-apply]");
  apply.disabled = false;
  apply.textContent = pickerScope === "workspace" ? "Save workspace default" : "Apply";
  setPreviewStatus(selectedPalette ? "" : "Deriving colors…");
  const sequence = ++previewSequence;
  selectedPalettePromise = selectedPalette
    ? Promise.resolve(selectedPalette)
    : extractPalette(item.url).then((palette) => ({ ...palette, source: item.label }));
  selectedPalettePromise.then((palette) => {
    if (sequence !== previewSequence || selectedItem?.id !== item.id) return;
    selectedPalette = palette;
    if (!solidColorTouched) selectedSolidColor = derivedSolidColor(palette);
    renderSwatches(palette);
    syncBackgroundControls();
    setPreviewStatus("");
  }).catch((error) => {
    if (sequence !== previewSequence) return;
    selectedPalettePromise = null;
    setPreviewStatus("");
    setError(error instanceof Error ? error.message : "Could not derive colors from that image.");
  });
}

async function applySelectedTheme() {
  if (!selectedItem && !pickerCurrent()) {
    if (pickerScope === "workspace") await saveWorkspaceTheme(null);
    return;
  }
  const apply = dialog.querySelector("[data-theme-apply]");
  setBusy(true);
  setError("");
  try {
    let next;
    let uploadUrl = activeObjectUrl;
    if (selectedItem) {
      const palette = selectedPalette || await (
        selectedPalettePromise || extractPalette(selectedItem.url)
      );
      next = {
        version: 1,
        source: {
          kind: "library",
          id: selectedItem.id,
          label: selectedItem.label,
          url: selectedItem.url,
        },
        palette: { ...palette, source: selectedItem.label },
        tokens: deriveWarehouseTokens(palette),
        background: backgroundChoice(),
        updatedAt: new Date().toISOString(),
      };
      if (pickerScope !== "workspace") await clearUploadBlob().catch(() => {});
      uploadUrl = null;
    } else {
      next = {
        ...pickerCurrent(),
        background: backgroundChoice(),
        updatedAt: new Date().toISOString(),
      };
      if (
        next.source.kind === "upload"
        && selectedBackgroundMode === "image"
        && !uploadUrl
      ) {
        const blob = await loadUploadBlob();
        if (blob) {
          releaseObjectUrl();
          activeObjectUrl = URL.createObjectURL(blob);
          uploadUrl = activeObjectUrl;
        }
      }
    }
    if (pickerScope === "workspace") await saveWorkspaceTheme(next);
    else { await persistAndApply(next, uploadUrl); closeDialog(); }
  } catch (error) {
    setError(error instanceof Error ? error.message : "Could not apply that image.");
  } finally {
    setBusy(false);
    apply.textContent = pickerScope === "workspace" ? "Save workspace default" : "Apply";
  }
}

async function onUpload(event) {
  if (pickerScope === "workspace") return;
  const input = event.currentTarget;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    setError("Choose an image file.");
    return;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    setError("That image is larger than 20 MB.");
    return;
  }
  setBusy(true);
  setError("");
  let url = null;
  try {
    url = URL.createObjectURL(file);
    const palette = await extractPalette(url);
    await saveUploadBlob(file);
    await saveServerUpload(file);
    const next = {
      version: 1,
      source: { kind: "upload", name: file.name },
      palette: { ...palette, source: file.name },
      tokens: deriveWarehouseTokens(palette),
      background: backgroundChoice(),
      updatedAt: new Date().toISOString(),
    };
    releaseObjectUrl();
    activeObjectUrl = url;
    url = null;
    await persistAndApply(next, activeObjectUrl);
    closeDialog();
  } catch (error) {
    setError(error instanceof Error ? error.message : "Could not save that image.");
  } finally {
    if (url) URL.revokeObjectURL(url);
    setBusy(false);
  }
}

async function resetTheme() {
  setBusy(true); setError("");
  try {
    await hydrateServerAppearance();
    appearanceRevision += 1;
    await saveServerAppearance(null, personalMode);
    await clearUploadBlob().catch(() => {});
    releaseObjectUrl();
    appearanceSource = "workspace";
    await hydrateServerAppearance();
    refreshButton(); showCurrentPreview(); renderLibrary(); notifyThemeChange();
    closeDialog();
  } catch (error) { setError(error.message || "Could not restore the workspace default."); }
  finally { setBusy(false); }
}

async function persistAndApply(next, uploadUrl = null) {
  if (!appearanceReady) await hydrateServerAppearance();
  appearanceRevision += 1;
  await saveServerAppearance(next, personalMode);
  appearanceSource = "personal";
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch {}
  current = next;
  applyStoredState(next, uploadUrl);
  refreshButton();
  notifyThemeChange();
}

function readStoredState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (!parsed || parsed.version !== 1 || !validStoredSource(parsed.source)) return null;
    if (!parsed.tokens || typeof parsed.tokens !== "object") return null;
    return parsed;
  } catch {
    return null;
  }
}

function validStoredSource(source) {
  if (source?.kind === "upload") return typeof source.name === "string";
  return source?.kind === "library"
    && /^[A-Za-z0-9][A-Za-z0-9_-]{0,160}$/.test(String(source.id || ""))
    && LIBRARY_URL.test(String(source.url || ""));
}

function validStoredPalette(palette) {
  if (!palette || !Number.isFinite(Number(palette.baseHue)) || !Number.isFinite(Number(palette.chroma))) {
    return false;
  }
  return ["vibrant", "darkVibrant", "lightVibrant"].every((key) => (
    typeof palette[key] === "string" && palette[key].length <= 180
  ));
}

function applyStoredState(state, uploadUrl = null) {
  const tokens = state && validStoredPalette(state.palette)
    ? deriveWarehouseTokens(state.palette, colorMode)
    : state?.tokens || null;
  appliedTokens = tokens;
  for (const key of THEME_KEYS) {
    if (key === "--warehouse-wallpaper" || key === "--warehouse-solid-background") continue;
    const value = tokens?.[key];
    if (typeof value === "string" && value.length <= 180) {
      ROOT.style.setProperty(key, value);
    } else {
      ROOT.style.removeProperty(key);
    }
  }
  if (!state) {
    ROOT.style.removeProperty("--warehouse-wallpaper");
    ROOT.style.removeProperty("--warehouse-solid-background");
    delete ROOT.dataset.warehouseTheme;
    delete ROOT.dataset.warehouseWallpaper;
    delete ROOT.dataset.warehouseBackground;
    return;
  }
  const background = themeBackground(state);
  ROOT.style.setProperty("--warehouse-solid-background", background.solidColor);
  ROOT.dataset.warehouseBackground = background.mode;
  const wallpaper = state.source.kind === "library" ? state.source.url : uploadUrl;
  if (background.mode === "image" && wallpaper) {
    setWallpaper(wallpaper);
  } else {
    ROOT.style.removeProperty("--warehouse-wallpaper");
    delete ROOT.dataset.warehouseWallpaper;
  }
  ROOT.dataset.warehouseTheme = state.source.kind;
}

function clearAppliedTheme() {
  for (const key of THEME_KEYS) ROOT.style.removeProperty(key);
  appliedTokens = null;
  delete ROOT.dataset.warehouseTheme;
  delete ROOT.dataset.warehouseWallpaper;
  delete ROOT.dataset.warehouseBackground;
}

function setWallpaper(url) {
  ROOT.style.setProperty("--warehouse-wallpaper", cssUrl(url));
  ROOT.dataset.warehouseWallpaper = "true";
}

function cssUrl(url) {
  const escaped = String(url).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
  return `url("${escaped}")`;
}

async function restoreUploadImage(state) {
  try {
    const blob = await loadUploadBlob();
    if (!blob || current !== state) return;
    releaseObjectUrl();
    activeObjectUrl = URL.createObjectURL(blob);
    if (themeBackground(state).mode === "image") setWallpaper(activeObjectUrl);
    refreshButton();
    notifyThemeChange();
  } catch {
    // Keep the derived colors. The reset action can clear a stale record.
  }
}

function releaseObjectUrl() {
  if (!activeObjectUrl) return;
  URL.revokeObjectURL(activeObjectUrl);
  activeObjectUrl = null;
}

function refreshButton() {
  if (!button) return;
  button.dataset.hasTheme = String(Boolean(current));
  button.dataset.backgroundMode = current ? themeBackground(current).mode : "default";
  const thumb = button.querySelector(".warehouse-theme-button-thumb");
  if (thumb) {
    thumb.style.backgroundImage = "";
    if (!current) {
      const fallback = document.querySelector(".bg");
      const fallbackImage = fallback ? getComputedStyle(fallback).backgroundImage : "";
      if (fallbackImage && fallbackImage !== "none") {
        thumb.style.backgroundImage = fallbackImage;
      }
    }
  }
  button.title = current
    ? `Appearance · ${current.source.label || current.source.name || "custom image"} · ${
      themeBackground(current).mode === "solid" ? "solid background" : "wallpaper"
    }`
    : "Calliope appearance";
}

function showCurrentPreview() {
  if (!dialog) return;
  setPreviewStatus("");
  selectedItem = pickerCurrent()?.source?.kind === "library" && library
    ? library.find((item) => item.id === pickerCurrent().source.id) || null
    : null;
  selectedPalette = pickerCurrent()?.palette || null;
  selectedPalettePromise = selectedPalette ? Promise.resolve(selectedPalette) : null;
  const background = themeBackground(pickerCurrent());
  selectedBackgroundMode = background.mode;
  selectedSolidColor = background.solidColor;
  solidColorTouched = Boolean(pickerCurrent()?.background?.solidColor);
  const url = pickerCurrent()?.source?.kind === "library" ? pickerCurrent().source.url : activeObjectUrl;
  const label = pickerCurrent()?.source?.label || pickerCurrent()?.source?.name || "Current Calliope background";
  renderPreview(url, label, pickerCurrent()?.palette || null);
  if (!url && selectedBackgroundMode === "image") {
    const fallback = document.querySelector(".bg");
    const fallbackImage = fallback ? getComputedStyle(fallback).backgroundImage : "";
    if (fallbackImage && fallbackImage !== "none") {
      previewImageSequence += 1;
      renderedPreviewBackgroundImage = fallbackImage;
      dialog.querySelector("[data-theme-preview-image]").setAttribute("aria-busy", "false");
    }
  }
  dialog.querySelector("[data-theme-reset]").disabled = pickerScope === "workspace" ? !pickerCurrent() : appearanceSource !== "personal";
  dialog.querySelector("[data-theme-apply]").disabled = !selectedItem && !pickerCurrent();
  syncBackgroundControls();
}

function renderPreview(url, label, palette) {
  previewWallpaperUrl = url || null;
  dialog.querySelector("[data-theme-preview-title]").textContent = label;
  renderSwatches(palette);
  preparePreviewWallpaper(previewWallpaperUrl);
  syncBackgroundControls();
}

function setPreviewStatus(message) {
  const status = dialog?.querySelector("[data-theme-preview-status]");
  if (!status) return;
  status.hidden = !message;
  status.textContent = message;
}

function previewBackgroundImage(url) {
  return url ? `url("${String(url).replaceAll('"', '\\"')}")` : "";
}

function preparePreviewWallpaper(url) {
  const preview = dialog.querySelector("[data-theme-preview-image]");
  const sequence = ++previewImageSequence;
  if (!url) {
    renderedPreviewBackgroundImage = "";
    preview.setAttribute("aria-busy", "false");
    return;
  }
  const nextBackgroundImage = previewBackgroundImage(url);
  if (nextBackgroundImage === renderedPreviewBackgroundImage) {
    preview.setAttribute("aria-busy", "false");
    return;
  }
  preview.setAttribute("aria-busy", "true");
  const candidate = new Image();
  const commit = () => {
    if (sequence !== previewImageSequence || previewWallpaperUrl !== url) return;
    renderedPreviewBackgroundImage = nextBackgroundImage;
    preview.setAttribute("aria-busy", "false");
    syncBackgroundControls();
  };
  candidate.decoding = "async";
  candidate.addEventListener("load", () => {
    if (typeof candidate.decode === "function") {
      void candidate.decode().catch(() => {}).then(commit);
    } else {
      commit();
    }
  }, { once: true });
  candidate.addEventListener("error", () => {
    if (sequence === previewImageSequence) preview.setAttribute("aria-busy", "false");
  }, { once: true });
  candidate.src = url;
}

function renderSwatches(palette) {
  const row = dialog.querySelector("[data-theme-swatches]");
  if (!palette) {
    row.innerHTML = "";
    return;
  }
  row.innerHTML = [
    palette.vibrant,
    palette.darkVibrant,
    palette.lightVibrant,
    palette.muted,
    palette.darkMuted,
    palette.lightMuted,
  ].filter(Boolean).map((color) => (
    `<span class="warehouse-theme-swatch" style="--swatch:${escapeHtml(color)}"></span>`
  )).join("");
}

function setBackgroundMode(mode) {
  if (!selectedItem && !current) return;
  if (mode !== "image" && mode !== "solid") return;
  selectedBackgroundMode = mode;
  if (mode === "solid" && !solidColorTouched) {
    selectedSolidColor = derivedSolidColor(selectedPalette || current?.palette);
  }
  syncBackgroundControls();
}

function onSolidColor(event) {
  const value = String(event.currentTarget.value || "").toLowerCase();
  if (!SOLID_COLOR.test(value)) return;
  selectedSolidColor = value;
  solidColorTouched = true;
  syncBackgroundControls();
}

function syncBackgroundControls() {
  if (!dialog) return;
  const available = Boolean(selectedItem || pickerCurrent());
  dialog.querySelectorAll("[data-theme-background-mode]").forEach((control) => {
    const active = control.dataset.themeBackgroundMode === selectedBackgroundMode;
    control.setAttribute("aria-pressed", String(active));
    control.disabled = !available;
  });
  const picker = dialog.querySelector("[data-theme-solid-picker]");
  picker.hidden = selectedBackgroundMode !== "solid";
  const input = dialog.querySelector("[data-theme-solid-color]");
  input.value = selectedSolidColor;
  input.disabled = !available;
  dialog.querySelector("[data-theme-solid-value]").textContent = selectedSolidColor;

  const preview = dialog.querySelector("[data-theme-preview-image]");
  preview.dataset.backgroundMode = selectedBackgroundMode;
  if (selectedBackgroundMode === "solid") {
    preview.style.backgroundImage = "none";
    preview.style.backgroundColor = selectedSolidColor;
  } else {
    preview.style.backgroundColor = "";
    preview.style.backgroundImage = renderedPreviewBackgroundImage;
  }
}

function backgroundChoice() {
  return {
    mode: selectedBackgroundMode === "solid" ? "solid" : "image",
    solidColor: SOLID_COLOR.test(selectedSolidColor)
      ? selectedSolidColor.toLowerCase()
      : derivedSolidColor(selectedPalette || pickerCurrent()?.palette),
  };
}

function themeBackground(state) {
  const solidColor = String(state?.background?.solidColor || "").toLowerCase();
  return {
    mode: state?.background?.mode === "solid" ? "solid" : "image",
    solidColor: SOLID_COLOR.test(solidColor)
      ? solidColor
      : derivedSolidColor(state?.palette),
  };
}

function adaptiveThemeTokens() {
  const computed = getComputedStyle(ROOT);
  const tokens = {};
  for (const key of THEME_KEYS) {
    if (key === "--warehouse-wallpaper" || key === "--warehouse-solid-background") continue;
    const value = String(computed.getPropertyValue(key) || appliedTokens?.[key] || "").trim();
    if (value && value.length <= 240 && !/[<>{};]/.test(value)) tokens[key] = value;
  }
  return tokens;
}

function adaptiveBackdrop(includeTransient = true) {
  const background = current ? themeBackground(current) : {
    mode: ROOT.dataset.warehouseBackground === "solid" ? "solid" : "image",
    solidColor: String(
      getComputedStyle(ROOT).getPropertyValue("--warehouse-solid-background")
      || getComputedStyle(ROOT).getPropertyValue("--background")
      || "#100d0b",
    ).trim(),
  };
  const backdrop = document.querySelector(".warehouse-desktop-background");
  const bridge = readBackgroundBridge();
  let wallpaper = null;
  let opacity = Number(backdrop?.dataset.warehouseBackgroundOpacity || bridge?.opacity || .64);
  if (background.mode === "image") {
    if (current?.source?.kind === "library") wallpaper = current.source.url;
    else if (current?.source?.kind === "upload") wallpaper = includeTransient ? activeObjectUrl : null;
    else wallpaper = backdrop?.dataset.warehouseBackgroundUrl || bridge?.url || null;
  }
  if (!Number.isFinite(opacity)) opacity = .64;
  return {
    mode: background.mode,
    solid_color: SOLID_COLOR.test(background.solidColor) ? background.solidColor : "#100d0b",
    wallpaper: wallpaper && (BACKGROUND_BRIDGE_URL.test(wallpaper) || wallpaper.startsWith("blob:"))
      ? wallpaper
      : null,
    wallpaper_opacity: clamp(opacity, .18, 1),
    upload: current?.source?.kind === "upload" && background.mode === "image",
  };
}

function adaptiveThemeSnapshot({ includeTransient = true } = {}) {
  return {
    schema_version: "rvbbit.viewer-theme.v1",
    mode: colorMode,
    source: {
      kind: current?.source?.kind || "default",
      id: current?.source?.id || null,
      label: current?.source?.label || current?.source?.name || "Calliope default",
    },
    tokens: adaptiveThemeTokens(),
    background: adaptiveBackdrop(includeTransient),
    material: {
      glass_background: "color-mix(in oklch, var(--panel) 78%, transparent)",
      glass_border: "color-mix(in oklch, var(--bone) 15%, transparent)",
      backdrop_blur: "18px",
      shadow: colorMode === "light"
        ? "0 20px 64px rgba(38,42,48,.18)"
        : "0 20px 64px rgba(0,0,0,.34)",
    },
    // A snapshot read is not a theme mutation. Keep the fallback timestamp
    // stable for this document so merely asking for the current theme cannot
    // wake every direct dashboard tab through the storage event channel.
    updated_at: current?.updatedAt || adaptiveSnapshotFallbackUpdatedAt,
  };
}

function adaptiveSnapshotFingerprint(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return "";
  const { updated_at: _updatedAt, ...semantic } = snapshot;
  return stableJson(semantic);
}

function persistAdaptiveSnapshot(snapshot = null) {
  try {
    const candidate = snapshot || adaptiveThemeSnapshot({ includeTransient: false });
    const previousRaw = localStorage.getItem(ADAPTIVE_SNAPSHOT_KEY);
    if (previousRaw) {
      try {
        const previous = JSON.parse(previousRaw);
        if (adaptiveSnapshotFingerprint(previous) === adaptiveSnapshotFingerprint(candidate)) {
          return false;
        }
      } catch {
        // Replace corrupt or legacy snapshots with the canonical current shape.
      }
    }
    localStorage.setItem(ADAPTIVE_SNAPSHOT_KEY, JSON.stringify(candidate));
    return true;
  } catch {
    // Adaptive artifacts still receive the in-memory snapshot through Calliope.
    return false;
  }
}

async function getAdaptiveSnapshot() {
  if (
    current?.source?.kind === "upload"
    && themeBackground(current).mode === "image"
    && !activeObjectUrl
  ) {
    const blob = await loadUploadBlob().catch(() => null);
    if (blob) {
      releaseObjectUrl();
      activeObjectUrl = URL.createObjectURL(blob);
      setWallpaper(activeObjectUrl);
      refreshButton();
    }
  }
  const snapshot = adaptiveThemeSnapshot();
  persistAdaptiveSnapshot(snapshot);
  return snapshot;
}

function derivedSolidColor(palette) {
  const hue = Number.isFinite(Number(palette?.baseHue))
    ? Number(palette.baseHue)
    : 28;
  return colorMode === "light"
    ? hslToHex(hue, 18, 94)
    : hslToHex(hue, 22, 7);
}

function hslToHex(hue, saturation, lightness) {
  const h = ((Number(hue) % 360) + 360) % 360;
  const s = Math.max(0, Math.min(100, Number(saturation))) / 100;
  const l = Math.max(0, Math.min(100, Number(lightness))) / 100;
  const chroma = (1 - Math.abs(2 * l - 1)) * s;
  const segment = h / 60;
  const x = chroma * (1 - Math.abs((segment % 2) - 1));
  const [r1, g1, b1] = segment < 1 ? [chroma, x, 0]
    : segment < 2 ? [x, chroma, 0]
      : segment < 3 ? [0, chroma, x]
        : segment < 4 ? [0, x, chroma]
          : segment < 5 ? [x, 0, chroma]
            : [chroma, 0, x];
  const m = l - chroma / 2;
  return `#${[r1, g1, b1].map((part) => (
    Math.round((part + m) * 255).toString(16).padStart(2, "0")
  )).join("")}`;
}

function setBusy(busy) {
  dialog.querySelector(".warehouse-theme-upload").setAttribute("aria-disabled", String(busy));
  dialog.querySelector("[data-theme-reset]").disabled = busy || (pickerScope === "workspace" ? !pickerCurrent() : appearanceSource !== "personal");
  dialog.querySelector("[data-theme-apply]").disabled = busy || (pickerScope !== "workspace" && !selectedItem && !pickerCurrent());
  dialog.querySelector('input[type="file"]').disabled = busy;
  dialog.querySelectorAll("[data-theme-background-mode]").forEach((control) => {
    control.disabled = busy || (!selectedItem && !pickerCurrent());
  });
  dialog.querySelector("[data-theme-solid-color]").disabled = busy || (!selectedItem && !pickerCurrent());
}

function setError(message) {
  if (!dialog) return;
  const error = dialog.querySelector("[data-theme-error]");
  error.hidden = !message;
  error.textContent = message;
}

function onStorage(event) {
  if ([STORAGE_KEY, MODE_STORAGE_KEY, INHERITANCE_STORAGE_KEY].includes(event.key)) {
    void hydrateServerAppearance();
  }
  if (event.key === MODE_STORAGE_KEY) {
    setColorMode(readColorMode(), { persist: false });
    return;
  }
  if (event.key === responsePreferenceCacheKey()) {
    voice = readVoicePreferences();
    renderVoicePreferences();
    window.dispatchEvent(new CustomEvent("warehouse-voice-change", {
      detail: { ...voice },
    }));
    return;
  }
  if (event.key !== STORAGE_KEY) return;
  const next = readStoredState();
  if (appearanceFingerprint(next) === appearanceFingerprint(current)) return;
  const previousObjectUrl = activeObjectUrl;
  current = next;
  applyStoredState(current, activeObjectUrl);
  if (!current || current.source?.kind !== "upload") {
    activeObjectUrl = null;
    if (previousObjectUrl) URL.revokeObjectURL(previousObjectUrl);
  }
  refreshButton();
  showCurrentPreview();
  renderLibrary();
  if (current?.source?.kind === "upload") void restoreUploadImage(current);
  notifyThemeChange();
}

function notifyThemeChange() {
  const snapshot = adaptiveThemeSnapshot();
  persistAdaptiveSnapshot();
  window.dispatchEvent(new CustomEvent("warehouse-theme-change", {
    detail: {
      source: current?.source || null,
      tokens: appliedTokens,
      background: current ? themeBackground(current) : null,
      mode: colorMode,
      snapshot,
    },
  }));
}

async function extractPalette(input) {
  const palette = await Vibrant.from(input).getPalette();
  return paletteToImagePalette(palette);
}

function paletteToImagePalette(palette) {
  const vibrant = firstNonNull(
    palette.Vibrant,
    palette.LightVibrant,
    palette.Muted,
    palette.LightMuted,
    palette.DarkVibrant,
    palette.DarkMuted,
  );
  if (!vibrant) {
    return {
      vibrant: "oklch(76% 0.14 195)",
      darkVibrant: "oklch(40% 0.10 195)",
      lightVibrant: "oklch(82% 0.10 195)",
      muted: "oklch(40% 0.03 270)",
      darkMuted: "oklch(15% 0.04 270)",
      lightMuted: "oklch(75% 0.02 270)",
      baseHue: 270,
      chroma: .04,
      source: "node-vibrant fallback",
      generatedAt: new Date().toISOString(),
    };
  }
  const darkVibrant = palette.DarkVibrant || palette.DarkMuted || darken(vibrant);
  const lightVibrant = palette.LightVibrant || palette.LightMuted || lighten(vibrant);
  const muted = palette.Muted || palette.LightMuted || palette.DarkMuted || vibrant;
  const darkMuted = palette.DarkMuted || darken(muted);
  const lightMuted = palette.LightMuted || lighten(muted);
  const baseSwatch = pickMostPopulous([
    palette.Vibrant,
    palette.DarkVibrant,
    palette.LightVibrant,
    palette.Muted,
  ]) || vibrant;
  const baseHcl = rgbToOklch(...baseSwatch.rgb);
  const chromaSamples = [
    palette.Vibrant,
    palette.LightVibrant,
    palette.DarkVibrant,
  ].filter(Boolean).map((swatch) => rgbToOklch(...swatch.rgb).c);
  const chroma = chromaSamples.length
    ? chromaSamples.reduce((sum, value) => sum + value, 0) / chromaSamples.length
    : baseHcl.c;

  return {
    vibrant: swatchToOklch(vibrant),
    darkVibrant: swatchToOklch(darkVibrant),
    lightVibrant: swatchToOklch(lightVibrant),
    muted: swatchToOklch(muted),
    darkMuted: swatchToOklch(darkMuted),
    lightMuted: swatchToOklch(lightMuted),
    baseHue: Math.round(baseHcl.h),
    chroma: round(chroma, 4),
    source: "node-vibrant",
    generatedAt: new Date().toISOString(),
  };
}

function deriveWarehouseTokens(palette, mode = colorMode) {
  return mode === "light"
    ? deriveLightWarehouseTokens(palette)
    : deriveDarkWarehouseTokens(palette);
}

function deriveDarkWarehouseTokens(palette) {
  // DataRabbit's dark palette logic, narrowed to the tokens the Warehouse
  // shells and native chart surfaces consume.
  const main = palette.vibrant;
  const rvbbit = palette.lightVibrant;
  const seriesChroma = clamp(palette.chroma * 6, .12, .20);
  const background = oklch(13, .035, palette.baseHue);
  const secondaryBackground = oklch(18, .04, palette.baseHue);
  const foreground = oklch(92, 0, 0);
  const border = oklch(40, .03, palette.baseHue);
  const chromeBg = oklch(11, .034, palette.baseHue);
  const chromeBorder = oklch(30, .03, palette.baseHue);
  const chromeText = oklch(70, 0, 0);
  const docBg = oklch(12, .035, palette.baseHue);
  const blockBg = oklch(17, .035, palette.baseHue);
  const blockBgHover = oklch(20, .04, palette.baseHue);
  const blockBorder = oklch(32, .03, palette.baseHue);
  const chart1 = main;
  const chart2 = oklch(72, seriesChroma * .9, (palette.baseHue + 90) % 360);
  const chart3 = oklch(80, seriesChroma * .8, (palette.baseHue + 180) % 360);
  const chart4 = oklch(70, seriesChroma, (palette.baseHue + 240) % 360);
  const chart5 = oklch(72, seriesChroma * .85, (palette.baseHue + 320) % 360);
  const chart6 = oklch(74, seriesChroma * .95, (palette.baseHue + 290) % 360);

  return {
    "--background": background,
    "--secondary-background": secondaryBackground,
    "--foreground": foreground,
    "--border": border,
    "--ring": main,
    "--overlay": oklch(0, 0, 0, .7),
    "--main": main,
    "--main-foreground": oklch(0, 0, 0),
    "--chart-1": chart1,
    "--chart-2": chart2,
    "--chart-3": chart3,
    "--chart-4": chart4,
    "--chart-5": chart5,
    "--chart-6": chart6,
    "--success": "oklch(72% 0.19 145)",
    "--warning": "oklch(80% 0.16 85)",
    "--danger": "oklch(65% 0.22 25)",
    "--info": "oklch(70% 0.14 240)",
    "--chrome-bg": chromeBg,
    "--chrome-border": chromeBorder,
    "--chrome-text": chromeText,
    "--doc-bg": docBg,
    "--block-bg": blockBg,
    "--block-bg-hover": blockBgHover,
    "--block-border": blockBorder,
    "--grid-dot": oklch(22, .03, palette.baseHue),
    "--grid-outline": oklch(22, .03, palette.baseHue),
    "--rvbbit-accent": rvbbit,
    "--rvbbit-bg": oklch(17, .025, hueOf(rvbbit) ?? palette.baseHue),
    "--wallpaper-overlay-from": oklch(4, .02, palette.baseHue, .78),
    "--wallpaper-overlay-to": oklch(14, .06, palette.baseHue, .45),
    "--ambient-1": oklch(72, .16, palette.baseHue, .08),
    "--ambient-2": oklch(80, .16, (palette.baseHue + 120) % 360, .05),
    "--ambient-3": oklch(70, .18, (palette.baseHue + 240) % 360, .05),
    "--void": background,
    "--panel": blockBg,
    "--panel-raised": blockBgHover,
    "--panel-soft": "color-mix(in oklch, var(--panel) 74%, transparent)",
    "--bone": foreground,
    "--bone-bright": oklch(98, .006, palette.baseHue),
    "--fog": "color-mix(in oklch, var(--bone) 60%, transparent)",
    "--dim": "color-mix(in oklch, var(--bone) 34%, transparent)",
    "--faint": "color-mix(in oklch, var(--bone) 16%, transparent)",
    "--line": "color-mix(in oklch, var(--bone) 13%, transparent)",
    "--line-hot": "color-mix(in oklch, var(--main) 48%, transparent)",
    "--amber": main,
    "--amber-soft": "color-mix(in oklch, var(--main) 12%, transparent)",
    "--jade": rvbbit,
    "--jade-soft": "color-mix(in oklch, var(--rvbbit-accent) 10%, transparent)",
  };
}

function deriveLightWarehouseTokens(palette) {
  // Keep the selected room's identity in accents and visualization colors,
  // while moving structural surfaces to DataRabbit's warm-paper light branch.
  const main = palette.darkVibrant;
  const rvbbit = palette.darkVibrant;
  const seriesChroma = clamp(palette.chroma * 7, .13, .18);
  const background = oklch(96, .008, 95);
  const secondaryBackground = oklch(92, .012, 95);
  const foreground = oklch(21, .015, 260);
  const border = oklch(45, .02, 255);
  const chromeBg = oklch(91, .01, 95);
  const chromeBorder = oklch(73, .015, 250);
  const chromeText = oklch(39, .012, 255);
  const docBg = oklch(98, .003, 95);
  const blockBg = oklch(99, .004, 95);
  const blockBgHover = oklch(94, .012, hueOf(main) ?? palette.baseHue);
  const blockBorder = oklch(76, .016, 250);
  const chart1 = main;
  const chart2 = oklch(58, seriesChroma * .92, (palette.baseHue + 90) % 360);
  const chart3 = oklch(66, seriesChroma * .82, (palette.baseHue + 180) % 360);
  const chart4 = oklch(60, seriesChroma, (palette.baseHue + 240) % 360);
  const chart5 = oklch(60, seriesChroma * .92, (palette.baseHue + 320) % 360);
  const chart6 = oklch(64, seriesChroma * .96, (palette.baseHue + 290) % 360);

  return {
    "--background": background,
    "--secondary-background": secondaryBackground,
    "--foreground": foreground,
    "--border": border,
    "--ring": main,
    "--overlay": oklch(18, .01, 260, .14),
    "--main": main,
    "--main-foreground": oklch(99, 0, 0),
    "--chart-1": chart1,
    "--chart-2": chart2,
    "--chart-3": chart3,
    "--chart-4": chart4,
    "--chart-5": chart5,
    "--chart-6": chart6,
    "--success": "oklch(58% 0.17 145)",
    "--warning": "oklch(71% 0.16 85)",
    "--danger": "oklch(58% 0.21 25)",
    "--info": "oklch(58% 0.14 240)",
    "--chrome-bg": chromeBg,
    "--chrome-border": chromeBorder,
    "--chrome-text": chromeText,
    "--doc-bg": docBg,
    "--block-bg": blockBg,
    "--block-bg-hover": blockBgHover,
    "--block-border": blockBorder,
    "--grid-dot": oklch(85, .015, 240),
    "--grid-outline": oklch(82, .015, 245),
    "--rvbbit-accent": rvbbit,
    "--rvbbit-bg": oklch(94, .03, hueOf(rvbbit) ?? palette.baseHue),
    "--wallpaper-overlay-from": oklch(94, .01, palette.baseHue, .62),
    "--wallpaper-overlay-to": oklch(86, .03, palette.baseHue, .35),
    "--ambient-1": oklch(76, .10, palette.baseHue, .18),
    "--ambient-2": oklch(82, .12, (palette.baseHue + 120) % 360, .14),
    "--ambient-3": oklch(72, .14, (palette.baseHue + 240) % 360, .10),
    "--void": background,
    "--panel": blockBg,
    "--panel-raised": blockBgHover,
    "--panel-soft": "color-mix(in oklch, var(--panel) 80%, transparent)",
    "--bone": foreground,
    "--bone-bright": oklch(15, .012, 260),
    "--fog": "color-mix(in oklch, var(--bone) 68%, transparent)",
    "--dim": "color-mix(in oklch, var(--bone) 46%, transparent)",
    "--faint": "color-mix(in oklch, var(--bone) 16%, transparent)",
    "--line": "color-mix(in oklch, var(--bone) 15%, transparent)",
    "--line-hot": "color-mix(in oklch, var(--main) 52%, transparent)",
    "--amber": main,
    "--amber-soft": "color-mix(in oklch, var(--main) 11%, transparent)",
    "--jade": rvbbit,
    "--jade-soft": "color-mix(in oklch, var(--rvbbit-accent) 9%, transparent)",
  };
}

function swatchToOklch(swatch) {
  const { l, c, h } = rgbToOklch(...swatch.rgb);
  return `oklch(${round(l * 100, 2)}% ${round(c, 4)} ${round(h, 1)})`;
}

function pickMostPopulous(swatches) {
  let best = null;
  for (const swatch of swatches) {
    if (swatch && (!best || swatch.population > best.population)) best = swatch;
  }
  return best;
}

function firstNonNull(...candidates) {
  return candidates.find(Boolean) || null;
}

function darken(swatch) {
  const [r, g, b] = swatch.rgb;
  return cloneSwatch(swatch, [Math.round(r * .6), Math.round(g * .6), Math.round(b * .6)]);
}

function lighten(swatch) {
  const [r, g, b] = swatch.rgb;
  const mix = .45;
  return cloneSwatch(swatch, [
    Math.round(r + (255 - r) * mix),
    Math.round(g + (255 - g) * mix),
    Math.round(b + (255 - b) * mix),
  ]);
}

function cloneSwatch(template, rgb) {
  return new template.constructor(rgb, 1);
}

function rgbToOklch(r, g, b) {
  const lr = srgbToLin(r / 255);
  const lg = srgbToLin(g / 255);
  const lb = srgbToLin(b / 255);
  const l_ = .4122214708 * lr + .5363325363 * lg + .0514459929 * lb;
  const m_ = .2119034982 * lr + .6806995451 * lg + .1073969566 * lb;
  const s_ = .0883024619 * lr + .2817188376 * lg + .6299787005 * lb;
  const lc = Math.cbrt(l_);
  const mc = Math.cbrt(m_);
  const sc = Math.cbrt(s_);
  const l = .2104542553 * lc + .793617785 * mc - .0040720468 * sc;
  const a = 1.9779984951 * lc - 2.428592205 * mc + .4505937099 * sc;
  const bb = .0259040371 * lc + .7827717662 * mc - .808675766 * sc;
  return {
    l: clamp(l, 0, 1),
    c: Math.hypot(a, bb),
    h: ((Math.atan2(bb, a) * 180) / Math.PI + 360) % 360,
  };
}

function srgbToLin(value) {
  return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
}

function oklch(lightness, chroma, hue, alpha) {
  return alpha !== undefined && alpha !== 1
    ? `oklch(${lightness}% ${chroma} ${hue} / ${alpha})`
    : `oklch(${lightness}% ${chroma} ${hue})`;
}

function hueOf(value) {
  const match = String(value).match(/oklch\(\s*[\d.]+%?\s+[\d.]+\s+([\d.]+)/i);
  return match ? Number(match[1]) : null;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function round(value, digits) {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[character]);
}

function openDb() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      reject(new Error("Browser image storage is unavailable."));
      return;
    }
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(DB_STORE)) {
        db.createObjectStore(DB_STORE, { keyPath: "id" });
      }
    };
    request.onerror = () => reject(request.error || new Error("Could not open browser image storage."));
    request.onsuccess = () => resolve(request.result);
  });
}

function withStore(mode, run) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const transaction = db.transaction(DB_STORE, mode);
    const store = transaction.objectStore(DB_STORE);
    const request = run(store);
    request.onerror = () => reject(request.error || new Error("Browser image storage failed."));
    request.onsuccess = () => resolve(request.result);
    transaction.oncomplete = () => db.close();
    transaction.onerror = () => {
      db.close();
      reject(transaction.error || new Error("Browser image storage failed."));
    };
    transaction.onabort = transaction.onerror;
  }));
}

function saveUploadBlob(file) {
  return withStore("readwrite", (store) => store.put({
    id: DB_KEY,
    blob: file,
    type: file.type,
    name: file.name,
    updatedAt: new Date().toISOString(),
  }));
}

async function loadUploadBlob() {
  const record = await withStore("readonly", (store) => store.get(DB_KEY));
  return record?.blob || null;
}

function clearUploadBlob() {
  return withStore("readwrite", (store) => store.delete(DB_KEY));
}
