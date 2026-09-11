"""Sanitized, cached executable discovery for desktop AI provider processes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def real_user_home() -> Path:
    """Keep provider authentication rooted in the user's home, never app data."""
    configured = os.environ.get("HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home().resolve()


def normalized_provider_path(inherited: str | None = None, home: Path | None = None) -> str:
    """Add bounded common GUI-missing locations without executing shell startup files."""
    user_home = home or real_user_home()
    candidates = [
        *(inherited or os.environ.get("PATH", "")).split(os.pathsep),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(user_home / ".local" / "bin"),
        str(user_home / ".npm-global" / "bin"),
        str(user_home / ".bun" / "bin"),
        str(user_home / ".volta" / "bin"),
        str(user_home / ".cargo" / "bin"),
        str(user_home / "Library" / "pnpm"),
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    unique: list[str] = []
    for candidate in candidates:
        cleaned = candidate.strip()
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    return os.pathsep.join(unique)


class ProviderExecutionEnvironment:
    """One provider-process environment with refreshable executable caching."""

    def __init__(self, *, inherited_path: str | None = None, home: Path | None = None) -> None:
        self.home = (home or real_user_home()).resolve()
        self.path = normalized_provider_path(inherited_path, self.home)
        self._executables: dict[str, Path | None] = {}

    def subprocess_env(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["HOME"] = str(self.home)
        environment["PATH"] = self.path
        environment.pop("CODEX_HOME", None)
        return environment

    def resolve(self, executable: str) -> Path | None:
        if executable not in self._executables:
            found = shutil.which(executable, path=self.path)
            self._executables[executable] = Path(found).resolve() if found else None
        return self._executables[executable]

    def refresh(self) -> None:
        self._executables.clear()
