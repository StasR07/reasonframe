export const AI_MODEL_KEY = "finance-terminal:ai-search-model:v2"

export function persistedAIModel() {
  try { return localStorage.getItem(AI_MODEL_KEY) } catch { return null }
}

export function persistAIModel(model: string) {
  try { localStorage.setItem(AI_MODEL_KEY, model) } catch { /* selection remains usable for this page */ }
}
