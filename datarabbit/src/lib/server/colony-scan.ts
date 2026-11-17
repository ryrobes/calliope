import "server-only"

/**
 * Colony's "Scan for local models" (docs/PEER_CAPABILITIES_PLAN.md P2) —
 * explicit, on-demand only (never silent/background, per the plan's own
 * doctrine). Probes a small, bounded set of well-known local endpoints
 * for signatures unambiguous enough not to need real confidence math:
 * Ollama's /api/tags either answers with a real model list or the port
 * isn't Ollama; Gradio's /config either has Gradio's exact shape or it's
 * something else entirely. A manual "I know the URL" fallback (the
 * existing form) always remains for anything this misses.
 */

const PROBE_TIMEOUT_MS = 800

export interface ScanCandidate {
  shape: "openai_chat" | "gradio"
  suggestedName: string
  localUrl: string
  confidence: number
  detail: string
  /** Ollama only: real installed models, so the picker never has to guess. */
  models?: string[]
}

async function probeOllama(): Promise<ScanCandidate | null> {
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS)
    const res = await fetch("http://localhost:11434/api/tags", { signal: controller.signal }).finally(() =>
      clearTimeout(timer),
    )
    if (!res.ok) return null
    const body = (await res.json()) as { models?: Array<{ name?: string }> }
    const models = (body.models ?? []).map((m) => m.name).filter((n): n is string => !!n)
    return {
      shape: "openai_chat",
      suggestedName: "local_ollama",
      localUrl: "http://localhost:11434/v1/chat/completions",
      confidence: 1,
      detail: `Ollama, ${models.length} model${models.length === 1 ? "" : "s"} installed`,
      models,
    }
  } catch {
    return null
  }
}

async function probeGradio(port: number): Promise<ScanCandidate | null> {
  const localUrl = `http://127.0.0.1:${port}`
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS)
    const res = await fetch(`${localUrl}/config`, { signal: controller.signal }).finally(() => clearTimeout(timer))
    if (!res.ok) return null
    const body = (await res.json()) as { version?: string; mode?: string }
    if (!body.version) return null // not a Gradio /config shape
    return {
      shape: "gradio",
      suggestedName: `local_gradio_${port}`,
      localUrl,
      confidence: 1,
      detail: `Gradio ${body.version}${body.mode ? ` (${body.mode})` : ""} on port ${port}`,
    }
  } catch {
    return null
  }
}

// Gradio's default is 7860; apps commonly increment when that's taken.
// Bounded, explicit range — not a general port scan.
const GRADIO_PORT_RANGE = [7860, 7861, 7862, 7863, 7864, 7865]

export async function scanForLocalModels(): Promise<ScanCandidate[]> {
  // Each probe bounds itself via its own AbortController (PROBE_TIMEOUT_MS),
  // and they run in parallel, so the whole scan is bounded by that same
  // ~800ms regardless of how many candidate ports are checked.
  const results = await Promise.all([probeOllama(), ...GRADIO_PORT_RANGE.map((p) => probeGradio(p))])
  return results.filter((c): c is ScanCandidate => c !== null)
}
