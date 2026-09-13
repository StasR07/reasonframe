# Subscription AI provider feasibility

**Initial investigation:** 2026-09-12

**Re-evaluated:** 2026-09-13 after reviewing Alloy's bring-your-own-subscription release
**Application:** Reasonframe

## Revised decision

An **Alloy-equivalent bring-your-own-subscription integration is viable**, and Reasonframe now implements that design.

This is narrower than the original strict specification. It uses provider-owned programmatic runtimes rather than raw public model APIs:

- OpenAI: the official Codex SDK/app-server with browser-based ChatGPT authorization and a localhost callback.
- Anthropic: the official Claude Agent SDK with a user-generated Claude setup token.

Both SDKs ship provider runtimes that execute as child processes internally. Reasonframe no longer discovers or launches a user-installed `codex` or `claude` executable, but it cannot truthfully claim that no provider binary exists beneath the SDK boundary.

## Executive verdict

| Provider | Alloy-equivalent goal | Original literal goal | Verdict |
| --- | --- | --- | --- |
| OpenAI / ChatGPT | Browser login with a local callback, subscription entitlement, bundled Codex SDK runtime, bounded schema output | Fails the requirements for raw direct HTTP and for no provider runtime subprocess | **WORKS** for supported app-server embedding; **PARTIALLY WORKS** for the original goal |
| Anthropic / Claude | `claude setup-token`, OS-protected token storage, Claude Agent SDK, custom prompt, no tools, schema output | Requires the user to run Claude Code once and the SDK executes its bundled runtime | **WORKS** for Alloy parity; **PARTIALLY WORKS** for the original goal |

Alloy's public documentation is direct evidence for this distinction:

