export type Frequency = "annual" | "quarterly"
export type ResultStatus = "SUCCESS" | "UNAVAILABLE" | "UNSUPPORTED" | "INVALID"

export interface Company {
  ticker: string
  cik: string
  name: string
  fiscal_year_end: string | null
  support_status: string
}

export interface MetricDefinition {
  code: string
  label: string
  category: "income" | "cash_flow" | "profitability" | "balance"
  unit_kind: string
  kind: "direct" | "derived"
}

export interface MetricSupport {
  ticker: string
  metric: string
  frequency: Frequency
}

export interface MacroSeriesDefinition {
  code: string
  label: string
  unit: string
  frequency: string
  provider_series_id: string | null
}

export interface Catalog {
  data_revision: string
  companies: Company[]
  company_aliases?: Record<string, string>
  company_metrics: string[]
  company_metric_definitions: MetricDefinition[]
  company_metric_support: MetricSupport[]
  macro_series: MacroSeriesDefinition[]
  market_instruments?: Array<{ instrument_id: string; company_ticker: string; symbol: string; exchange_mic: string; currency: string; share_class: string | null; is_primary: number; valuation_status: string }>
  market_support?: MarketSupport[]
  market_metric_definitions?: Array<{ code: string; label: string; unit_kind: string }>
  valuation_metric_definitions?: Array<{ code: string; label: string; unit_kind: string }>
  valuation_support?: Array<{ ticker: string; instrument_id: string; status: string }>
  features?: { ai_search: boolean }
  market_provider?: "TIINGO"
  provider_connected?: boolean
  market_connection_state?: "CONNECTED" | "NOT_CONNECTED" | "INVALID_TOKEN" | "RATE_LIMITED"
  market_attribution?: string
}

export interface MarketSupport {
  ticker: string
  instrument_id: string
  first_date: string | null
  last_date: string | null
  observation_count: number
}

export interface AIAccountStatus {
  provider: "chatgpt_codex" | "claude_code"
  connected: boolean
  state: "CONNECTED" | "SIGN_IN_REQUIRED" | "RUNTIME_ERROR" | "NOT_INSTALLED" | "UNSUPPORTED"
  plan_type: string | null
  model: string | null
  installed?: boolean
  supported?: boolean
  message?: string | null
}

export interface AIConnectResult {
  provider: "chatgpt_codex" | "claude_code"
  connected: boolean
  auth_url: string | null
}

export type DataSourceState = "NOT_STARTED" | "NOT_CONFIGURED" | "UPDATING" | "UP_TO_DATE" | "PARTIALLY_READY" | "RATE_LIMITED" | "OFFLINE" | "FAILED"
export interface DataSourceStatus {
  status: DataSourceState
  completed: number
  total: number
  last_attempted_refresh: string | null
  last_successful_refresh: string | null
  message: string | null
}
export interface DataSyncStatus {
  operation: "IDLE" | "BOOTSTRAP" | "REFRESH"
  busy: boolean
  sources: { market: DataSourceStatus; sec: DataSourceStatus; macro: DataSourceStatus }
  market_pending: string[]
  onboarding_ready: boolean
  latest_market_date: string | null
  market_ready: number
  market_total: number
  tiingo_configured: boolean
  fred_configured: boolean
  sec_configured: boolean
  automatic_refresh: boolean
}
export interface RuntimeStatus {
  onboarding_completed: boolean
  automatic_refresh: boolean
  data: DataSyncStatus
}

export interface AIModel {
  id: string
  name: string
  description: string
  is_default: boolean
}

export interface Evidence {
  id: string
  metric: string
  label: string
  value: string
  unit: string
  source_type: "SEC" | "FRED" | "MARKET"
  source_concept: string | null
  accession_number: string | null
  filing_form: string | null
  filing_date: string | null
  period_start: string | null
  period_end: string
  derivation: string | null
  provider_series_id: string | null
  provider?: string | null
  instrument_id?: string | null
  symbol?: string | null
  exchange_mic?: string | null
  market_field?: string | null
  retrieved_at?: string | null
  action_type?: string | null
  date_type?: string | null
}

export interface SeriesPoint {
  date: string
  value: string
  point_type: "SOURCE" | "CALCULATED"
  observation_id: string | null
  formula: string | null
  input_observation_ids: string[]
  fiscal_year: number | null
  fiscal_period: string | null
  evidence: Evidence[]
  state?: "AVAILABLE" | "UNAVAILABLE" | "NOT_MEANINGFUL"
  valuation_basis?: string | null
  source_fiscal_year?: number | null
  availability_date?: string | null
}

export interface SeriesResult {
  id: string
  label: string
  entity: string | null
  metric: string
  unit: string
  frequency: string
  observations: SeriesPoint[]
  state?: "AVAILABLE" | "UNAVAILABLE" | "NOT_MEANINGFUL"
  context?: Record<string, unknown>
}

export interface QueryResponse {
  status: ResultStatus
  series: SeriesResult[]
  errors: string[]
}

export interface CompanyQuery {
  domain: "company"
  tickers: string[]
  metric: string
  frequency: Frequency
  start_year?: number
  end_year?: number
  operation?: "LEVEL" | "YOY_GROWTH" | "QOQ_GROWTH" | "AVERAGE"
  ranking?: "NONE" | "HIGHEST" | "LOWEST"
  ranking_limit?: number
  universe_ranking?: boolean
}

export interface MacroQuery {
  domain: "macro"
  series: string[]
  start_date?: string
  end_date?: string
  operation?: "LEVEL" | "AVERAGE"
}

