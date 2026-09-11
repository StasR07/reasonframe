import { describe, expect, it } from "vitest"
import { connectionMessageText } from "./app-shell"

describe("ChatGPT connection polling messages", () => {
  it("distinguishes login timeout from service failure", () => {
    expect(connectionMessageText("timeout")).toBe("Login timed out - try connecting again.")
    expect(connectionMessageText("service")).toBe("Connection service unavailable.")
  })
})
