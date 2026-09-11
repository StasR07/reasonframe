# Reasonframe

**Financial research with specialist AI analysts.**

Reasonframe is a local-first desktop application for researching public companies through financial statements, market data, valuation, comparison, macroeconomic context, structured search, and AI analysis.

<img src="docs/screenshots/company-ai.png" alt="Reasonframe company research view with Apple financial data, market history, and completed AI analyst overview" width="100%">

## Why Reasonframe

Financial software is good at retrieving data and performing calculations. General-purpose AI is good at interpretation, but it should not be responsible for inventing or recomputing financial facts.

Reasonframe separates those jobs: **the application provides the facts and calculations; AI analysts provide structured interpretation.** Analyst workflows operate on bounded research evidence assembled by the application, while the underlying data remains inspectable from the interface.

The result is a research workflow that feels closer to working through a company with several specialist perspectives than chatting with a generic finance bot.

## What you can do

- **Research companies** — inspect financial statements, historical metrics, market performance, and deterministic valuation.
- **Run specialist AI analysis** — independent Fundamentals and Valuation analysis, followed by Skeptic review and a synthesized Overview.
- **Compare companies** — analyze 2–4 companies across fundamentals, stock performance, and valuation, with cross-company AI synthesis.
- **Search financial data** — ask structured questions about metrics, histories, comparisons, rankings, changes, and CAGR.
- **Explore macro context** — view curated long-run FRED series with the underlying observations and transformation details.
- **Inspect evidence** — trace reported values, calculated outputs, and AI claims back to the data used by the application.

## Specialist AI analysis

Reasonframe's company workflow uses multiple analytical stages rather than a single general-purpose response:

```text
Fundamentals ─┐
              ├──> Skeptic ──> Overview
Valuation ────┘
```

Fundamentals and Valuation run independently. The Skeptic stage challenges the resulting analysis. Overview then synthesizes the result into the most important conclusions, risks, uncertainties, and a confidence assessment.

AI is not used to calculate values that the application can calculate directly. Analyst runs receive structured evidence packets and do not receive general web or tool access.

## Compare

Compare 2–4 companies without forcing every company into identical assumptions. Missing or partially comparable metrics remain visible as caveats instead of causing the whole analysis to fail.

<img src="docs/screenshots/compare.png" alt="Reasonframe comparing Apple and Microsoft revenue with AI-generated cross-company conclusions" width="100%">

## Search

Search is designed for financial questions rather than open-ended chat. It supports scalar queries, histories, pairwise comparisons, explicit-subset and universe rankings, highest/lowest queries, changes, CAGR, aliases, and relative dates.

Ranking and growth calculations are performed by the application before AI receives the result.

<img src="docs/screenshots/search-ranking.png" alt="Reasonframe ranking the top five companies by revenue growth with AI interpretation and inspectable evidence" width="100%">

## Macro

Reasonframe includes a curated set of FRED macroeconomic series for adding long-run economic context to company research.

<img src="docs/screenshots/macro.png" alt="Reasonframe macroeconomic research view showing long-run inflation data from FRED" width="100%">

## Local-first by design

Reasonframe is packaged as a desktop application with a Tauri 2 shell and a local FastAPI sidecar. Application state and research data are stored in OS-local app-data directories, with SQLite serving normal browsing and analysis requests.

Market history is downloaded using the user's own Tiingo API token and stored locally. Reasonframe does not redistribute a pre-populated Tiingo market-data database.

The local-first model is intentional: the application can own its financial calculations, cache research data locally, and send AI providers bounded analysis inputs rather than handing an unconstrained model the entire research process.

## Data sources

Reasonframe currently combines:

- **SEC filings** — company fundamentals normalized from SEC/XBRL data using the fair-access identity included with the v0.1.0 release.
- **Tiingo** — end-of-day market history, splits, and dividends using a user-supplied API token.
- **FRED** — a curated set of macroeconomic series with access included in the v0.1.0 release.

