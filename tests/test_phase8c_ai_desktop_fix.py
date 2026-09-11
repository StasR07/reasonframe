from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from finance_terminal.ai_provider import (
    AIProviderState, AIProviderStatus, ClaudeCodeProvider, CodexSubscriptionProvider,
    LazyCodexProvider, ProviderManager,
)
from finance_terminal.api import create_app
from finance_terminal.provider_runtime import ProviderExecutionEnvironment
from finance_terminal.runtime import RuntimeConfig, RuntimePaths
from finance_terminal.storage import SQLiteStore


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_provider_discovery_uses_inherited_and_common_gui_paths_and_preserves_home(tmp_path, monkeypatch) -> None:
    home = tmp_path / "user-home"
    inherited = executable(tmp_path / "custom-bin" / "claude")
    common = executable(home / ".local" / "bin" / "common-provider")
    monkeypatch.setenv("CODEX_HOME", "/must/not/leak")

    execution = ProviderExecutionEnvironment(inherited_path=str(inherited.parent), home=home)
    assert execution.resolve("claude") == inherited.resolve()
    assert execution.resolve("common-provider") == common.resolve()
    assert execution.resolve("missing-provider") is None
    assert execution.subprocess_env()["HOME"] == str(home.resolve())
    assert "CODEX_HOME" not in execution.subprocess_env()


def test_claude_invocation_uses_absolute_discovered_executable(tmp_path, monkeypatch) -> None:
    binary = executable(tmp_path / "bin" / "claude")
    execution = ProviderExecutionEnvironment(inherited_path=str(binary.parent), home=tmp_path / "home")
    provider = ClaudeCodeProvider(tmp_path / "data", execution=execution)
    captured: dict[str, object] = {}

    class Process:
        returncode = 0

        async def communicate(self):
            return b'{"loggedIn":true}', b""

    async def create(*arguments, **kwargs):
        captured["arguments"] = arguments
        captured["environment"] = kwargs["env"]
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    code, _ = asyncio.run(provider._run("auth", "status"))
    assert code == 0
    assert captured["arguments"][0] == str(binary.resolve())  # type: ignore[index]
    assert captured["environment"]["HOME"] == str((tmp_path / "home").resolve())  # type: ignore[index]


@pytest.mark.parametrize(
    ("result", "state"),
    [
        ((0, '{"loggedIn":true}'), AIProviderState.CONNECTED),
        ((1, '{"loggedIn":false}'), AIProviderState.SIGN_IN_REQUIRED),
        ((1, ""), AIProviderState.RUNTIME_ERROR),
    ],
)
def test_claude_status_distinguishes_auth_states(tmp_path, result, state) -> None:
    binary = executable(tmp_path / "bin" / "claude")
    provider = ClaudeCodeProvider(
        tmp_path / "data",
        execution=ProviderExecutionEnvironment(inherited_path=str(binary.parent), home=tmp_path / "home"),
    )

    async def fake_run(*_arguments: str, timeout: float):
        del timeout
        return result

    provider._run = fake_run  # type: ignore[method-assign]
    status = asyncio.run(provider.status())
    assert status.state is state
    assert status.connected is (state is AIProviderState.CONNECTED)


def test_missing_provider_state_is_explicit(tmp_path) -> None:
    provider = ClaudeCodeProvider(
        tmp_path / "data",
        execution=ProviderExecutionEnvironment(inherited_path=str(tmp_path / "empty"), home=tmp_path / "home"),
    )
    assert asyncio.run(provider.status()).state is AIProviderState.NOT_INSTALLED


def test_removed_provider_state_falls_back_safely_without_being_exposed(tmp_path) -> None:
    paths = RuntimePaths.resolve(tmp_path / "app-data")
    config = RuntimeConfig(paths)
    config.settings.update(active_ai_provider="gemini_cli")
    manager = ProviderManager(paths.root, config.settings)

    assert manager.active_id == "chatgpt_codex"
    assert set(manager.providers) == {"chatgpt_codex", "claude_code"}
    with pytest.raises(ValueError, match="Unknown AI provider"):
        manager.select("gemini_cli")


def test_codex_bundled_runtime_failure_is_not_reported_as_not_installed(tmp_path, monkeypatch) -> None:
    import finance_terminal.ai_provider as provider_module
    import openai_codex

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("missing runtime")

    monkeypatch.setattr(provider_module, "CodexSubscriptionProvider", missing)
    lazy = LazyCodexProvider(tmp_path / "missing")
    missing_status = asyncio.run(lazy.status())
    assert missing_status.state is AIProviderState.RUNTIME_ERROR
    assert missing_status.installed is True

    class BrokenCodex:
        def __init__(self, _config):
            pass

        async def account(self, **_kwargs):
            raise RuntimeError("private subprocess detail")

        async def close(self):
            pass

    monkeypatch.setattr(openai_codex, "AsyncCodex", BrokenCodex)
    status = asyncio.run(CodexSubscriptionProvider(tmp_path / "broken").status())
    assert status.state is AIProviderState.RUNTIME_ERROR
    assert "private subprocess detail" not in (status.message or "")


