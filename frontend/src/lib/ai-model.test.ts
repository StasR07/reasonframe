import { describe, expect, it } from "vitest"
import { AI_MODEL_KEY, persistedAIModel, persistAIModel } from "./ai-model"

describe("AI model preference storage", () => {
  it("persists the selected discovered model locally", () => {
    persistAIModel("discovered-model-id")
    expect(persistedAIModel()).toBe("discovered-model-id")
    expect(localStorage.getItem(AI_MODEL_KEY)).toBe("discovered-model-id")
  })
})
