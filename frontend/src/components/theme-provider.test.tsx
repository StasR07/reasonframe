import { fireEvent, render, screen } from "@testing-library/react"
import { beforeEach, expect, it, vi } from "vitest"
import { ThemeProvider, useTheme } from "./theme-provider"

function Controls() {
  const { theme, setTheme } = useTheme()
  return <><span data-testid="theme">{theme}</span><button onClick={() => setTheme("light")}>Light</button><button onClick={() => setTheme("dark")}>Dark</button><button onClick={() => setTheme("system")}>System</button></>
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.className = ""
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
})

it("defaults to System and persists explicit Light and Dark choices", () => {
  const { unmount } = render(<ThemeProvider><Controls/></ThemeProvider>)
  expect(screen.getByTestId("theme")).toHaveTextContent("system")
  fireEvent.click(screen.getByRole("button", { name: "Dark" }))
  expect(document.documentElement).toHaveClass("dark")
  expect(localStorage.getItem("stas-finance-theme")).toBe("dark")
  fireEvent.click(screen.getByRole("button", { name: "Light" }))
  expect(document.documentElement).not.toHaveClass("dark")
  expect(localStorage.getItem("stas-finance-theme")).toBe("light")
  unmount()
  render(<ThemeProvider><Controls/></ThemeProvider>)
  expect(screen.getByTestId("theme")).toHaveTextContent("light")
})

it("System follows operating-system preference changes", () => {
  let listener: (() => void) | undefined
  const media = { matches: true, addEventListener: vi.fn((_event, next) => { listener = next }), removeEventListener: vi.fn() }
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue(media))
  render(<ThemeProvider><Controls/></ThemeProvider>)
  expect(document.documentElement).toHaveClass("dark")
  media.matches = false
  listener?.()
  expect(document.documentElement).not.toHaveClass("dark")
})

