/**
 * Shared visualization color references.
 *
 * These are CSS variable names, not resolved colors. Keeping them as live
 * variables lets charts, graph nodes, telemetry bars, and KG chips follow
 * dark/light changes and wallpaper-derived theme overrides.
 */

export const VIZ_SERIES_COLORS = [
  "var(--viz-series-1)",
  "var(--viz-series-2)",
  "var(--viz-series-3)",
  "var(--viz-series-4)",
  "var(--viz-series-5)",
  "var(--viz-series-6)",
  "var(--viz-series-7)",
  "var(--viz-series-8)",
  "var(--viz-series-9)",
]

export function colorForVizSeriesIndex(index: number): string {
  return VIZ_SERIES_COLORS[index % VIZ_SERIES_COLORS.length]
}

export function colorForVizKind(value: string): string {
  return `var(--viz-kind-${stableSlot(value, 10)})`
}

export const VIZ_CHIP_FG = "var(--viz-chip-fg)"

function stableSlot(value: string, slots: number): number {
  let h = 0
  for (let i = 0; i < value.length; i += 1) {
    h = ((h << 5) - h + value.charCodeAt(i)) | 0
  }
  return (((h % slots) + slots) % slots) + 1
}

// ── Resolved palettes for non-CSS renderers ─────────────────────────────────
// WebGL surfaces (sigma) can't consume `var(...)` / `oklch()` / `color-mix()`
// strings — they need concrete parsed colors. These helpers resolve the live
// theme variables through the browser's own cascade (probe element) and
// normalize through a 2d canvas so the output is always a plain hex/rgb the
// GL color parser accepts.

let resolverCanvas: HTMLCanvasElement | null = null

/** Resolve any CSS color expression (var refs, oklch, color-mix) to a concrete
 *  color string. Returns null when it can't (SSR, missing var). */
export function resolveCssColor(expr: string): string | null {
  if (typeof window === "undefined" || !document?.body) return null
  const probe = document.createElement("span")
  probe.style.display = "none"
  probe.style.color = expr
  document.body.appendChild(probe)
  const computed = getComputedStyle(probe).color
  probe.remove()
  if (!computed) return null
  resolverCanvas ??= document.createElement("canvas")
  const ctx = resolverCanvas.getContext("2d")
  if (!ctx) return computed
  ctx.fillStyle = "#010203"
  ctx.fillStyle = computed
  const normalized = ctx.fillStyle
  return normalized && normalized !== "#010203" ? normalized : computed
}

/** The theme's --viz-kind-1..10 resolved to concrete colors; `fallback` when
 *  any slot fails to resolve (so a palette is never half-themed). */
export function resolveVizKindPalette(fallback: string[]): string[] {
  const out: string[] = []
  for (let i = 1; i <= 10; i += 1) {
    const color = resolveCssColor(`var(--viz-kind-${i})`)
    if (!color) return fallback
    out.push(color)
  }
  return out
}

/** The theme's --viz-series-1..9 resolved to concrete colors. */
export function resolveVizSeriesPalette(fallback: string[]): string[] {
  const out: string[] = []
  for (let i = 1; i <= 9; i += 1) {
    const color = resolveCssColor(`var(--viz-series-${i})`)
    if (!color) return fallback
    out.push(color)
  }
  return out
}
