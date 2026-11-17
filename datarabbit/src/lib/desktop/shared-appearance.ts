"use client"

import type { ImagePalette, ThemeMode } from "./palette"

/**
 * Appearance contract shared by DataRabbit, Calliope, and viewer-adaptive
 * artifacts. localStorage/IndexedDB are fast first-paint caches. The signed
 * Warehouse preference is canonical so direct ports, origins, and devices do
 * not split into independent themes.
 */
export const SHARED_APPEARANCE_KEY = "rvbbit-warehouse-theme-v1"
export const SHARED_MODE_KEY = "rvbbit-warehouse-color-mode"
export const SHARED_ADAPTIVE_SNAPSHOT_KEY = "rvbbit-warehouse-adaptive-theme-v1"
export const SHARED_APPEARANCE_EVENT = "rvbbit:appearance-change"
const SERVER_APPEARANCE_URL = "/api/rvbbit/appearance"

export type SharedAppearanceSource =
  | { kind: "default"; label?: string }
  | { kind: "upload"; name?: string }
  | { kind: "library"; id: string; label?: string; url: string; originalUrl?: string }

export interface SharedAppearance {
  version: 1
  source: SharedAppearanceSource
  palette: ImagePalette
  tokens: Record<string, string>
  background: {
    mode: "image" | "solid"
    solidColor: string
  }
  paletteOverrides?: Partial<ImagePalette>
  updatedAt: string
}

export interface SharedAppearanceResponse {
  appearance: SharedAppearance | null
  mode: ThemeMode | null
  has_upload: boolean
  updated_at: string | null
}

function isPalette(value: unknown): value is ImagePalette {
  if (!value || typeof value !== "object") return false
  const palette = value as Partial<ImagePalette>
  return Number.isFinite(Number(palette.baseHue))
    && Number.isFinite(Number(palette.chroma))
    && ["vibrant", "darkVibrant", "lightVibrant", "muted", "darkMuted", "lightMuted"]
      .every((key) => typeof palette[key as keyof ImagePalette] === "string")
}

function isSource(value: unknown): value is SharedAppearanceSource {
  if (!value || typeof value !== "object") return false
  const source = value as Partial<SharedAppearanceSource>
  if (source.kind === "default" || source.kind === "upload") return true
  return source.kind === "library"
    && typeof source.id === "string"
    && typeof (source as { url?: unknown }).url === "string"
}

export function readSharedAppearance(): SharedAppearance | null {
  if (typeof window === "undefined") return null
  try {
    const value = JSON.parse(window.localStorage.getItem(SHARED_APPEARANCE_KEY) || "null") as Partial<SharedAppearance> | null
    if (!value || value.version !== 1 || !isSource(value.source) || !isPalette(value.palette)) return null
    if (!value.tokens || typeof value.tokens !== "object") return null
    return value as SharedAppearance
  } catch {
    return null
  }
}

export function readSharedMode(fallback: ThemeMode = "dark"): ThemeMode {
  if (typeof window === "undefined") return fallback
  try {
    const shared = window.localStorage.getItem(SHARED_MODE_KEY)
    if (shared === "light" || shared === "dark") return shared
    // One-time compatibility with DataRabbit's pre-unification mode key.
    return window.localStorage.getItem("rvbbit-lens-theme") === "light" ? "light" : "dark"
  } catch {
    return fallback
  }
}

export function sharedWallpaperUrl(source: SharedAppearanceSource | undefined): string | null {
  if (source?.kind !== "library") return null
  return source.url || source.originalUrl || null
}

export function adaptiveSnapshot(appearance: SharedAppearance, mode: ThemeMode) {
  const wallpaper = appearance.background.mode === "image"
    ? sharedWallpaperUrl(appearance.source)
    : null
  return {
    schema_version: "rvbbit.viewer-theme.v1",
    mode,
    source: {
      kind: appearance.source.kind,
      id: appearance.source.kind === "library" ? appearance.source.id : null,
      label: (
        appearance.source.kind === "library" || appearance.source.kind === "default"
          ? appearance.source.label
          : appearance.source.name
      ) || "Calliope default",
    },
    tokens: appearance.tokens,
    background: {
      mode: appearance.background.mode,
      solid_color: appearance.background.solidColor,
      wallpaper,
      wallpaper_opacity: .64,
      upload: appearance.source.kind === "upload" && appearance.background.mode === "image",
    },
    material: {
      glass_background: "color-mix(in oklch, var(--panel) 78%, transparent)",
      glass_border: "color-mix(in oklch, var(--bone) 15%, transparent)",
      backdrop_blur: "18px",
      shadow: mode === "light"
        ? "0 20px 64px rgba(38,42,48,.18)"
        : "0 20px 64px rgba(0,0,0,.34)",
    },
    updated_at: appearance.updatedAt,
  }
}

function semanticAppearance(appearance: SharedAppearance | null) {
  if (!appearance) return null
  const rest = { ...appearance } as Partial<SharedAppearance>
  delete rest.updatedAt
  return rest
}

