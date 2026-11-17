"use client"

import type { ImagePalette } from "./palette"

/**
 * Wallpaper persistence via IndexedDB. We keep blobs (not data URLs) so
 * large images don't bloat the localStorage quota. Single user, single
 * key — no userId multiplexing.
 *
 * Each record also carries the palette derived from the image plus any
 * per-color overrides the user has locked in. This lets us restore the
 * exact theme on reload without re-running vibrant on every page load.
 */

// This is Calliope's existing browser appearance store. DataRabbit and
// Calliope are served from one origin in the hosted stack, so using one DB
// lets uploaded wallpaper blobs survive navigation in either direction.
const DB_NAME = "rvbbit-warehouse-browser"
const DB_VERSION = 1
const STORE_NAME = "appearance"
const KEY = "theme:default"
const LEGACY_DB_NAME = "rvbbit-lens-desktop"
const LEGACY_DB_VERSION = 2
const LEGACY_STORE_NAME = "wallpapers"
const LEGACY_KEY = "wallpaper:default"
const LEGACY_MIGRATION_KEY = "rvbbit-shared-appearance-idb-migrated-v1"

export const WALLPAPER_IMAGE_EXTENSIONS = [
  "avif",
  "bmp",
  "gif",
  "heic",
  "heif",
  "ico",
  "jfif",
  "jpe",
  "jpeg",
  "jpg",
  "pjp",
  "pjpeg",
  "png",
  "svg",
  "tif",
  "tiff",
  "webp",
] as const

export const WALLPAPER_FILE_ACCEPT = [
  "image/*",
  ...WALLPAPER_IMAGE_EXTENSIONS.map((ext) => `.${ext}`),
].join(",")

export type DesktopWallpaperSource =
  | { kind: "upload"; name?: string }
  | { kind: "library"; id: string; label?: string; url?: string; originalUrl?: string }

interface WallpaperRecord {
  id: string
  blob?: Blob
  type?: string
  updatedAt: string
  source?: DesktopWallpaperSource
  palette?: ImagePalette
  /**
   * User overrides. Whatever's set here wins over the extractor's
   * output during apply, and survives re-extraction. Keys correspond
   * to the ImagePalette fields the user chose to lock.
   */
  paletteOverrides?: Partial<ImagePalette>
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("Browser storage is unavailable."))
      return
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id" })
      }
    }
    req.onerror = () => reject(req.error ?? new Error("Failed to open wallpaper storage."))
    req.onsuccess = () => resolve(req.result)
  })
}

function legacyMigrationDone(): boolean {
  try { return window.localStorage.getItem(LEGACY_MIGRATION_KEY) === "1" } catch { return false }
}

function markLegacyMigrationDone(): void {
  try { window.localStorage.setItem(LEGACY_MIGRATION_KEY, "1") } catch { /* best-effort */ }
}

function loadLegacyRecord(): Promise<WallpaperRecord | undefined> {
  if (typeof indexedDB === "undefined" || legacyMigrationDone()) return Promise.resolve(undefined)
  return new Promise((resolve) => {
    const req = indexedDB.open(LEGACY_DB_NAME, LEGACY_DB_VERSION)
    req.onerror = () => { markLegacyMigrationDone(); resolve(undefined) }
    req.onupgradeneeded = () => {
      // A missing legacy DB is not an error and must not be created just to migrate it.
      req.transaction?.abort()
    }
    req.onsuccess = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(LEGACY_STORE_NAME)) {
        db.close()
        markLegacyMigrationDone()
        resolve(undefined)
        return
      }
      const tx = db.transaction(LEGACY_STORE_NAME, "readonly")
      const get = tx.objectStore(LEGACY_STORE_NAME).get(LEGACY_KEY)
      get.onerror = () => { db.close(); markLegacyMigrationDone(); resolve(undefined) }
      get.onsuccess = () => {
        const legacy = get.result as WallpaperRecord | undefined
        db.close()
        markLegacyMigrationDone()
        resolve(legacy ? { ...legacy, id: KEY } : undefined)
      }
    }
  })
}

