"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import CodeMirror from "@uiw/react-codemirror"
import { yaml as yamlLang } from "@codemirror/lang-yaml"
import {
  BookOpen,
  CheckCircle2,
  FileCode2,
  FileText,
  Loader2,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Shield,
  Sparkles,
  User,
  XCircle,
} from "@/lib/icons"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { rvbbitLensCodeMirrorTheme } from "@/lib/desktop/codemirror-theme"

type DocumentKind = "profile" | "config" | "soul"
type ViewMode = "skills" | DocumentKind

const VIEW_MODE_STORAGE_KEY = "rvbbit.agent-skills.view-mode"
const VIEW_MODES = new Set<ViewMode>(["skills", "profile", "config", "soul"])

function storedViewMode(): ViewMode {
  if (typeof window === "undefined") return "skills"
  try {
    const value = window.localStorage.getItem(VIEW_MODE_STORAGE_KEY) as ViewMode | null
    return value && VIEW_MODES.has(value) ? value : "skills"
  } catch {
    return "skills"
  }
}

function skillOriginRank(origin: AgentSkill["origin"]): number {
  // A hollow copy (zero-byte SKILL.md) is a defect the operator must see first.
  return origin === "empty" ? -1 : origin === "custom" ? 0 : origin === "customized" ? 1 : 2
}

interface AgentProfile {
  key: string
  name: string
  description: string
  skills_total: number
  skills_enabled: number
  config_hash: string
  soul_hash?: string | null
  profile_hash?: string | null
  role_label?: string
  signature_font?: string | null
  avatar_file?: string | null
  avatar_day_file?: string | null
  config_user_managed?: boolean
}

interface AgentSkill {
  name: string
  description: string
  enabled: boolean
  path: string
  category: string
  content_hash: string
  body_preview: string
  content?: string
  origin: "builtin" | "customized" | "custom" | "empty"
  is_custom: boolean
  empty?: boolean
}

interface ProfileSnapshot {
  profile: AgentProfile
  skills: AgentSkill[]
}

interface ProfileDocument {
  profile: string
  document: DocumentKind
  content: string
  content_hash: string
  user_managed?: boolean
}

async function responseJson(response: Response): Promise<Record<string, unknown>> {
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok) {
    const wrapped = body.error as { message?: string } | string | undefined
    const validation = body.validation as { errors?: Array<{ path?: string; message?: string; hint?: string }> } | undefined
    const issue = validation?.errors?.[0]
    const issueMessage = issue?.message
      ? `${issue.path ? `${issue.path}: ` : ""}${issue.message}${issue.hint ? ` — ${issue.hint}` : ""}`
      : ""
    const message = typeof wrapped === "string" ? wrapped : wrapped?.message
    const phase = typeof body.phase === "string" ? body.phase.replaceAll("_", " ") : ""
    throw new Error(message || issueMessage || (phase ? `${phase} failed` : `HTTP ${response.status}`))
  }
  return body
}

function adminFetch(path: string, body: Record<string, unknown>) {
  return fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Calliope-Admin": "1" },
    body: JSON.stringify(body),
  })
}

