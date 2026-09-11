"""Small user-local runtime configuration and secret-storage layer."""

from __future__ import annotations

import json
import os
import platform
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel, ConfigDict


APP_DIRECTORY_NAME = "Stas Finance Terminal"


def user_data_dir() -> Path:
    """Return the app-owned data directory, with an explicit test/dev override."""
    override = os.environ.get("FINANCE_TERMINAL_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIRECTORY_NAME
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_DIRECTORY_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "stas-finance-terminal"


class RuntimePaths(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    root: Path
    database: Path
    settings: Path
    secrets: Path
    bootstrap_state: Path
    logs: Path

    @classmethod
    def resolve(cls, root: str | Path | None = None) -> "RuntimePaths":
        base = Path(root).expanduser().resolve() if root is not None else user_data_dir()
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            base.chmod(0o700)
        except OSError:
            pass
        logs = base / "logs"
        logs.mkdir(exist_ok=True, mode=0o700)
        return cls(
            root=base,
            database=base / "terminal.db",
            settings=base / "settings.json",
            secrets=base / "secrets.json",
            bootstrap_state=base / "bootstrap-state.json",
            logs=logs,
        )


DEFAULT_SETTINGS: dict[str, Any] = {
    "automatic_refresh": True,
    "active_ai_provider": "chatgpt_codex",
    "selected_ai_models": {},
    "onboarding_completed": False,
}


class LocalJSONStore:
    """Atomic, process-local serialized JSON storage with restrictive permissions."""

    def __init__(self, path: str | Path, defaults: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.defaults = dict(defaults or {})
        self._lock = RLock()

    def read(self) -> dict[str, Any]:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                return {**self.defaults, **payload} if isinstance(payload, dict) else dict(self.defaults)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return dict(self.defaults)

    def write(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", dir=self.path.parent
            )
            temporary = Path(temporary_name)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, indent=2, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
                try:
                    self.path.chmod(0o600)
                except OSError:
                    pass
            finally:
                if temporary.exists():
                    temporary.unlink()

    def update(self, **changes: Any) -> dict[str, Any]:
        payload = self.read()
        payload.update(changes)
        self.write(payload)
        return payload


class RuntimeConfig:
    """Settings and secrets facade. Secret values never appear in status payloads."""

    def __init__(self, paths: RuntimePaths | None = None) -> None:
        self.paths = paths or RuntimePaths.resolve()
        self.settings = LocalJSONStore(self.paths.settings, DEFAULT_SETTINGS)
        self.secrets = LocalJSONStore(self.paths.secrets)

    def get_secret(self, name: str, *, environment_fallback: str | None = None) -> str | None:
        stored = self.secrets.read().get(name)
        if isinstance(stored, str) and stored.strip():
            return stored.strip()
        if environment_fallback:
            fallback = os.environ.get(environment_fallback, "").strip()
            return fallback or None
        return None

    def has_secret(self, name: str, *, environment_fallback: str | None = None) -> bool:
        return self.get_secret(name, environment_fallback=environment_fallback) is not None

    def fred_api_key(self) -> str | None:
        """Use the owner-only local secret or a development environment override."""
        return self.get_secret("fred_api_key", environment_fallback="FRED_API_KEY")

    def has_fred_api_key(self) -> bool:
        return self.fred_api_key() is not None

    def edgar_identity(self) -> str | None:
        """Use the owner-only local SEC fair-access identity or a development override."""
        return self.get_secret("edgar_identity", environment_fallback="EDGAR_IDENTITY")

    def has_edgar_identity(self) -> bool:
        return self.edgar_identity() is not None

    def set_secret(self, name: str, value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Secret value must not be empty")
        payload = self.secrets.read()
        payload[name] = cleaned
        self.secrets.write(payload)

    def remove_secret(self, name: str) -> None:
        payload = self.secrets.read()
        payload.pop(name, None)
        self.secrets.write(payload)