export interface MarketQuery {
  domain: "market"
  tickers: string[]
  series: "RAW_CLOSE" | "ADJUSTED_CLOSE"
  view?: "latest" | "history"
  start_date?: string
  end_date?: string
  operation?: "LEVEL" | "RETURN" | "INDEXED" | "MAX_DRAWDOWN"
}

export interface ValuationQuery {
  domain: "valuation"
  tickers: string[]
  metric: "PE_RATIO" | "PS_RATIO" | "P_FCF_RATIO" | "FCF_YIELD"
  view?: "latest" | "history"
  start_year?: number
  end_year?: number
}

export type QueryRequest = CompanyQuery | MacroQuery | MarketQuery | ValuationQuery

export interface Interpretation {
  domain: "company" | "macro" | "market" | "valuation"
  entities: string[]
  metric: string
  frequency: string | null
  range: string
  operation: string
}

export interface FactualClaim { text: string; evidence_refs: string[] }
export interface FactualSummary { claims: FactualClaim[] }
export interface ClarificationChoice { label: string; question: string }

export interface AskSuccessResponse {
  status: "success"
  question: string
  interpretation: Interpretation
  validated_query: QueryRequest
  results: QueryResponse
  factual_summary: FactualSummary | null
}

export interface AskUnsupportedResponse {
  status: "unsupported"
  question: string
  reason_code: "UNSUPPORTED_ENTITY" | "UNSUPPORTED_METRIC" | "REQUIRES_MARKET_DATA" | "INVESTMENT_ADVICE" | "OPEN_ENDED_ANALYSIS" | "CAUSAL_EXPLANATION" | "OUT_OF_SCOPE" | "INVALID_COMBINATION"
  message: string
}

export interface AskClarificationResponse {
  status: "clarification"
  question: string
  message: string
  choices: ClarificationChoice[]
}

export interface AskErrorResponse {
  status: "error"
  question: string
  error_code: "AI_DISCONNECTED" | "AI_UNAVAILABLE" | "AI_USAGE_LIMIT" | "AI_TIMEOUT" | "MODEL_OUTPUT_INVALID" | "QUERY_FAILED"
  message: string
}

export type AskResponse = AskSuccessResponse | AskUnsupportedResponse | AskClarificationResponse | AskErrorResponse

export interface AnalystClaim { text: string; evidence_refs: string[] }
export interface FocusedInterpretation { observations: AnalystClaim[]; caveat: string | null }
export interface ComparisonAnalysis {
  confidence?: AnalystConfidence
  summary: AnalystClaim
  fundamentals_comparison: AnalystClaim[]
  valuation_comparison: AnalystClaim[]
  market_context: AnalystClaim[]
  key_tradeoffs: AnalystClaim[]
  limitations: string[]
}
export type AnalystStance = "POSITIVE" | "NEUTRAL" | "NEGATIVE"
export type AnalystConfidence = "LOW" | "MODERATE" | "HIGH"
export interface AnalystAssessment {
  stance: AnalystStance
  confidence: AnalystConfidence
  thesis: AnalystClaim
  supporting_claims: AnalystClaim[]
  uncertainties: string[]
}
export type SkepticAssessment = "NO_MATERIAL_CHALLENGE" | "QUALIFIED" | "MATERIAL_CHALLENGE"
export interface SkepticChallenge {
  target: "FUNDAMENTALS" | "VALUATION" | "BOTH"
  issue_type: "COUNTEREVIDENCE" | "WEAK_SUPPORT" | "OVERSTATEMENT" | "CONTRADICTION" | "CROSS_ANALYST_TENSION"
  text: string
  evidence_refs: string[]
}
export interface SkepticReview { assessment: SkepticAssessment; challenges: SkepticChallenge[]; missing_evidence: string[] }
export interface OverviewOutput {
  stance: AnalystStance
  confidence: AnalystConfidence
  synthesis: AnalystClaim
  key_conclusions: AnalystClaim[]
  key_risks: AnalystClaim[]
  uncertainties: string[]
}
export interface AnalysisError {
  status: "error"
  error_code: "AI_DISCONNECTED" | "AI_UNAVAILABLE" | "AI_USAGE_LIMIT" | "AI_TIMEOUT" | "MODEL_OUTPUT_INVALID" | "DATA_UNAVAILABLE"
  message: string
}
export interface FocusedAnalysisSuccess {
  status: "success"; mode: "focused"; packet_version: number; cache_key: string
  interpretation: FocusedInterpretation; evidence_context: SeriesResult[]
}
export interface ComparisonAnalysisSuccess {
  status: "success"; mode: "comparison"; packet_version: number; cache_key: string
  interpretation: ComparisonAnalysis; evidence_context: SeriesResult[]
}
export interface CompanyAnalysisSuccess {
  status: "success"; mode: "company"; packet_version: number; cache_key: string; ticker: string
  fundamentals: AnalystAssessment | null; valuation: AnalystAssessment | null; skeptic: SkepticReview | null
  overview: OverviewOutput | null; failures: string[]; failure_details?: Record<string, string>; evidence_context: SeriesResult[]
}
export type AnalysisResponse = FocusedAnalysisSuccess | ComparisonAnalysisSuccess | CompanyAnalysisSuccess | AnalysisError

export type CompanyStage = "fundamentals" | "valuation" | "skeptic" | "overview"
export type CompanyStageState = "Waiting" | "Running" | "Complete" | "Failed" | "Unavailable" | "Skipped"
export interface CompanyAnalysisEvent {
  event: "analysis_started" | "stage_started" | "stage_completed" | "stage_failed" | "stage_skipped" | "analysis_completed" | "analysis_failed"
  stage: CompanyStage | null
  result: AnalystAssessment | SkepticReview | OverviewOutput | CompanyAnalysisSuccess | AnalysisError | null
  message: string | null
  evidence_context?: SeriesResult[]
}
