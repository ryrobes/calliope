"use strict"
/* eslint-disable @typescript-eslint/no-require-imports */

const { contextBridge, ipcRenderer } = require("electron")

const allowedMenuActions = new Set([
  "new-sql",
  "finder",
  "connections",
  "scenes",
  "saved-views",
  "notifications",
  "capabilities",
  "command-palette",
  "scry",
  "tile",
  "workspace-1",
  "workspace-2",
  "workspace-3",
  "workspace-4",
  "workspace-5",
  "workspace-scene",
])

window.addEventListener("DOMContentLoaded", () => {
  document.documentElement.dataset.datarabbitDesktop = "true"
  document.documentElement.dataset.datarabbitPlatform = process.platform
})

contextBridge.exposeInMainWorld(
  "dataRabbitDesktop",
  Object.freeze({
    isDesktopApp: true,
    platform: process.platform,
    electronVersion: process.versions.electron,
    async captureSceneSnapshot() {
      const value = await ipcRenderer.invoke("datarabbit:capture-scene")
      return typeof value === "string" ? value : null
    },
    onMenuAction(listener) {
      if (typeof listener !== "function") return () => {}

      const handler = (_event, action) => {
        if (allowedMenuActions.has(action)) listener(action)
      }

      ipcRenderer.on("datarabbit:menu-action", handler)
      return () => ipcRenderer.removeListener("datarabbit:menu-action", handler)
    },
  }),
)
