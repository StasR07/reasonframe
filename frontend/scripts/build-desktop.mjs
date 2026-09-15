import { homedir } from "node:os"
import { resolve } from "node:path"
import { spawn } from "node:child_process"

const tauri = resolve("node_modules/.bin/tauri")
const remap = `--remap-path-prefix=${homedir()}=/Users/builder`
const rustflags = [process.env.RUSTFLAGS, remap].filter(Boolean).join(" ")
const child = spawn(tauri, ["build"], {
  env: { ...process.env, RUSTFLAGS: rustflags },
  stdio: "inherit",
})

child.on("error", (error) => {
  console.error(error.message)
  process.exitCode = 1
})
child.on("exit", (code, signal) => {
  if (signal) console.error(`Tauri build stopped by ${signal}`)
  process.exitCode = code ?? 1
})