function semanticSnapshot(value: unknown) {
  if (!value || typeof value !== "object") return value
  const rest = { ...value as Record<string, unknown> }
  delete rest.updated_at
  return rest
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`
  if (!value || typeof value !== "object") {
    const encoded = JSON.stringify(value)
    return encoded === undefined ? "undefined" : encoded
  }
  const record = value as Record<string, unknown>
  return `{${Object.keys(record).sort().map((key) => (
    `${JSON.stringify(key)}:${stableJson(record[key])}`
  )).join(",")}}`
}

export function sameAppearanceValue(left: unknown, right: unknown): boolean {
  return stableJson(left) === stableJson(right)
}

function cachedAppearanceMatches(appearance: SharedAppearance, mode: ThemeMode): boolean {
  return readSharedMode(mode) === mode
    && sameAppearanceValue(semanticAppearance(readSharedAppearance()), semanticAppearance(appearance))
}

export function cacheSharedAppearance(appearance: SharedAppearance, mode: ThemeMode): void {
  if (typeof window === "undefined") return
  const unchanged = cachedAppearanceMatches(appearance, mode)
  try {
    if (!unchanged) {
      window.localStorage.setItem(SHARED_APPEARANCE_KEY, JSON.stringify(appearance))
      window.localStorage.setItem(SHARED_MODE_KEY, mode)
    }
    const nextSnapshot = adaptiveSnapshot(appearance, mode)
    const previousSnapshot = JSON.parse(
      window.localStorage.getItem(SHARED_ADAPTIVE_SNAPSHOT_KEY) || "null",
    ) as unknown
    if (!sameAppearanceValue(semanticSnapshot(previousSnapshot), semanticSnapshot(nextSnapshot))) {
      window.localStorage.setItem(SHARED_ADAPTIVE_SNAPSHOT_KEY, JSON.stringify(nextSnapshot))
    }
  } catch {
    // Appearance remains active for this render even when persistence is blocked.
  }
  if (unchanged) return
  window.dispatchEvent(new CustomEvent(SHARED_APPEARANCE_EVENT, {
    detail: { appearance, mode },
  }))
}

export function cacheSharedAppearanceReset(mode: ThemeMode): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.removeItem(SHARED_APPEARANCE_KEY)
    window.localStorage.removeItem(SHARED_ADAPTIVE_SNAPSHOT_KEY)
    window.localStorage.setItem(SHARED_MODE_KEY, mode)
  } catch {
    // The current UI can still reset even when its cache is unavailable.
  }
  window.dispatchEvent(new CustomEvent(SHARED_APPEARANCE_EVENT, {
    detail: { appearance: null, mode },
  }))
}

export async function readServerAppearance(): Promise<SharedAppearanceResponse> {
  const response = await fetch(SERVER_APPEARANCE_URL, { cache: "no-store" })
  if (!response.ok) throw new Error(`Appearance sync failed (${response.status})`)
  const value = await response.json() as Partial<SharedAppearanceResponse>
  const mode = value.mode === "light" || value.mode === "dark" ? value.mode : null
  const candidate = value.appearance as Partial<SharedAppearance> | null | undefined
  const appearance = candidate
    && candidate.version === 1
    && isSource(candidate.source)
    && isPalette(candidate.palette)
    && candidate.tokens
    && typeof candidate.tokens === "object"
    ? candidate as SharedAppearance
    : null
  if (appearance && mode) cacheSharedAppearance(appearance, mode)
  else if (mode) cacheSharedAppearanceReset(mode)
  return {
    appearance,
    mode,
    has_upload: value.has_upload === true,
    updated_at: typeof value.updated_at === "string" ? value.updated_at : null,
  }
}

export async function saveServerAppearance(
  appearance: SharedAppearance | null | undefined,
  mode: ThemeMode,
): Promise<void> {
  const response = await fetch(SERVER_APPEARANCE_URL, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ appearance, mode }),
    keepalive: true,
  })
  if (!response.ok) throw new Error(`Appearance sync failed (${response.status})`)
}

export async function uploadServerAppearanceImage(file: File): Promise<void> {
  const response = await fetch(`${SERVER_APPEARANCE_URL}/image`, {
    method: "PUT",
    headers: {
      "content-type": file.type || "application/octet-stream",
      "x-rvbbit-filename": encodeURIComponent(file.name || "wallpaper"),
    },
    body: file,
  })
  if (!response.ok) throw new Error(`Wallpaper sync failed (${response.status})`)
}

export async function readServerAppearanceImage(name = "wallpaper"): Promise<File> {
  const response = await fetch(`${SERVER_APPEARANCE_URL}/image`, { cache: "no-store" })
  if (!response.ok) throw new Error(`Wallpaper sync failed (${response.status})`)
  const blob = await response.blob()
  return new File([blob], name, { type: blob.type || "image/png" })
}

export function writeSharedAppearance(appearance: SharedAppearance, mode: ThemeMode): void {
  if (typeof window !== "undefined" && cachedAppearanceMatches(appearance, mode)) return
  cacheSharedAppearance(appearance, mode)
  void saveServerAppearance(appearance, mode).catch(() => {})
}

export function writeSharedMode(mode: ThemeMode): void {
  if (typeof window === "undefined") return
  try { window.localStorage.setItem(SHARED_MODE_KEY, mode) } catch { /* best-effort */ }
  void saveServerAppearance(undefined, mode).catch(() => {})
}

export function clearSharedAppearance(mode: ThemeMode): void {
  cacheSharedAppearanceReset(mode)
  void saveServerAppearance(null, mode).catch(() => {})
}
