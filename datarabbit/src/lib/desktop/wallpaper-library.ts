export type WallpaperVariant = "thumb" | "1080" | "1440" | "original"

export interface WallpaperLibraryItem {
  id: string
  filename: string
  label: string
  extension: string
  sizeBytes: number
  width: number | null
  height: number | null
  urls: Record<WallpaperVariant, string>
  /** Stable cross-app URL persisted in the shared appearance record. */
  canonicalUrl?: string
}

export async function fetchWallpaperLibrary(): Promise<WallpaperLibraryItem[]> {
  // Hosted Calliope owns the optimized, shared catalog. Keeping this as the
  // primary source means a room chosen in either app has the same durable URL.
  const shared = await fetch("/theme/library").catch(() => null)
  if (shared?.ok) {
    const body = (await shared.json()) as {
      items?: Array<{ id?: string; label?: string; thumb?: string; url?: string; size_bytes?: number }>
    }
    const items = Array.isArray(body.items) ? body.items : []
    return items.flatMap((item) => {
      if (!item.id || !item.url || !item.thumb) return []
      return [{
        id: item.id,
        filename: `${item.id}.webp`,
        label: item.label || item.id,
        extension: "webp",
        sizeBytes: Number(item.size_bytes || 0),
        width: null,
        height: null,
        urls: {
          thumb: item.thumb,
          "1080": item.url,
          "1440": item.url,
          original: item.url,
        },
        canonicalUrl: item.url,
      } satisfies WallpaperLibraryItem]
    })
  }

  // Standalone OSS DataRabbit still has its local wallpaper service.
  const fallback = await fetch("/api/desktop/wallpapers")
  if (!fallback.ok) throw new Error(`Wallpaper library failed: ${fallback.status}`)
  const body = (await fallback.json()) as { wallpapers?: WallpaperLibraryItem[] }
  return (Array.isArray(body.wallpapers) ? body.wallpapers : []).map((item) => ({
    ...item,
    canonicalUrl: item.urls["1440"] || item.urls.original,
  }))
}

export function wallpaperVariantUrl(id: string, variant: WallpaperVariant): string {
  return `/api/desktop/wallpapers/${encodeURIComponent(id)}?variant=${variant}`
}

export function selectWallpaperVariant(width: number, height: number, dpr = 1): WallpaperVariant {
  const targetLongEdge = Math.max(width, height) * Math.max(1, dpr)
  if (targetLongEdge <= 1920) return "1080"
  if (targetLongEdge <= 2880) return "1440"
  return "original"
}

export function selectWallpaperVariantForViewport(): WallpaperVariant {
  if (typeof window === "undefined") return "1440"
  return selectWallpaperVariant(window.innerWidth, window.innerHeight, window.devicePixelRatio || 1)
}
