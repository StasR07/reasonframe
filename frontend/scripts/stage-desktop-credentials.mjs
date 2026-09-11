import { chmodSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const repository = resolve(scriptDirectory, "../..")
const resourceDirectory = resolve(repository, "frontend/src-tauri/resources")

function readDotenv() {
  try {
    const values = {}
    for (const line of readFileSync(resolve(repository, ".env"), "utf8").split(/\r?\n/)) {
      const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/)
      if (!match) continue
      let value = match[2]
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1)
      }
      values[match[1]] = value
    }
    return values
  } catch (error) {
    if (error?.code === "ENOENT") return {}
    throw error
  }
}

export function stageDesktopCredentials() {
  const dotenv = readDotenv()
  const credentials = {
    "fred-api-key": (process.env.FRED_API_KEY ?? dotenv.FRED_API_KEY ?? "").trim(),
    "edgar-identity": (process.env.EDGAR_IDENTITY ?? dotenv.EDGAR_IDENTITY ?? "").trim(),
  }
  const missing = Object.entries(credentials).filter(([, value]) => !value).map(([name]) => name)
  if (missing.length) {
    throw new Error(`Desktop release credentials are missing: ${missing.join(", ")}`)
  }
  mkdirSync(resourceDirectory, { recursive: true, mode: 0o700 })
  for (const [name, value] of Object.entries(credentials)) {
    const destination = resolve(resourceDirectory, name)
    writeFileSync(destination, `${value}\n`, { encoding: "utf8", mode: 0o600 })
    chmodSync(destination, 0o600)
  }
  console.log("Staged bundled FRED and SEC release defaults.")
}
