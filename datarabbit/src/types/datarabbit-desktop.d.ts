type DataRabbitMenuAction =
  | "new-sql"
  | "finder"
  | "connections"
  | "scenes"
  | "saved-views"
  | "notifications"
  | "capabilities"
  | "command-palette"
  | "scry"
  | "tile"
  | "workspace-1"
  | "workspace-2"
  | "workspace-3"
  | "workspace-4"
  | "workspace-5"
  | "workspace-scene"

interface Window {
  dataRabbitDesktop?: {
    readonly isDesktopApp: true
    readonly platform: string
    readonly electronVersion: string
    captureSceneSnapshot(): Promise<string | null>
    onMenuAction(listener: (action: DataRabbitMenuAction) => void): () => void
  }
}