Tiingo users must supply their own token. See the [Tiingo API documentation](https://www.tiingo.com/documentation/general) and [API token page](https://api.tiingo.com/account/api/token).

The packaged v0.1.0 application includes its FRED access and SEC fair-access identity. Only release maintainers need to configure those values when producing a desktop build.

## Certified company universe

Reasonframe intentionally limits normal company browsing to data that has passed its certification checks. The current release exposes **50 supported companies**.

Certification is a quality boundary: incomplete or poorly normalized company data is not silently presented as if it were fully supported. Reasonframe is therefore intentionally narrower than a full-market terminal today.

## Getting started

### macOS

The strongest packaged support today is **macOS on Apple Silicon (ARM64)**.

1. Download the latest Reasonframe DMG from the repository's **Releases** page.
2. Install and open `Reasonframe.app`.
3. Add a Tiingo API token. Create a free Tiingo account if needed, then copy your token from the Tiingo API token page and paste it into Settings → Market Data. FRED and SEC access are included.
4. Connect a supported AI provider if you want to use analyst workflows.
5. Allow the initial local data setup to complete.

The data bootstrap is resumable, and subsequent refreshes can run automatically or manually from Settings.

### AI providers

**OpenAI Codex** is the primary integration. Reasonframe supports browser-based ChatGPT authentication through its bundled Codex runtime, so a globally installed `codex` command is not required. See OpenAI's [Codex sign-in documentation](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan).

**Claude Code** is also supported through Reasonframe's isolated, tool-disabled analyst path. Provider authentication and account requirements remain subject to Anthropic's own Claude Code setup and terms.

## Architecture

```text
┌─────────────────────────────┐
│     React / TypeScript      │
│        Tauri 2 shell        │
└──────────────┬──────────────┘
               │ localhost
               ▼
┌─────────────────────────────┐
│      FastAPI sidecar        │
├─────────────────────────────┤
│ SQLite                      │
│ SEC / XBRL normalization    │
│ Tiingo market data          │
│ Curated FRED macro data     │
│ Deterministic calculations  │
│ AI provider layer           │
└─────────────────────────────┘
```

The frontend is built with React, TypeScript, Vite, Tailwind/shadcn-style components, and Recharts. The backend is Python/FastAPI with SQLite, Pydantic, SEC/XBRL normalization, and curated FRED data.

Reasonframe is deliberately a local desktop project rather than a hosted multi-user platform.

## Current limitations

- Packaged release support is currently strongest on **macOS Apple Silicon (ARM64)**.
- Market data requires a user-supplied **Tiingo API token**.
- FRED access and the SEC fair-access identity are included with the **v0.1.0 packaged release**.
- Tiingo market data is downloaded locally and is **not redistributed** with Reasonframe.
- The normal product universe is limited to companies that pass Reasonframe's certification checks.
- Apple notarization may still be pending for early release artifacts.
- Reasonframe does **not** provide Buy/Hold/Sell recommendations, price targets, forecasts, fair values, DCFs, or forward valuation.

## Project status

Reasonframe is an independent project. The current pre-release is **v0.1.0-rc.1**: a release candidate focused on a polished macOS desktop experience, a deliberately bounded company universe, and transparent research workflows.

The project is not intended to replace institutional market-data platforms or professional investment judgment.

## Development

Python 3.12, Node.js/npm, Rust, Tauri's macOS prerequisites, and `uv` are required for the full desktop build.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
cd frontend && npm install
```

Copy `.env.example` to `.env` and provide development-only data-source credentials. Run the backend and frontend in separate terminals:

```bash
.venv/bin/uvicorn finance_terminal.api:app --host 127.0.0.1 --port 8000
cd frontend && npm run dev
```

Run the routine checks:

```bash
.venv/bin/python -m pytest -m 'not live'
cd frontend && npm test
cd frontend && npm run lint
cd frontend && npm run build
```

Build the macOS sidecar and desktop package:

```bash
scripts/build-macos-sidecar.sh
cd frontend && npm run desktop:build
```

Build outputs are generated under `frontend/src-tauri/target/release/bundle/` and should be attached to a GitHub Release rather than committed to source control.

## Data, trademarks, and disclaimer

Reasonframe is an independent project and is not affiliated with or endorsed by the U.S. Securities and Exchange Commission, the Federal Reserve Bank of St. Louis, Tiingo, OpenAI, or Anthropic. Product and company names belong to their respective owners.

Financial information can be incomplete, delayed, restated, or interpreted differently across sources. Reasonframe is a research tool, not an investment adviser, and its outputs are not investment advice.

<!-- Before public release: add the chosen LICENSE file and, if desired, a short License section here. -->
