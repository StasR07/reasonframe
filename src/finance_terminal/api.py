"""Minimal FastAPI boundary over the deterministic query engine."""

from __future__ import annotations

import os
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .analysis import (
    AnalysisResponse, AnalysisService, CompanyAnalysisRequest,
    ComparisonAnalysisRequest, FocusedAnalysisRequest,
)
from .ai import AskRequest, AskResponse, AskService
from .ai_provider import (
    AIDisconnectedError,
    AIAccountStatus,
    AIConnectResult,
    AIModel,
    AIProvider,
    AIProviderError,
    AIProviderState,
    AIProviderStatus,
    LazyCodexProvider,
    ProviderManager,
)
from .market import MarketConnectionState
from .market import MarketProviderError
from .query import EvidenceResult, QueryEngine, QueryRequest, QueryResponse
from .data_sync import DataSyncManager
from .runtime import RuntimeConfig
from .storage import SQLiteStore
from .tiingo import TiingoProvider


load_dotenv()
logger = logging.getLogger(__name__)


class SecretInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=4096)


class RuntimeSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automatic_refresh: bool | None = None
    onboarding_completed: bool | None = None


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automatic: bool = False


class AIProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str


class AIConnectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential: SecretStr | None = None


def create_app(
    store: SQLiteStore | None = None,
    ask_service: AskService | None = None,
    provider: AIProvider | None = None,
    runtime_config: RuntimeConfig | None = None,
    sync_manager: DataSyncManager | None = None,
) -> FastAPI:
    owns_database = store is None
    config = runtime_config or RuntimeConfig()
    database_override = os.environ.get("DATABASE_PATH", "").strip()
    app_data_override = os.environ.get("FINANCE_TERMINAL_DATA_DIR", "").strip()
    database_path = config.paths.database if app_data_override else (Path(database_override) if database_override else config.paths.database)
    database = store or SQLiteStore(database_path)
    database.seed_certified_registry()
    engine = QueryEngine(database)
    ai_provider = provider or (
        ask_service.provider if ask_service else
        LazyCodexProvider(config.paths.root) if store is not None else
        ProviderManager(config.paths.root, config.settings)
    )
    natural_language = ask_service or AskService(database, ai_provider)
    analysis = AnalysisService(database, ai_provider)
    sync = sync_manager or DataSyncManager(database, config)
    background_sync_task: asyncio.Task[object] | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await ai_provider.close()
            if owns_database:
                database.close()

    app = FastAPI(title="Reasonframe", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["tauri://localhost", "http://tauri.localhost"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )
    app.state.store = database
    app.state.ai_provider = ai_provider
    app.state.ask_service = natural_language
    app.state.analysis_service = analysis
    app.state.runtime_config = config
    app.state.sync_manager = sync

    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/catalog")
    def catalog() -> dict[str, object]:
        payload = database.catalog()
        payload["features"] = {"ai_search": True}
        provider_connected = config.has_secret("tiingo_token", environment_fallback="TIINGO_API_TOKEN")
        payload["market_provider"] = "TIINGO"
        payload["provider_connected"] = provider_connected
        payload["market_connection_state"] = (
            MarketConnectionState.CONNECTED if provider_connected else MarketConnectionState.NOT_CONNECTED
        ).value
        payload["market_attribution"] = "EOD market data sourced from Tiingo."
        return payload

    @app.get("/api/v1/runtime")
    def runtime_status() -> dict[str, object]:
        settings = config.settings.read()
        return {
            "onboarding_completed": bool(settings.get("onboarding_completed", False)),
            "automatic_refresh": bool(settings.get("automatic_refresh", True)),
            "data": sync.public_status(),
        }

    @app.patch("/api/v1/settings")
    def update_settings(request: RuntimeSettingsInput) -> dict[str, object]:
        changes = request.model_dump(exclude_none=True)
        settings = config.settings.read()
        settings.update(changes)
        config.settings.write(settings)
        return {
            "onboarding_completed": bool(settings.get("onboarding_completed", False)),
            "automatic_refresh": bool(settings.get("automatic_refresh", True)),
        }

    @app.post("/api/v1/settings/tiingo")
    def save_tiingo(request: SecretInput) -> dict[str, bool]:
        token = request.value.strip()
        try:
            instrument = database.primary_market_instrument("AAPL")
            source = database.provider_market_instrument(instrument.instrument_id, "TIINGO") if instrument else None
            if source is None:
                raise RuntimeError("Local market catalog is unavailable")
            TiingoProvider(token).fetch_eod(source, date.today() - timedelta(days=7), date.today())
        except MarketProviderError as exc:
            if exc.code.value == "authentication_error":
                raise HTTPException(400, "Tiingo could not authenticate this token. Replace the token and try again.") from exc
            if exc.code.value == "rate_limited":
                raise HTTPException(429, "Tiingo's current request window is exhausted. Try again later.") from exc
            raise HTTPException(503, "Tiingo could not be reached. Check your connection and try again.") from exc
        except Exception as exc:
            raise HTTPException(503, "Tiingo validation could not be completed.") from exc
        config.set_secret("tiingo_token", token)
        return {"configured": True}

    @app.delete("/api/v1/settings/tiingo")
    def remove_tiingo() -> dict[str, bool]:
        config.remove_secret("tiingo_token")
        return {"configured": False}

    @app.post("/api/v1/settings/fred")
    def save_fred(request: SecretInput) -> dict[str, bool]:
        config.set_secret("fred_api_key", request.value)
        return {"configured": True}

    @app.delete("/api/v1/settings/fred")
    def remove_fred() -> dict[str, bool]:
        config.remove_secret("fred_api_key")
        return {"configured": False}

    @app.post("/api/v1/settings/edgar")
    def save_edgar_identity(request: SecretInput) -> dict[str, bool]:
        identity = request.value.strip()
        if "@" not in identity or len(identity.split()) < 2:
            raise HTTPException(400, "Enter a name and email address for SEC fair-access requests.")
        config.set_secret("edgar_identity", identity)
        return {"configured": True}

    @app.delete("/api/v1/settings/edgar")
    def remove_edgar_identity() -> dict[str, bool]:
        config.remove_secret("edgar_identity")
        return {"configured": False}

    def start_sync(function, operation: str) -> dict[str, object]:
        nonlocal background_sync_task
        if background_sync_task is None or background_sync_task.done():
            if sync.reserve(operation):
                background_sync_task = asyncio.create_task(asyncio.to_thread(function))
        return sync.public_status()

    @app.get("/api/v1/data/status")
    def data_status() -> dict[str, object]:
        return sync.public_status()

    @app.post("/api/v1/data/bootstrap")
    async def start_bootstrap() -> dict[str, object]:
        return start_sync(lambda: sync.run_bootstrap(_lock_reserved=True), "BOOTSTRAP")

    @app.post("/api/v1/data/refresh")
    async def start_refresh(request: SyncRequest) -> dict[str, object]:
        return start_sync(
            lambda: sync.run_refresh(automatic=request.automatic, _lock_reserved=True), "REFRESH"
        )

    @app.post("/api/v1/query", response_model=QueryResponse)
    def query(request: QueryRequest, http_request: Request) -> QueryResponse:
        try:
            return engine.execute(request)
        except Exception as exc:
            logger.exception(
                "deterministic query failed request_id=%s domain=%s tickers=%s metric=%s frequency=%s range=%s exception=%s",
                http_request.state.request_id, request.domain, getattr(request, "tickers", None),
                getattr(request, "metric", getattr(request, "series", None)),
                getattr(request, "frequency", None),
                (getattr(request, "start_year", getattr(request, "start_date", None)),
                 getattr(request, "end_year", getattr(request, "end_date", None))),
                type(exc).__name__,
            )
            raise HTTPException(500, "Could not load data") from exc

    @app.get("/api/v1/evidence/{evidence_id}", response_model=EvidenceResult)
    def evidence(evidence_id: str) -> EvidenceResult:
        resolved = engine.resolve_evidence(evidence_id)
        if resolved is None:
            raise HTTPException(404, "Evidence reference was not found in local storage")
        return resolved

    async def public_ai_status() -> AIProviderStatus:
        try:
            current = await ai_provider.status()
        except AIProviderError as exc:
            raise HTTPException(503, "AI provider is currently unavailable") from exc
        if isinstance(current, AIProviderStatus):
            return current
        return AIProviderStatus(
            **current.model_dump(),
            state=(
                AIProviderState.CONNECTED
                if current.connected else AIProviderState.SIGN_IN_REQUIRED
            ),
            message="Connected with ChatGPT" if current.connected else "Sign in with ChatGPT to use AI analysis.",
        )

    @app.get("/api/v1/ai/status", response_model=AIProviderStatus)
    async def ai_status() -> AIProviderStatus:
        return await public_ai_status()

    @app.get("/api/v1/ai/providers", response_model=list[AIProviderStatus])
    async def ai_providers() -> list[AIProviderStatus]:
        if isinstance(ai_provider, ProviderManager):
            return await ai_provider.provider_statuses()
        return [await public_ai_status()]

    @app.post("/api/v1/ai/providers/refresh", response_model=list[AIProviderStatus])
    async def refresh_ai_providers() -> list[AIProviderStatus]:
        if isinstance(ai_provider, ProviderManager):
            return await ai_provider.refresh_provider_statuses()
        return [await public_ai_status()]

    @app.put("/api/v1/ai/provider", response_model=AIProviderStatus)
    async def select_ai_provider(request: AIProviderSelection) -> AIProviderStatus:
        if not isinstance(ai_provider, ProviderManager):
            raise HTTPException(409, "AI provider selection is unavailable in this runtime")
        try:
            ai_provider.select(request.provider)
            return await public_ai_status()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/v1/ai/connect", response_model=AIConnectResult)
    async def ai_connect(request: AIConnectInput | None = None) -> AIConnectResult:
        try:
            credential = request.credential.get_secret_value() if request and request.credential else None
            return await ai_provider.connect(credential)
        except AIDisconnectedError as exc:
            raise HTTPException(400, str(exc)) from exc
        except AIProviderError as exc:
            raise HTTPException(503, "AI provider connection could not be started") from exc

    @app.post("/api/v1/ai/disconnect", response_model=AIAccountStatus)
    async def ai_disconnect() -> AIAccountStatus:
        try:
            return await ai_provider.disconnect()
        except AIProviderError as exc:
            raise HTTPException(503, "AI provider could not be disconnected") from exc

    @app.get("/api/v1/ai/models", response_model=list[AIModel])
    async def ai_models() -> list[AIModel]:
        try:
            return await ai_provider.models()
        except AIProviderError as exc:
            raise HTTPException(409, "Connect the selected AI provider before listing models") from exc

    @app.post("/api/v1/ai/verify")
    async def verify_ai_provider() -> dict[str, bool]:
        """Run one fixed, minimal structured call through the active provider manager."""
        schema: dict[str, object] = {
            "type": "object",
            "properties": {"ok": {"type": "boolean", "const": True}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        try:
            raw = await ai_provider.generate_structured(
                'TASK: provider-connection-check\nReturn {"ok": true}.', schema,
            )
            parsed = __import__("json").loads(raw)
        except (AIProviderError, ValueError, TypeError) as exc:
            raise HTTPException(503, "AI provider verification failed") from exc
        if parsed != {"ok": True}:
            raise HTTPException(503, "AI provider verification returned an invalid result")
        return {"ok": True}

    @app.post("/api/v1/ask", response_model=AskResponse)
    async def ask(request: AskRequest) -> AskResponse:
        return await natural_language.ask(request.question, model=request.model)

    @app.post("/api/v1/analyze/result", response_model=AnalysisResponse)
    async def analyze_result(request: FocusedAnalysisRequest) -> AnalysisResponse:
        return await analysis.focused(request)

    @app.post("/api/v1/analyze/company", response_model=AnalysisResponse)
    async def analyze_company(request: CompanyAnalysisRequest) -> AnalysisResponse:
        return await analysis.company(request)

    @app.post("/api/v1/analyze/company/stream")
    async def analyze_company_stream(request: CompanyAnalysisRequest) -> StreamingResponse:
        async def stream():
            async for item in analysis.company_events(request):
                yield f"event: {item.event}\ndata: {item.model_dump_json()}\n\n"
        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/analyze/comparison", response_model=AnalysisResponse)
    async def analyze_comparison(request: ComparisonAnalysisRequest) -> AnalysisResponse:
        return await analysis.comparison(request)

    return app


app = create_app()