- Its [ChatGPT subscription guide](https://alloy.app/guide/integrations/chatgpt-subscription) demonstrates ChatGPT subscription authorization through a Codex-backed session runner. Reasonframe uses app-server's normal browser callback instead of Alloy's device-code variant to avoid requiring the user to enable a beta security setting.
- Its [Claude subscription guide](https://alloy.app/guide/integrations/claude-subscription) requires `claude setup-token` and routes requests through the Claude Agent SDK.

Alloy does not claim to call the public Responses or Messages APIs directly using consumer-subscription OAuth. Its design is an authenticated agent-runtime integration.

## Requirements matrix

| Requirement | OpenAI implementation | Claude implementation |
| --- | --- | --- |
| No user-created API key | Met | Met |
| Uses paid subscription | Met through ChatGPT-authenticated Codex | Met through the Claude setup token and Agent SDK |
| Embedded connection UX | Met with an authorization URL, automatic localhost callback, and status polling | Partially met: the setup token is generated outside Reasonframe and pasted into the app |
| No dependency on PATH-installed provider CLI | Met | Met during inference; Claude Code is still required once to create the setup token |
| No provider runtime subprocess | Not met; the Codex SDK runs app-server | Not met; the Agent SDK runs its bundled Claude runtime |
| Raw direct public model HTTP | Not met | Not met |
| Application-owned system prompt | Reasonframe replaces Codex base instructions for each isolated thread, but Codex remains the transport | Official Agent SDK documentation says a custom prompt string sends only the supplied prompt |
| No tools, files, web, MCP, plugins, or user settings | Disabled by SDK configuration and an empty isolated working directory | Disabled with `tools=[]`, empty setting sources, strict empty MCP configuration, and no plugins or skills |
| Strict structured output | Codex output schema | Agent SDK JSON Schema output |
| Bounded evidence contract | Preserved | Preserved |

## OpenAI / ChatGPT

### Supported integration surface

Official OpenAI documentation describes Codex app-server as the interface for embedding Codex in a product. It exposes authentication, account state, models, threads, approvals, and event streaming. Its normal `chatgpt` authentication mode returns an authorization URL and hosts a random-port localhost callback. OpenAI also supports a beta `chatgptDeviceCode` fallback, but that flow requires the user or workspace administrator to enable device-code authorization in ChatGPT settings. Reasonframe therefore uses normal browser login.

Codex owns token exchange, refresh, persistence, subscription/workspace selection, and request authentication. This is preferable to copying Codex's OAuth client ID and private token endpoints into Reasonframe.

Sources:

- [OpenAI authentication](https://learn.chatgpt.com/docs/auth)
- [Codex app-server](https://learn.chatgpt.com/docs/app-server)
- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)

### Implemented path

1. Reasonframe starts `login_chatgpt()` through the official Python SDK.
2. The backend returns the OpenAI authorization URL to the local WebView.
3. The frontend opens that URL in the user's default browser.
4. After sign-in, the browser redirects to app-server's random localhost callback; app-server exchanges the grant and emits its completion notification.
5. The SDK stores refreshed credentials under Reasonframe's app-scoped Codex home.
6. Status polling changes the provider to `CONNECTED`.
7. Each generation creates an ephemeral, read-only thread in a fresh empty directory.
8. Reasonframe's task-specific instruction is supplied as `base_instructions`, tools and web access are disabled, and the normalized output schema is required.

The Reasonframe WebView receives only the authorization URL. The external browser necessarily carries the short-lived OAuth callback grant to app-server's localhost listener, but application JavaScript never receives it. Access tokens, refresh tokens, cookies, and credential files remain outside the WebView.

### Classification

| Component | Classification |
| --- | --- |
| Product embedding through app-server | Officially documented and supported |
| Browser `chatgpt` login with localhost callback | Officially documented and supported by app-server |
| `chatgptDeviceCode` login | Officially documented beta fallback; requires a user or workspace security setting and is not Reasonframe's default |
| ChatGPT subscription entitlement in Codex | Officially documented |
| Direct public Responses API with the resulting token | Not documented |
| ChatGPT internal Codex endpoint | Internal implementation detail; Reasonframe does not call it directly |
| Codex SDK's bundled runtime | Supported SDK implementation, but still a subprocess |

## Anthropic / Claude

### Supported integration surface

Anthropic documents `claude setup-token` as a way to create a long-lived, inference-only OAuth token for a Claude subscription. The token is supplied to the Agent SDK as `CLAUDE_CODE_OAUTH_TOKEN`.

Claude Code itself also supports a smooth browser login through `claude auth login --claudeai`. That command authenticates the interactive Claude Code client, however; Anthropic does not expose a corresponding browser-login handle or callback API in the Agent SDK for a third-party desktop application to embed. Invoking the bundled command directly and depending on its credential cache would put Reasonframe back across the CLI boundary this implementation deliberately removed, and would retain the broader interactive-client credential instead of the inference-only setup token.

Anthropic's separate `ant auth login` browser flow is not a substitute: it connects a Claude Console workspace for metered API billing rather than consuming a Claude.ai subscription. For that reason, Reasonframe keeps the documented setup-token flow instead of presenting either browser login as equivalent to ChatGPT subscription login.

Anthropic's current June 2026 subscription guidance explicitly states that Claude Agent SDK, `claude -p`, and third-party application usage still draw from subscription limits while its previously announced separate-credit change is paused. That specific Agent SDK guidance is the basis for this implementation.

Sources:

- [Claude Agent SDK subscription use](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [Claude Code authentication](https://code.claude.com/docs/en/authentication)
- [Python Agent SDK reference](https://code.claude.com/docs/en/agent-sdk/python)
- [Agent SDK system prompts](https://code.claude.com/docs/en/agent-sdk/modifying-system-prompts)
- [Agent SDK structured output](https://code.claude.com/docs/en/agent-sdk/structured-outputs)

Anthropic also maintains broader guidance preferring API credentials for general third-party products and prohibiting clients that misrepresent their identity. Reasonframe therefore identifies itself through the official Agent SDK path and does not reproduce Messages API headers or impersonate Claude Code with custom HTTP.

### Implemented path

1. The user signs in to Claude Code and runs `claude setup-token` once.
2. The user pastes the resulting `sk-ant-oat…` value into Reasonframe's masked field.
3. The local FastAPI sidecar receives it and stores it in the operating-system credential store; it is never written to settings JSON or returned in a response.
4. Generation uses the official `claude-agent-sdk` package and its bundled runtime.
5. Reasonframe passes the token only in the child runtime environment and blanks inherited Anthropic API-key variables to prevent accidental separately billed routing.
6. Each call uses an isolated app directory, a custom Reasonframe system prompt, `tools=[]`, no allowed tools, no settings sources, no skills/plugins, strict empty MCP, no session persistence, and a JSON Schema output format.

### Classification

| Component | Classification |
| --- | --- |
| Setup-token generation | Officially documented Claude Code flow |
| Embedded Claude.ai browser login | Available to the interactive Claude Code CLI, but no Agent SDK embedding API is documented |
| `ant auth login` | Browser login for Claude Console/API billing, not Claude.ai subscription entitlement |
| Third-party Agent SDK use against subscription limits | Explicitly acknowledged by current Anthropic guidance |
| Custom system prompt string | Officially documented; SDK says only the supplied prompt is sent |
| Empty toolset and settings sources | Officially documented SDK configuration |
| Structured JSON output | Officially documented SDK feature |
| Agent SDK bundled runtime | Supported SDK implementation, but still a subprocess |
| Raw Messages API with the setup token | Not used; unnecessary and less defensible than the SDK path |

## Security boundary

- OpenAI OAuth credentials stay inside Codex's app-server-managed storage.
- Claude's setup token is stored in the OS credential store through `keyring`.
- Credential values are represented as `SecretStr` at the API boundary and are never included in response models or logs.
- The Claude token is scoped to an app-specific credential-store account derived from the local data directory.
- Reasonframe clears inherited Anthropic API-key variables for Claude subscription calls.
- Provider configuration, credentials, tools, files, web access, MCP servers, skills, plugins, and session history are excluded from model turns to the extent supported by each SDK.

The OpenAI authorization URL and Claude setup-token field necessarily exist briefly in the local Tauri WebView. The OpenAI OAuth grant is delivered by the external browser directly to app-server's localhost callback, not to Reasonframe JavaScript. Claude still requires a pasted setup token; that value is sent only to the loopback FastAPI sidecar and cleared from component state after a successful connection.

## Residual risks and limitations

1. Provider subscription limits and supported models can change independently of Reasonframe.
2. Both SDK packages contain large platform runtimes, increasing desktop bundle size.
3. A provider runtime is still executed below the SDK API even though Reasonframe no longer shells out to a PATH-installed command.
4. OpenAI's Codex runtime remains an agent transport; `base_instructions` and lockdown configuration substantially improve isolation but cannot turn it into the public raw Responses API.
5. Anthropic structured output may add provider-controlled format instructions or retry invalid output. That behavior is part of the supported schema feature.
6. Live connect/generate/disconnect verification requires the user's subscriptions and is intentionally excluded from routine automated tests.
7. Browser login depends on the localhost callback being reachable; device-code login remains an official fallback for remote or unusually restricted environments, but requires the ChatGPT security toggle shown in the user's screenshot.
8. Claude cannot currently match the embedded ChatGPT callback UX using a documented third-party SDK surface; its one-time setup-token step remains a product limitation.

## Final verdict

The original zero-provider-binary/direct-public-HTTP specification remains unavailable with consumer subscriptions. The revised Alloy-equivalent goal is viable and has been implemented using documented provider SDK surfaces without API keys or PATH-installed provider commands.
