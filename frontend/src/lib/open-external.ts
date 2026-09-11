import { openUrl } from "@tauri-apps/plugin-opener"

export async function openExternalUrl(url: string) {
  if ("__TAURI_INTERNALS__" in window) {
    await openUrl(url)
    return
  }
  const opened = window.open(url, "_blank", "noopener,noreferrer")
  if (opened === null) throw new Error("The browser blocked the sign-in window")
}
