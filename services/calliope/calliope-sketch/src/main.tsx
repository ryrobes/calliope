import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Excalidraw,
  CaptureUpdateAction,
  exportToBlob,
  exportToSvg,
  serializeAsJSON,
} from "@excalidraw/excalidraw";
import type {
  AppState,
  BinaryFileData,
  BinaryFiles,
  ExcalidrawImperativeAPI,
} from "@excalidraw/excalidraw/types";
import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import "@excalidraw/excalidraw/index.css";
import "./sketch.css";
import { materializeSketchElements } from "./scene";
import { DEFAULT_SKETCH_FONT } from "./fonts";
import type { SketchScene } from "./scene";
import { applyAdaptiveSketchTheme } from "./theme";
import type { ThemeMode } from "./theme";
import { authoredElements, displayElements, sketchBackground } from "./scene-colors";

type SketchRecord = {
  id: string;
  title: string;
  revision: number;
  element_count: number;
  last_actor: "calliope" | "human" | "undo";
  last_operation_count: number;
  last_change_summary?: { element_ids?: string[] };
  can_undo_calliope?: boolean;
  scene: SketchScene;
};
type SketchResponse = { sketch: SketchRecord; read_only?: boolean; presentation?: boolean; frozen_snapshot?: boolean };

const mount = document.getElementById("sketch-root");
if (!mount) throw new Error("Sketch mount is unavailable");
const embedded = window.parent !== window;
document.documentElement.dataset.embedded = String(embedded);
const sketchId = String(mount.dataset.sketchId || "");
const declaredReadOnly = mount.dataset.readOnly === "true";
const declaredPresentation = mount.dataset.presentation === "true";
const sourceUrl = String(
  mount.dataset.sourceUrl || `/api/calliope/sketches/${encodeURIComponent(sketchId)}`,
);
const requestedTheme = document.documentElement.dataset.theme
  || new URLSearchParams(window.location.search).get("theme");
// Ego-centric arrival: ?focus=<element_id> opens the scene zoomed to that
// element's neighborhood instead of the whole map (Briarpatch geography —
// stable bp-* ids make every territory node a linkable address).
const requestedFocus = new URLSearchParams(window.location.search).get("focus");
const FOCUS_NEIGHBORHOOD_PX = 640;

function sceneFiles(scene: SketchScene | undefined): BinaryFileData[] {
  const files = scene?.files;
  if (!files || typeof files !== "object") return [];
  return Object.values(files).filter((item): item is BinaryFileData =>
    Boolean(item && typeof item === "object" && (item as any).dataURL));
}

function focusTargets(elements: readonly ExcalidrawElement[]): ExcalidrawElement[] {
  if (!requestedFocus) return [];
  const target = elements.find((item) => !item.isDeleted && item.id === requestedFocus);
  if (!target) return [];
  const cx = target.x + (target.width || 0) / 2;
  const cy = target.y + (target.height || 0) / 2;
  return elements.filter((item) => {
    if (item.isDeleted) return false;
    const dx = item.x + (item.width || 0) / 2 - cx;
    const dy = item.y + (item.height || 0) / 2 - cy;
    return Math.hypot(dx, dy) <= FOCUS_NEIGHBORHOOD_PX;
  });
}

function parentMessage(type: string, detail: Record<string, unknown> = {}) {
  if (window.parent === window) return;
  window.parent.postMessage({ type, sketch_id: sketchId, ...detail }, window.location.origin);
}

async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: { "content-type": "application/json", ...(options.headers || {}) },
  });
  let body: any = null;
  try { body = await response.json(); } catch { /* handled below */ }
  if (!response.ok) {
    const error = new Error(body?.error?.message || `Sketch request failed (${response.status})`);
    (error as Error & { code?: string; status?: number }).code = body?.error?.code;
    (error as Error & { code?: string; status?: number }).status = response.status;
    throw error;
  }
  return body as T;
}

