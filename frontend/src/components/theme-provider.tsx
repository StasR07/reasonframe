import { createContext, useContext, useLayoutEffect, useState, type ReactNode } from "react"

export type Theme = "system" | "light" | "dark"

const STORAGE_KEY = "stas-finance-theme"

function storedTheme(): Theme {
  const value = localStorage.getItem(STORAGE_KEY)
  return value === "light" || value === "dark" || value === "system" ? value : "system"
}

function applyTheme(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && Boolean(window.matchMedia?.("(prefers-color-scheme: dark)").matches))
  document.documentElement.classList.toggle("dark", dark)
  document.documentElement.style.colorScheme = dark ? "dark" : "light"
}

const ThemeContext = createContext<{ theme: Theme; setTheme: (theme: Theme) => void } | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(storedTheme)

  useLayoutEffect(() => {
    applyTheme(theme)
    const media = window.matchMedia?.("(prefers-color-scheme: dark)")
    const updateSystemTheme = () => { if (theme === "system") applyTheme(theme) }
    media?.addEventListener("change", updateSystemTheme)
    return () => media?.removeEventListener("change", updateSystemTheme)
  }, [theme])

  const setTheme = (next: Theme) => {
    localStorage.setItem(STORAGE_KEY, next)
    setThemeState(next)
  }

  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) throw new Error("useTheme must be used within ThemeProvider")
  return context
}