def test_codex_clean_path_login_transition_and_structured_call_share_sdk_runtime(tmp_path, monkeypatch) -> None:
    """Every OpenAI path must work without resolving a bare codex executable."""
    import finance_terminal.provider_runtime as runtime_module
    import openai_codex

    created: list[object] = []

    class LoginHandle:
        auth_url = "https://auth.openai.com/codex/callback-test"

        def __init__(self, runtime):
            self.runtime = runtime

        async def wait(self):
            self.runtime.connected = True

        async def cancel(self):
            return None

    class Thread:
        async def run(self, _prompt, **_kwargs):
            return SimpleNamespace(final_response='{"ok":true}')

    class SDKRuntime:
        def __init__(self, config):
            self.config = config
            self.connected = False
            self.closed = False
            created.append(self)

        async def account(self, **_kwargs):
            account = SimpleNamespace(type="chatgpt", plan_type="plus") if self.connected else None
            return SimpleNamespace(account=SimpleNamespace(root=account) if account else None)

        async def login_chatgpt(self):
            return LoginHandle(self)

        async def logout(self):
            self.connected = False

        async def models(self, **_kwargs):
            return SimpleNamespace(data=[SimpleNamespace(
                model="terra", display_name="GPT-5.6 Terra", description="", is_default=True, hidden=False,
            )])

        async def thread_start(self, **_kwargs):
            return Thread()

        async def close(self):
            self.closed = True

    original_which = runtime_module.shutil.which

    def reject_bare_codex(name, *args, **kwargs):
        if name == "codex":
            raise AssertionError("OpenAI attempted to resolve bare codex from PATH")
        return original_which(name, *args, **kwargs)

    monkeypatch.setattr(runtime_module.shutil, "which", reject_bare_codex)
    monkeypatch.setattr(openai_codex, "AsyncCodex", SDKRuntime)

    async def scenario():
        provider = CodexSubscriptionProvider(
            tmp_path / "app-data",
            execution=ProviderExecutionEnvironment(inherited_path="", home=tmp_path / "home"),
        )
        assert (await provider.status()).state is AIProviderState.SIGN_IN_REQUIRED
        login = await provider.connect()
        assert login.auth_url == LoginHandle.auth_url
        await asyncio.sleep(0)
        assert (await provider.status()).state is AIProviderState.CONNECTED
        assert await provider.generate_structured("TASK: test", {"type": "object"}) == '{"ok":true}'
        await provider.disconnect()
        assert (await provider.status()).state is AIProviderState.SIGN_IN_REQUIRED
        await provider.close()

    asyncio.run(scenario())
    assert len(created) == 1


def test_codex_authenticated_start_uses_account_only_and_same_sdk_runtime(tmp_path, monkeypatch) -> None:
    import openai_codex

    calls = {"account": 0, "models": 0}

    class SDKRuntime:
        def __init__(self, _config):
            pass

        async def account(self, **_kwargs):
            calls["account"] += 1
            return SimpleNamespace(account=SimpleNamespace(root=SimpleNamespace(type="chatgpt", plan_type="plus")))

        async def models(self, **_kwargs):
            calls["models"] += 1
            raise RuntimeError("model catalog must not control auth status")

        async def close(self):
            pass

    monkeypatch.setattr(openai_codex, "AsyncCodex", SDKRuntime)
    provider = CodexSubscriptionProvider(tmp_path / "app-data")
    status = asyncio.run(provider.status())
    assert status.state is AIProviderState.CONNECTED
    assert status.message == "Connected with ChatGPT"
    assert calls == {"account": 1, "models": 0}


def test_retry_detection_rechecks_provider_status_and_api_is_sanitized(tmp_path, monkeypatch) -> None:
    paths = RuntimePaths.resolve(tmp_path / "app-data")
    store = SQLiteStore(paths.database)
    config = RuntimeConfig(paths)
    manager = ProviderManager(paths.root, config.settings)
    calls = 0

    async def statuses():
        nonlocal calls
        calls += 1
        return [AIProviderStatus(
            connected=True, state=AIProviderState.CONNECTED,
            message="Connected with ChatGPT",
        )]

    async def refresh():
        manager.execution.refresh()
        return await statuses()

    monkeypatch.setattr(manager, "provider_statuses", statuses)
    monkeypatch.setattr(manager, "refresh_provider_statuses", refresh)
    try:
        with TestClient(create_app(store, provider=manager, runtime_config=config)) as client:
            response = client.post("/api/v1/ai/providers/refresh")
            assert response.status_code == 200
            payload = response.json()
            assert calls == 1
            assert payload[0]["state"] == "CONNECTED"
            serialized = json.dumps(payload).lower()
            assert "token" not in serialized and "auth.json" not in serialized
            assert "executable" not in serialized and str(tmp_path).lower() not in serialized
    finally:
        store.close()


def test_claude_timeout_kills_subprocess_and_is_safely_classified(tmp_path, monkeypatch) -> None:
    binary = executable(tmp_path / "bin" / "claude")
    provider = ClaudeCodeProvider(
        tmp_path / "data", timeout_seconds=0.001,
        execution=ProviderExecutionEnvironment(inherited_path=str(binary.parent), home=tmp_path / "home"),
    )

    class SlowProcess:
        returncode = None
        killed = False

        async def communicate(self):
            await asyncio.sleep(1)
            return b"", b""

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9

    process = SlowProcess()

    async def create(*_arguments, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    with pytest.raises(Exception, match="timed out"):
        asyncio.run(provider.generate_structured("TASK: parser", {"type": "object"}))
    assert process.killed