function sceneFingerprint(elements: readonly ExcalidrawElement[]) {
  return JSON.stringify(authoredElements(elements).filter((item) => !item.isDeleted).map((item) => {
    const { updated: _updated, versionNonce: _nonce, seed: _seed, index: _index, ...stable } = item;
    return stable;
  }));
}

function retainedAppState(appState: AppState | Record<string, unknown> | undefined) {
  const source = (appState || {}) as Record<string, unknown>;
  const result: Record<string, unknown> = {};
  for (const key of ["gridSize", "gridStep", "gridModeEnabled"]) {
    if (source[key] !== undefined) result[key] = source[key];
  }
  return result;
}

function backgroundFor(_theme: ThemeMode) {
  return "transparent";
}

function blobDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result || "")), { once: true });
    reader.addEventListener("error", () => reject(reader.error || new Error("Could not render preview")), { once: true });
    reader.readAsDataURL(blob);
  });
}

function SketchApp({ initial }: { initial: SketchResponse }) {
  const [sketch, setSketch] = useState(initial.sketch);
  const [readOnly] = useState(Boolean(initial.read_only || declaredReadOnly));
  const [presentation] = useState(Boolean(initial.presentation || declaredPresentation));
  const frozen = Boolean(initial.frozen_snapshot);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const [editing, setEditing] = useState(false);
  const [finishingEdit, setFinishingEdit] = useState(false);
  const canEdit = !readOnly && !presentation;
  const editable = canEdit && editing && !finishingEdit;
  const [theme, setTheme] = useState<ThemeMode>(() =>
    requestedTheme === "light" || requestedTheme === "dark"
      ? requestedTheme
      : window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark",
  );
  const [themeEpoch, setThemeEpoch] = useState(0);
  const [status, setStatus] = useState<"ready" | "dirty" | "saving" | "error">("ready");
  const [message, setMessage] = useState("");
  const [mounted, setMounted] = useState(false);
  const apiRef = useRef<ExcalidrawImperativeAPI | null>(null);
  const revisionRef = useRef(Number(initial.sketch.revision));
  const fingerprintRef = useRef("");
  const observedFingerprintRef = useRef("");
  const dirtyRef = useRef(false);
  const applyingRef = useRef(true);
  const savingRef = useRef(false);
  const saveTimerRef = useRef<number | null>(null);
  const previewTimerRef = useRef<number | null>(null);
  // On open the scene always fits the frame — whether the Stage panel or the
  // Large workspace — zooming in or out as needed. Frame size changes keep it
  // fitted until the person zooms or pans themselves; an explicit open/restore
  // (the shell's viewport.changed message) fits again and hands the zoom back.
  const userAdjustedViewRef = useRef(false);
  const fitTimerRef = useRef<number | null>(null);
  const fitToFrame = useCallback((animate = false) => {
    const api = apiRef.current;
    if (!api) return;
    const elements = api.getSceneElements();
    if (!elements.length) return;
    api.scrollToContent(elements, {
      fitToViewport: true, viewportZoomFactor: 0.9, minZoom: 0.1, maxZoom: 1.6, animate,
    });
  }, []);
  const scheduleFit = useCallback(() => {
    if (fitTimerRef.current != null) window.clearTimeout(fitTimerRef.current);
    fitTimerRef.current = window.setTimeout(() => {
      fitTimerRef.current = null;
      if (!userAdjustedViewRef.current) window.requestAnimationFrame(() => fitToFrame(false));
    }, 90);
  }, [fitToFrame]);
  useEffect(() => {
    if (!frozen) return;
    const refit = () => { userAdjustedViewRef.current = false; scheduleFit(); };
    document.addEventListener('fullscreenchange', refit);
    return () => document.removeEventListener('fullscreenchange', refit);
  }, [frozen, scheduleFit]);

  const forcePreviewRef = useRef<() => void>(() => {});

  const initialElements = useMemo(() => materializeSketchElements(initial.sketch.scene), [initial.sketch]);
  const initialData = useMemo(() => ({
    elements: displayElements(initialElements, sketchBackground(theme)),
    appState: {
      ...(initial.sketch.scene?.appState || {}),
      // Zen is a per-open presentation default, not shared scene state. Users
      // can still toggle it off without a later Calliope revision forcing it
      // back on during the same editing session.
      zenModeEnabled: true,
      currentItemFontFamily: DEFAULT_SKETCH_FONT,
      viewBackgroundColor: backgroundFor(theme),
    },
    files: Object.fromEntries(
      sceneFiles(initial.sketch.scene).map((item) => [String(item.id), item]),
    ) as BinaryFiles,
    scrollToContent: !requestedFocus,
  }), [initialElements]); // Theme changes are applied imperatively after mount.

  const notifySaved = useCallback((next: SketchRecord, changed: boolean) => {
    parentMessage("calliope.sketch.saved", {
      revision: next.revision,
      changed,
      last_actor: next.last_actor,
      last_operation_count: next.last_operation_count,
      element_count: next.element_count,
      last_change_summary: next.last_change_summary || {},
      can_undo_calliope: Boolean(next.can_undo_calliope),
      title: next.title,
    });
  }, []);

  const applyRemote = useCallback((next: SketchRecord, highlight = true) => {
    const api = apiRef.current;
    if (!api) return;
    applyingRef.current = true;
    const files = sceneFiles(next.scene);
    if (files.length) api.addFiles(files);
    const elements = materializeSketchElements(next.scene);
    const changedIds = highlight && next.last_actor === "calliope"
      ? (next.last_change_summary?.element_ids || []).filter(Boolean)
      : [];
    const selectedElementIds = Object.fromEntries(
      changedIds.map((id) => [id, true as const]),
    ) as Record<string, true>;
    api.updateScene({
      elements: displayElements(elements, sketchBackground(theme)),
      appState: {
        ...(next.scene?.appState || {}),
        viewBackgroundColor: backgroundFor(theme),
        selectedElementIds,
      },
    });
    api.history.clear();
    api.scrollToContent(elements, { fitToContent: true, animate: true });
    revisionRef.current = Number(next.revision);
    fingerprintRef.current = sceneFingerprint(elements);
    observedFingerprintRef.current = fingerprintRef.current;
    dirtyRef.current = false;
    setSketch(next);
    setStatus("ready");
    setMessage("");
    window.setTimeout(() => { applyingRef.current = false; }, 0);
    if (!readOnly) {
      if (previewTimerRef.current != null) window.clearTimeout(previewTimerRef.current);
      previewTimerRef.current = window.setTimeout(() => {
        previewTimerRef.current = null;
        forcePreviewRef.current();
      }, 350);
    }
    if (changedIds.length) {
      window.setTimeout(() => {
        if (revisionRef.current !== Number(next.revision) || !apiRef.current) return;
        apiRef.current.updateScene({ appState: { selectedElementIds: {} } });
      }, 1800);
    }
  }, [readOnly, theme]);

  const saveNow = useCallback(async (forcePreview = false) => {
    const api = apiRef.current;
    if (!api || !canEdit || savingRef.current) return false;
    const rendered = api.getSceneElements();
    const elements = authoredElements(rendered);
    const fingerprint = sceneFingerprint(elements);
    // Preview-only writes must not acknowledge unsaved scene edits.
    if (forcePreview && fingerprint !== fingerprintRef.current) return false;
    if (!forcePreview && fingerprint === fingerprintRef.current) {
      dirtyRef.current = false;
      setStatus("ready");
      setMessage("");
      return true;
    }
    savingRef.current = true;
    setStatus("saving");
    setMessage("Saving");
    try {
      const appState = api.getAppState();
      const blob = await exportToBlob({
        elements: rendered.filter((item) => !item.isDeleted),
        appState: {
          ...appState,
          viewBackgroundColor: sketchBackground(theme),
          exportBackground: true,
          exportWithDarkMode: false,
          theme: "light",
        },
        files: api.getFiles() as BinaryFiles,
        mimeType: "image/png",
        maxWidthOrHeight: 1600,
        exportPadding: 42,
      });
      const result = await requestJson<{ changed: boolean; sketch: SketchRecord }>(
        `/api/calliope/sketches/${encodeURIComponent(sketchId)}`,
        {
          method: "PUT",
          body: JSON.stringify({
            expected_revision: revisionRef.current,
            elements,
            app_state: retainedAppState(appState),
            preview_data_url: await blobDataUrl(blob),
            preview_only: forcePreview,
          }),
        },
      );
      revisionRef.current = Number(result.sketch.revision);
      fingerprintRef.current = fingerprint;
      dirtyRef.current = false;
      setSketch(result.sketch);
      setStatus("ready");
      setMessage(result.changed ? `Saved · r${result.sketch.revision}` : "");
      notifySaved(result.sketch, result.changed);
      return true;
    } catch (error: any) {
      if (error?.status === 409) {
        try {
          const latest = await requestJson<SketchResponse>(sourceUrl);
          applyRemote(latest.sketch, true);
          setMessage("Reloaded newer edit");
          parentMessage("calliope.sketch.conflict", { revision: latest.sketch.revision });
          return false;
        } catch { /* surface the original conflict below */ }
      }
      setStatus("error");
      setMessage(error?.message || "Could not save");
      parentMessage("calliope.sketch.error", { message: error?.message || "Could not save" });
      return false;
    } finally {
      savingRef.current = false;
    }
  }, [applyRemote, notifySaved, canEdit, theme]);

  const toggleEditing = useCallback(async () => {
    if (!canEdit || savingRef.current || finishingEdit) return;
    if (!editing) {
      setEditing(true);
      setMessage("");
      apiRef.current?.updateScene({ appState: { zenModeEnabled: false } });
      apiRef.current?.setActiveTool({ type: "selection" });
      return;
    }
    // Temporarily lock interactions while flushing the last edit. On failure
    // keep editing enabled so the user's unsaved work remains available.
    setFinishingEdit(true);
    if (saveTimerRef.current != null) window.clearTimeout(saveTimerRef.current);
    try {
      if (await saveNow(false)) {
        setEditing(false);
        apiRef.current?.updateScene({ appState: { selectedElementIds: {}, zenModeEnabled: true } });
      }
    } finally {
      setFinishingEdit(false);
    }
  }, [canEdit, editing, finishingEdit, saveNow]);

  forcePreviewRef.current = () => void saveNow(true);

  const scheduleSave = useCallback(() => {
    if (!editable) return;
    dirtyRef.current = true;
    setStatus("dirty");
    setMessage("Unsaved edit");
    parentMessage("calliope.sketch.dirty", { revision: revisionRef.current });
    if (saveTimerRef.current != null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => void saveNow(false), 1000);
  }, [editable, saveNow]);

  const onChange = useCallback((
    elements: readonly ExcalidrawElement[],
    appState: AppState,
  ) => {
    const sidebar = appState.openSidebar;
    if (sidebar?.name === "default" && (!sidebar.tab || sidebar.tab === "library")) {
      apiRef.current?.updateScene({ appState: { openSidebar: null } });
    }
    if (!mounted || applyingRef.current || !editable) return;
    const fingerprint = sceneFingerprint(elements);
    // Mode, selection and viewport changes also fire onChange. Only a new
    // scene edit should project ink and schedule a write.
    if (fingerprint === observedFingerprintRef.current) return;
    const projected = displayElements(elements, sketchBackground(theme));
    if (projected.some((element, i) => element !== elements[i])) {
      apiRef.current?.updateScene({ elements: projected });
    }
    observedFingerprintRef.current = fingerprint;
    scheduleSave();
  }, [mounted, editable, scheduleSave, theme]);

  const setApi = useCallback((api: ExcalidrawImperativeAPI) => {
    apiRef.current = api;
    fingerprintRef.current = sceneFingerprint(api.getSceneElements());
    observedFingerprintRef.current = fingerprintRef.current;
    window.requestAnimationFrame(() => {
      // Excalidraw exposes its API before it restores initialData. Establish
      // the saved baseline after that commit, before allowing interaction.
      fingerprintRef.current = sceneFingerprint(api.getSceneElements());
      observedFingerprintRef.current = fingerprintRef.current;
      applyingRef.current = false;
      setMounted(true);
      const neighborhood = focusTargets(api.getSceneElements());
      if (neighborhood.length) {
        api.scrollToContent(neighborhood, { fitToContent: true, animate: false });
        api.updateScene({
          appState: {
            selectedElementIds: { [requestedFocus as string]: true },
          },
        });
        window.setTimeout(() => {
          apiRef.current?.updateScene({ appState: { selectedElementIds: {} } });
        }, 2200);
      } else {
        fitToFrame(false);
        // The frame may still be settling (shelf reveal, dialog open); fit again
        // once layout has had a task to land, unless the person moved first.
        scheduleFit();
      }
      parentMessage("calliope.sketch.ready", {
        revision: revisionRef.current,
        read_only: readOnly,
      });
      parentMessage("calliope.sketch.theme.request");
      if (!readOnly) {
        previewTimerRef.current = window.setTimeout(() => void saveNow(true), 450);
      }
    });
  }, [readOnly, saveNow]);

  const undoCalliope = useCallback(async () => {
    if (!editable || savingRef.current || dirtyRef.current) return;
    savingRef.current = true;
    setStatus("saving");
    setMessage("Undoing Calliope");
    try {
      const result = await requestJson<{ sketch: SketchRecord }>(
        `/api/calliope/sketches/${encodeURIComponent(sketchId)}/undo-calliope`,
        {
          method: "POST",
          body: JSON.stringify({ expected_revision: revisionRef.current }),
        },
      );
      applyRemote(result.sketch, false);
      notifySaved(result.sketch, true);
    } catch (error: any) {
      setStatus("error");
      setMessage(error?.message || "Could not undo");
    } finally {
      savingRef.current = false;
    }
  }, [applyRemote, notifySaved, editable]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    // Excalidraw finishes applying initialData during its own commit. Reapply
    // the viewer color one task later so a late parent-theme handoff wins that
    // race instead of leaving dark controls over a light canvas (or vice versa).
    const timer = window.setTimeout(() => {
      const api = apiRef.current;
      if (!api) return;
      api.updateScene({
        elements: displayElements(api.getSceneElementsIncludingDeleted(), sketchBackground(theme)),
        appState: { viewBackgroundColor: backgroundFor(theme) },
        captureUpdate: CaptureUpdateAction.NEVER,
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [mounted, theme, themeEpoch]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== window.parent) return;
      if (event.data?.type === "calliope.sketch.snapshot") {
        const request_id = event.data.request_id;
        void (async () => {
          try {
            const api = apiRef.current;
            if (!api) throw new Error("Wait for the Sketch to load");
            // Copy the live scene immediately, including edits not yet autosaved.
            // The export is independent of later saves, remote updates and themes.
            const elements = structuredClone(api.getSceneElements().filter(item => !item.isDeleted));
            if (!elements.length) throw new Error("Add something to the Sketch before taking a snapshot");
            const dirty = dirtyRef.current;
            const files = structuredClone(api.getFiles());
            const scene = {elements: authoredElements(elements), files,
              appState: retainedAppState(api.getAppState())};
            const blob = await exportToBlob({elements, files,
              appState: {...api.getAppState(), viewBackgroundColor: sketchBackground(theme),
                exportBackground: true, exportWithDarkMode: false, theme: "light"},
              mimeType: "image/png", maxWidthOrHeight: 2400, exportPadding: 42});
            parentMessage("calliope.sketch.snapshot.result", {request_id, dirty, scene, data_url: await blobDataUrl(blob)});
          } catch (error: any) {
            parentMessage("calliope.sketch.snapshot.result", {request_id, error: error?.message || "Could not capture Sketch"});
          }
        })();
      } else if (event.data?.type === "calliope.sketch.theme.apply") {
        setTheme(applyAdaptiveSketchTheme(event.data));
        setThemeEpoch(epoch => epoch + 1);
      } else if (event.data?.type === "calliope.sketch.flush") {
        if (saveTimerRef.current != null) window.clearTimeout(saveTimerRef.current);
        void saveNow(false);
      } else if (event.data?.type === "calliope.sketch.viewport.changed") {
        userAdjustedViewRef.current = false;
        window.requestAnimationFrame(() => {
          window.dispatchEvent(new Event("resize"));
          scheduleFit();
        });
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [saveNow, scheduleFit, theme]);

  useEffect(() => {
    // The iframe resizing (panel ↔ workspace, window resize) is a viewport
    // change too; keep the scene fitted until the person takes the zoom.
    const onResize = () => scheduleFit();
    const onAdjust = () => { userAdjustedViewRef.current = true; };
    window.addEventListener("resize", onResize);
    window.addEventListener("wheel", onAdjust, { passive: true, capture: true });
    window.addEventListener("pointerdown", onAdjust, { passive: true, capture: true });
    window.addEventListener("keydown", onAdjust, { capture: true });
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("wheel", onAdjust, { capture: true } as EventListenerOptions);
      window.removeEventListener("pointerdown", onAdjust, { capture: true } as EventListenerOptions);
      window.removeEventListener("keydown", onAdjust, { capture: true } as EventListenerOptions);
      if (fitTimerRef.current != null) window.clearTimeout(fitTimerRef.current);
    };
  }, [scheduleFit]);

  useEffect(() => {
    if (presentation) return;
    const interval = window.setInterval(async () => {
      if (document.hidden || dirtyRef.current || savingRef.current) return;
      try {
        const latest = await requestJson<SketchResponse>(sourceUrl);
        if (Number(latest.sketch.revision) > revisionRef.current) {
          applyRemote(latest.sketch, true);
        }
      } catch { /* the visible editor retains its last safe checkpoint */ }
    }, 2500);
    return () => window.clearInterval(interval);
  }, [applyRemote, presentation]);

  useEffect(() => () => {
    if (saveTimerRef.current != null) window.clearTimeout(saveTimerRef.current);
    if (previewTimerRef.current != null) window.clearTimeout(previewTimerRef.current);
  }, []);

  async function downloadSnapshot(format: "svg" | "png" | "excalidraw") {
    const api = apiRef.current;
    if (!api || exporting) return;
    setExporting(true); setExportError('');
    try {
      // Render the immutable content using the theme currently visible here.
      const elements = structuredClone(api.getSceneElements().filter(item => !item.isDeleted));
      const files = structuredClone(api.getFiles());
      const appState = {...api.getAppState(), viewBackgroundColor: sketchBackground(theme),
        exportBackground: true, exportWithDarkMode: false, theme: "light" as const};
      let blob: Blob;
      if (format === 'svg') {
        const svg = await exportToSvg({elements, appState, files, exportPadding: 42});
        blob = new Blob([new XMLSerializer().serializeToString(svg)], {type: 'image/svg+xml'});
      } else if (format === 'excalidraw') {
        blob = new Blob([serializeAsJSON(elements, appState, files, 'local')],
          {type: 'application/vnd.excalidraw+json'});
      } else {
        blob = await exportToBlob({elements, appState, files, mimeType: 'image/png',
          maxWidthOrHeight: 2400, exportPadding: 42});
      }
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = `sketch-snapshot.${format}`;
      link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error: any) {
      setExportError(error?.message || 'Export failed. Try again.');
    } finally { setExporting(false); }
  }

  const renderControls = useCallback(() => (
    <div className="sketch-tools" role="toolbar" aria-label="Sketch controls" aria-live="polite">
      <span className={status === "error" ? "error" : status === "dirty" ? "dirty" : "muted"}>
        {!editing ? "View only" : message || `Saved · r${sketch.revision}`}
      </span>
      {canEdit && (
        <button type="button" className="sketch-edit-toggle workspace-control"
          aria-pressed={editing}
          disabled={!mounted || status === "saving" || finishingEdit}
          onClick={() => void toggleEditing()}
        >{finishingEdit ? "Saving…" : editing ? "Done editing" : "Edit canvas"}</button>
      )}
      {editable && (
        <button className="workspace-control"
          type="button"
          disabled={!sketch.can_undo_calliope || status !== "ready"}
          onClick={() => void undoCalliope()}
          title="Undo Calliope's latest revision"
        >Undo Callie</button>
      )}
      {!embedded && <button className="workspace-control"
        type="button"
        onClick={() => parentMessage("calliope.sketch.expand.request")}
        title="Open the Sketch in a large workspace"
        aria-label="Open large workspace"
      >↗</button>}
    </div>
  ), [message, editing, editable, canEdit, finishingEdit, mounted, toggleEditing, sketch.can_undo_calliope, sketch.revision, status, undoCalliope]);

  return (
    <main className={`sketch-app ${!editable ? "read-only" : "editing"} ${presentation ? "presentation" : ""} ${frozen ? "frozen-snapshot" : ""}`}>
      <div className="sketch-canvas">
        <Excalidraw
          initialData={initialData}
          excalidrawAPI={setApi}
          onChange={onChange}
          viewModeEnabled={!editable}
          // Calliope supplies the actual palette and readable scene colors.
          // Light rendering avoids the built-in canvas AND image inversion.
          theme="light"
          name={sketch.title}
          autoFocus={false}
          detectScroll={false}
          handleKeyboardGlobally={false}
          UIOptions={{
            canvasActions: {
              loadScene: false,
              saveToActiveFile: false,
              export: false,
              toggleTheme: false,
              saveAsImage: false,
            },
            tools: { image: false },
          }}
        />
      </div>
      {!presentation && renderControls()}
      {frozen && <div className="snapshot-tools" role="toolbar" aria-label="Frozen Sketch controls">
        <button className="workspace-control" type="button" onClick={() => fitToFrame(true)}>Fit</button>
        <button className="workspace-control" type="button" onClick={() => {
          const node = document.documentElement;
          void (document.fullscreenElement ? document.exitFullscreen() : node.requestFullscreen())
            .catch(() => setExportError('Use Open full size in the Stage header.'));
        }}>Full screen</button>
        {(['svg', 'png', 'excalidraw'] as const).map(format =>
          <button className="workspace-control" type="button" key={format} disabled={exporting || !mounted}
            onClick={() => void downloadSnapshot(format)}
            title={`Download ${format === 'excalidraw' ? 'editable native Sketch' : format.toUpperCase()} using the current theme`}>
            {format === 'excalidraw' ? 'Sketch file' : format.toUpperCase()}
          </button>)}
        {exportError && <span role="alert">{exportError}</span>}
      </div>}

      <div className={`sketch-loading ${mounted ? "done" : ""}`} aria-hidden={mounted}>
        <i></i><strong>{frozen ? "Opening snapshot" : presentation ? "Opening plan" : "Opening shared sketch"}</strong><span>{presentation ? "Restoring the pinned read-only revision" : "Restoring the latest sketch"}</span>
      </div>
    </main>
  );
}

async function boot() {
  if (!sketchId) throw new Error("Sketch identity is missing");
  const initial = await requestJson<SketchResponse>(sourceUrl);
  createRoot(mount!).render(<SketchApp initial={initial} />);
}

void boot().catch((error) => {
  mount.innerHTML = `<div class="sketch-fatal"><strong>Sketch unavailable</strong><span></span></div>`;
  const detail = mount.querySelector("span");
  if (detail) detail.textContent = error?.message || "The latest checkpoint could not be opened.";
  parentMessage("calliope.sketch.error", { message: error?.message || "Sketch unavailable" });
});