export function AgentSkillsWindow() {
  const [profiles, setProfiles] = useState<AgentProfile[]>([])
  const [profileKey, setProfileKey] = useState("default")
  const [snapshot, setSnapshot] = useState<ProfileSnapshot | null>(null)
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null)
  const [skillContent, setSkillContent] = useState<string>("")
  const [mode, setMode] = useState<ViewMode>(storedViewMode)
  const [query, setQuery] = useState("")
  const [document, setDocument] = useState<ProfileDocument | null>(null)
  const [draft, setDraft] = useState("")
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [restartRequired, setRestartRequired] = useState(false)

  const loadProfiles = useCallback(async () => {
    const response = await fetch("/api/calliope/admin/hermes/profiles", {
      credentials: "same-origin", cache: "no-store",
    })
    const body = await responseJson(response)
    const next = (body.profiles || []) as AgentProfile[]
    setProfiles(next)
    if (next.length && !next.some((item) => item.key === profileKey)) setProfileKey(next[0].key)
  }, [profileKey])

  const loadProfile = useCallback(async (key: string) => {
    const response = await fetch(`/api/calliope/admin/hermes/profile?profile=${encodeURIComponent(key)}`, {
      credentials: "same-origin", cache: "no-store",
    })
    const body = await responseJson(response) as unknown as ProfileSnapshot
    setSnapshot(body)
    setSelectedSkill((current) => body.skills.some((item) => item.name === current) ? current : body.skills[0]?.name || null)
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      await Promise.all([loadProfiles(), loadProfile(profileKey)])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }, [loadProfile, loadProfiles, profileKey])

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0)
    return () => window.clearTimeout(timer)
  }, [refresh])

  useEffect(() => {
    try {
      window.localStorage.setItem(VIEW_MODE_STORAGE_KEY, mode)
    } catch {
      // Private/restricted browser storage should not block the editor itself.
    }
  }, [mode])

  useEffect(() => {
    if (mode !== "skills" || !selectedSkill) return
    let cancelled = false
    void (async () => {
      try {
        const response = await fetch(
          `/api/calliope/admin/hermes/profile/skill?profile=${encodeURIComponent(profileKey)}&skill=${encodeURIComponent(selectedSkill)}`,
          { credentials: "same-origin", cache: "no-store" },
        )
        const body = await responseJson(response) as { skill?: AgentSkill }
        if (!cancelled) setSkillContent(body.skill?.content || "")
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause))
      }
    })()
    return () => { cancelled = true }
  }, [mode, profileKey, selectedSkill])

  const loadDocument = useCallback(async (kind: DocumentKind) => {
    setBusy(`load-${kind}`)
    setError(null)
    try {
      const response = await fetch(
        `/api/calliope/admin/hermes/profile/document?profile=${encodeURIComponent(profileKey)}&document=${kind}`,
        { credentials: "same-origin", cache: "no-store" },
      )
      const body = await responseJson(response) as unknown as ProfileDocument
      setDocument(body)
      setDraft(body.content)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(null)
    }
  }, [profileKey])

  useEffect(() => {
    if (mode === "skills") return
    const timer = window.setTimeout(() => void loadDocument(mode), 0)
    return () => window.clearTimeout(timer)
  }, [loadDocument, mode])

  const filteredSkills = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matching = !needle ? [...(snapshot?.skills || [])] : (snapshot?.skills || []).filter((skill) =>
      `${skill.name} ${skill.description} ${skill.category}`.toLowerCase().includes(needle),
    )
    return matching.sort((left, right) =>
      skillOriginRank(left.origin) - skillOriginRank(right.origin)
      || left.name.localeCompare(right.name),
    )
  }, [query, snapshot?.skills])

  const selected = snapshot?.skills.find((skill) => skill.name === selectedSkill) || null
  const dirty = Boolean(document && draft !== document.content)

  const toggleSkill = useCallback(async (skill: AgentSkill) => {
    setBusy(`skill:${skill.name}`)
    setError(null)
    setNotice(null)
    try {
      const response = await adminFetch("/api/calliope/admin/hermes/profile/skill", {
        profile: profileKey, skill: skill.name, enabled: !skill.enabled,
      })
      const body = await responseJson(response)
      setRestartRequired((current) => current || Boolean(body.restart_required))
      setNotice(`${skill.name} ${skill.enabled ? "disabled" : "enabled"}${body.restart_required ? " — restart when you are ready." : ". The verified gateway update is queued."}`)
      const enabled = !skill.enabled
      setSnapshot((current) => current ? {
        ...current,
        profile: {
          ...current.profile,
          skills_enabled: Math.max(0, current.profile.skills_enabled + (enabled ? 1 : -1)),
        },
        skills: current.skills.map((item) => item.name === skill.name ? { ...item, enabled } : item),
      } : current)
      setProfiles((current) => current.map((profile) => profile.key === profileKey ? {
        ...profile,
        skills_enabled: Math.max(0, profile.skills_enabled + (enabled ? 1 : -1)),
      } : profile))
      // The toggle rewrote config.yaml. An open config editor now holds a stale
      // content_hash: refresh it when the draft is clean so the next save does
      // not fail its compare-and-swap; when the draft is dirty, say so instead
      // of silently rebasing the user's edits over the change.
      if (document?.document === "config") {
        if (draft === document.content) {
          await loadDocument("config")
        } else {
          setNotice((current) => `${current ? `${current} ` : ""}config.yaml changed on the server; reload the document before saving your draft.`)
        }
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(null)
    }
  }, [document, draft, loadDocument, profileKey])

  const saveDocument = useCallback(async () => {
    if (!document) return
    setBusy(`save-${document.document}`)
    setError(null)
    setNotice(null)
    try {
      const validateResponse = await adminFetch("/api/calliope/admin/hermes/profile/validate", {
        profile: profileKey, document: document.document, content: draft,
      })
      await responseJson(validateResponse)
      const response = await adminFetch("/api/calliope/admin/hermes/profile/document", {
        profile: profileKey,
        document: document.document,
        content: draft,
        expected_hash: document.content_hash,
        reason: `edited ${profileKey} ${document.document} in DataRabbit Agent Skills`,
      })
      const body = await responseJson(response)
      setRestartRequired((current) => current || Boolean(body.restart_required))
      setNotice(body.restart_required ? "Saved and versioned. Restart Hermes when you are ready." : "Saved. The verified gateway update is queued.")
      await Promise.all([loadDocument(document.document), loadProfile(profileKey), loadProfiles()])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(null)
    }
  }, [document, draft, loadDocument, loadProfile, loadProfiles, profileKey])

  const restart = useCallback(async () => {
    setBusy("restart")
    setError(null)
    setNotice(null)
    try {
      const response = await adminFetch("/api/calliope/admin/hermes/restart", {
        reason: "apply Agent Skills profile and skill changes",
      })
      await responseJson(response)
      setRestartRequired(false)
      setNotice("Verified Hermes restart queued. The controller will roll back automatically if health checks fail.")
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(null)
    }
  }, [])

  return (
    <div className="flex h-full min-h-0 flex-col bg-chrome-bg/15 text-[12px]">
      <header className="flex shrink-0 items-center gap-3 border-b border-chrome-border bg-chrome-bg/45 px-4 py-2.5">
        <div className="flex h-9 w-9 items-center justify-center rounded-full border border-rvbbit-accent/40 bg-rvbbit-accent/10 shadow-[0_0_22px_color-mix(in_oklch,var(--rvbbit-accent)_18%,transparent)]">
          <BookOpen className="h-4.5 w-4.5 text-rvbbit-accent" />
        </div>
        <div>
          <div className="font-semibold tracking-[0.02em] text-foreground">Agent Skills</div>
          <div className="text-[10px] text-chrome-text/55">profile capabilities · configuration · character</div>
        </div>
        <div className="flex-1" />
        {restartRequired ? (
          <span className="rounded-full border border-warning/35 bg-warning/10 px-2 py-1 text-[9px] uppercase tracking-[0.14em] text-warning">restart pending</span>
        ) : null}
        <Button size="sm" variant="outline" disabled={Boolean(busy)} onClick={() => void restart()}>
          {busy === "restart" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="mr-1.5 h-3.5 w-3.5" />}
          Restart safely
        </Button>
        <Button size="sm" variant="ghost" disabled={loading} onClick={() => void refresh()} title="Refresh">
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </Button>
      </header>

      {error ? <div className="flex shrink-0 items-center gap-2 border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-destructive"><XCircle className="h-3.5 w-3.5" />{error}</div> : null}
      {notice ? <div className="flex shrink-0 items-center gap-2 border-b border-success/30 bg-success/10 px-4 py-2 text-success"><CheckCircle2 className="h-3.5 w-3.5" />{notice}</div> : null}

      <div className="flex min-h-0 flex-1">
        <aside className="w-[220px] shrink-0 overflow-y-auto border-r border-chrome-border bg-background/15 p-2">
          <div className="px-2 pb-2 pt-1 text-[9px] uppercase tracking-[0.18em] text-chrome-text/45">Hermes profiles</div>
          <div className="space-y-1">
            {profiles.map((profile) => {
              const active = profile.key === profileKey
              return (
                <button
                  key={profile.key}
                  type="button"
                  onClick={() => {
                    setProfileKey(profile.key)
                    setSnapshot(null)
                    setSelectedSkill(null)
                    setSkillContent("")
                    setDocument(null)
                    setNotice(null)
                  }}
                  className={cn(
                    "group relative w-full overflow-hidden rounded-md border px-2.5 py-2.5 text-left transition",
                    active ? "border-rvbbit-accent/45 bg-rvbbit-accent/10" : "border-transparent hover:border-chrome-border hover:bg-background/25",
                  )}
                >
                  {active ? <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-rvbbit-accent" /> : null}
                  <div className="flex items-center gap-2.5">
                    <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-full border font-serif text-[13px]", active ? "border-rvbbit-accent/40 text-rvbbit-accent" : "border-chrome-border text-chrome-text/65")}>
                      {profile.avatar_file || profile.avatar_day_file ? (
                        <img className="h-full w-full object-cover" src={`/calliope/specialists/${encodeURIComponent(profile.avatar_file || profile.avatar_day_file || "")}`} alt="" />
                      ) : profile.name.slice(0, 1)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium text-foreground">{profile.name}</div>
                      <div className="mt-0.5 truncate font-mono text-[9px] text-chrome-text/45">{profile.key}</div>
                    </div>
                  </div>
                  <div className="mt-2 flex items-center gap-1.5 text-[9px] text-chrome-text/50">
                    <span>{profile.skills_enabled}/{profile.skills_total} enabled</span>
                    {profile.config_user_managed ? <span className="rounded border border-chrome-border px-1">custom yaml</span> : null}
                  </div>
                </button>
              )
            })}
          </div>
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex shrink-0 items-center gap-1 border-b border-chrome-border bg-background/10 px-3 py-2">
            <ModeButton active={mode === "skills"} icon={Sparkles} label="Skills" onClick={() => setMode("skills")} />
            <ModeButton active={mode === "profile"} icon={User} label="Identity YAML" onClick={() => setMode("profile")} />
            <ModeButton active={mode === "config"} icon={FileCode2} label="Runtime YAML" onClick={() => setMode("config")} />
            <ModeButton active={mode === "soul"} icon={FileText} label="SOUL.md" onClick={() => setMode("soul")} />
            <div className="flex-1" />
            <div className="max-w-[440px] truncate text-right text-[10px] text-chrome-text/50">{snapshot?.profile.description}</div>
          </div>

          {mode === "skills" ? (
            <div className="flex min-h-0 flex-1">
              <section className="flex w-[42%] min-w-[310px] flex-col border-r border-chrome-border">
                <div className="relative shrink-0 border-b border-chrome-border p-3">
                  <Search className="pointer-events-none absolute left-5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-chrome-text/40" />
                  <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find an installed skill…" className="h-8 pl-8 text-[11px]" />
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-2">
                  {filteredSkills.map((skill, index) => {
                    const firstLocal = index === 0 && skill.origin !== "builtin"
                    const firstBuiltin = skill.origin === "builtin" && filteredSkills[index - 1]?.origin !== "builtin"
                    const locallyAuthored = skill.origin === "custom"
                    return (
                      <div key={skill.name}>
                        {firstLocal ? (
                          <div className="mb-1.5 flex items-center gap-2 px-2 text-[8px] uppercase tracking-[0.18em] text-warning/70">
                            <span>Created or changed here</span><span className="h-px flex-1 bg-warning/20" />
                          </div>
                        ) : null}
                        {firstBuiltin ? (
                          <div className="mb-1.5 mt-3 flex items-center gap-2 px-2 text-[8px] uppercase tracking-[0.18em] text-chrome-text/35">
                            <span>Built in</span><span className="h-px flex-1 bg-chrome-border/70" />
                          </div>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => setSelectedSkill(skill.name)}
                          className={cn(
                            "mb-1 w-full rounded-md border p-3 text-left transition",
                            locallyAuthored && "bg-warning/[0.045]",
                            skill.origin === "customized" && "bg-rvbbit-accent/[0.025]",
                            selectedSkill === skill.name
                              ? locallyAuthored ? "border-warning/45 bg-warning/[0.09]" : "border-rvbbit-accent/45 bg-rvbbit-accent/8"
                              : locallyAuthored ? "border-warning/20 hover:border-warning/35 hover:bg-warning/[0.07]" : "border-transparent hover:border-chrome-border hover:bg-background/20",
                          )}
                        >
                          <div className="flex items-start gap-2">
                            <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", skill.enabled ? "bg-success shadow-[0_0_8px_color-mix(in_oklch,var(--success)_65%,transparent)]" : "bg-chrome-text/25")} />
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <span className="truncate font-mono text-[11px] text-foreground">{skill.name}</span>
                                {locallyAuthored ? <span className="rounded border border-warning/30 bg-warning/10 px-1 text-[8px] uppercase tracking-wider text-warning">local</span> : null}
                                {skill.origin === "customized" ? <span className="rounded border border-rvbbit-accent/25 bg-rvbbit-accent/[0.07] px-1 text-[8px] uppercase tracking-wider text-rvbbit-accent">edited</span> : null}
                                {skill.origin === "empty" ? <span title="SKILL.md is empty — the agent loads nothing; the boot reconciler reseeds shipped skills" className="rounded border border-destructive/40 bg-destructive/10 px-1 text-[8px] uppercase tracking-wider text-destructive">empty</span> : null}
                                <span className="rounded border border-chrome-border px-1 text-[8px] uppercase tracking-wider text-chrome-text/45">{skill.category}</span>
                              </div>
                              <div className="mt-1 line-clamp-2 text-[10px] leading-4 text-chrome-text/55">{skill.description || skill.body_preview}</div>
                            </div>
                          </div>
                        </button>
                      </div>
                    )
                  })}
                  {!filteredSkills.length ? <div className="p-6 text-center text-chrome-text/45">No skills match this filter.</div> : null}
                </div>
              </section>

              <section className="flex min-w-0 flex-1 flex-col bg-background/10">
                {selected ? (
                  <>
                    <div className="shrink-0 border-b border-chrome-border px-4 py-3">
                      <div className="flex items-start gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="font-mono text-[13px] font-semibold text-foreground">{selected.name}</div>
                          <div className="mt-1 max-w-3xl text-[11px] leading-5 text-chrome-text/65">{selected.description}</div>
                          <div className="mt-2 font-mono text-[9px] text-chrome-text/35">{selected.path}</div>
                        </div>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={selected.enabled}
                          disabled={Boolean(busy)}
                          onClick={() => void toggleSkill(selected)}
                          className={cn("flex items-center gap-2 rounded-full border px-2.5 py-1.5 text-[10px] transition", selected.enabled ? "border-success/40 bg-success/10 text-success" : "border-chrome-border text-chrome-text/55")}
                        >
                          {busy === `skill:${selected.name}` ? <Loader2 className="h-3 w-3 animate-spin" /> : <span className={cn("relative h-3.5 w-6 rounded-full", selected.enabled ? "bg-success/50" : "bg-chrome-text/20")}><span className={cn("absolute top-0.5 h-2.5 w-2.5 rounded-full bg-foreground transition-all", selected.enabled ? "left-3" : "left-0.5")} /></span>}
                          {selected.enabled ? "Enabled" : "Disabled"}
                        </button>
                      </div>
                    </div>
                    <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap p-5 font-mono text-[11px] leading-5 text-chrome-text/75">{skillContent || "Loading skill instructions…"}</pre>
                  </>
                ) : <div className="flex flex-1 items-center justify-center text-chrome-text/45">Select a skill to inspect its instructions.</div>}
              </section>
            </div>
          ) : (
            <section className="flex min-h-0 flex-1 flex-col">
              <div className="flex shrink-0 items-center gap-3 border-b border-chrome-border px-4 py-2.5">
                <Shield className="h-3.5 w-3.5 text-rvbbit-accent" />
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-foreground">{
                    mode === "profile" ? `${snapshot?.profile.name || profileKey} · profile.yaml`
                      : mode === "config" ? `${snapshot?.profile.name || profileKey} · config.yaml`
                        : `${snapshot?.profile.name || profileKey} · SOUL.md`
                  }</div>
                  <div className="text-[10px] text-chrome-text/50">
                    {mode === "profile"
                      ? "Display name, use-case label, signature font, and avatar asset used throughout Calliope."
                      : mode === "config"
                        ? "Validated against pinned Hermes and the hosted profile invariants before save."
                        : "The agent’s durable identity, temperament, and working principles."}
                  </div>
                </div>
                {dirty ? <span className="text-[9px] uppercase tracking-widest text-warning">unsaved</span> : null}
                <Button size="sm" variant="ghost" disabled={!dirty || Boolean(busy)} onClick={() => { if (document) setDraft(document.content) }}>
                  <RotateCcw className="mr-1 h-3.5 w-3.5" /> Revert
                </Button>
                <Button size="sm" disabled={!dirty || Boolean(busy)} onClick={() => void saveDocument()}>
                  {busy?.startsWith("save-") ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Save className="mr-1 h-3.5 w-3.5" />}
                  Validate & save
                </Button>
              </div>
              <div className="min-h-0 flex-1 bg-background/20 text-[12px]">
                {busy === `load-${mode}` && !document ? (
                  <div className="flex h-full items-center justify-center gap-2 text-chrome-text/50"><Loader2 className="h-4 w-4 animate-spin" /> loading {mode}…</div>
                ) : (
                  <CodeMirror
                    value={draft}
                    height="100%"
                    theme="none"
                    basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true }}
                    extensions={[...(mode !== "soul" ? [yamlLang()] : []), ...rvbbitLensCodeMirrorTheme]}
                    onChange={setDraft}
                    className="h-full [&_.cm-editor]:h-full"
                  />
                )}
              </div>
            </section>
          )}
        </main>
      </div>
    </div>
  )
}

function ModeButton({ active, icon: Icon, label, onClick }: { active: boolean; icon: typeof Sparkles; label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={cn("flex items-center gap-1.5 rounded px-2.5 py-1.5 text-[10px] font-medium transition", active ? "bg-rvbbit-accent/12 text-rvbbit-accent" : "text-chrome-text/55 hover:bg-background/25 hover:text-foreground")}>
      <Icon className="h-3.5 w-3.5" />{label}
    </button>
  )
}