async function loadRecordWithMigration(): Promise<WallpaperRecord | undefined> {
  const current = await withStore<WallpaperRecord | undefined>("readonly", (s) => s.get(KEY))
  if (current) {
    markLegacyMigrationDone()
    return current
  }
  const legacy = await loadLegacyRecord()
  if (legacy) await withStore("readwrite", (s) => s.put(legacy))
  return legacy
}

function withStore<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return open().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, mode)
        const store = tx.objectStore(STORE_NAME)
        const req = run(store)
        req.onerror = () => reject(req.error ?? new Error("Wallpaper storage failed."))
        req.onsuccess = () => resolve(req.result)
        tx.oncomplete = () => db.close()
        tx.onerror = () => { db.close(); reject(tx.error ?? new Error("Wallpaper storage failed.")) }
        tx.onabort = () => { db.close(); reject(tx.error ?? new Error("Wallpaper storage was aborted.")) }
      }),
  )
}

export async function saveDesktopWallpaper(
  blob: Blob,
  palette?: ImagePalette,
  paletteOverrides?: Partial<ImagePalette>,
  source: DesktopWallpaperSource = { kind: "upload" },
): Promise<void> {
  const record: WallpaperRecord = {
    id: KEY,
    blob,
    type: blob.type,
    updatedAt: new Date().toISOString(),
    source,
    palette,
    paletteOverrides,
  }
  await withStore("readwrite", (s) => s.put(record))
}

export async function saveDesktopWallpaperSource(
  source: DesktopWallpaperSource,
  palette?: ImagePalette,
  paletteOverrides?: Partial<ImagePalette>,
): Promise<void> {
  const record: WallpaperRecord = {
    id: KEY,
    updatedAt: new Date().toISOString(),
    source,
    palette,
    paletteOverrides,
  }
  await withStore("readwrite", (s) => s.put(record))
}

export async function loadDesktopWallpaperBlob(): Promise<Blob | null> {
  const r = await loadRecordWithMigration()
  return r?.blob ?? null
}

/** Full record — blob + palette + overrides. Returns null if no wallpaper. */
export async function loadDesktopWallpaperRecord(): Promise<{
  blob: Blob | null
  source?: DesktopWallpaperSource
  palette?: ImagePalette
  paletteOverrides?: Partial<ImagePalette>
} | null> {
  const r = await loadRecordWithMigration()
  if (!r) return null
  return {
    blob: r.blob ?? null,
    source: r.source ?? (r.blob ? { kind: "upload" } : undefined),
    palette: r.palette,
    paletteOverrides: r.paletteOverrides,
  }
}

export async function updateDesktopWallpaperPalette(
  palette: ImagePalette | undefined,
  paletteOverrides: Partial<ImagePalette> | undefined,
): Promise<void> {
  // Read-modify-write so we preserve the blob.
  const existing = await withStore<WallpaperRecord | undefined>("readonly", (s) => s.get(KEY))
  if (!existing) return
  const next: WallpaperRecord = {
    ...existing,
    palette,
    paletteOverrides,
    updatedAt: new Date().toISOString(),
  }
  await withStore("readwrite", (s) => s.put(next))
}

export async function clearDesktopWallpaper(): Promise<void> {
  markLegacyMigrationDone()
  await withStore("readwrite", (s) => s.delete(KEY))
}

export function isLikelyImageFile(file: File): boolean {
  if (file.type.startsWith("image/")) return true
  const ext = file.name.toLowerCase().split(".").pop()
  return !!ext && (WALLPAPER_IMAGE_EXTENSIONS as readonly string[]).includes(ext)
}

export function canRenderImageUrl(url: string): Promise<boolean> {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => resolve(true)
    img.onerror = () => resolve(false)
    img.src = url
  })
}
