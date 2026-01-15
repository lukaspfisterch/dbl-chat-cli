# Inventory

## Tree

- .gitignore
- pyproject.toml
- README.md
- src/dbl_chat_cli/__init__.py
- src/dbl_chat_cli/__main__.py
- src/dbl_chat_cli/client.py
- src/dbl_chat_cli/gateway_api.py
- src/dbl_chat_cli/repl.py

### .gitignore

```
.venv/
__pycache__/
*.pyc
*.egg-info/
dist/
build/
```

### pyproject.toml

```
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "dbl-chat-cli"
version = "0.1.0"
description = "CLI chat client for dbl-gateway"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
  "httpx>=0.27.0",
  "prompt_toolkit>=3.0.43",
]

[project.scripts]
dbl-chat-cli = "dbl_chat_cli.__main__:main"

[tool.setuptools]
package-dir = { "" = "src" }

[tool.setuptools.packages.find]
where = ["src"]
include = ["dbl_chat_cli*"]
```

### README.md

```
# dbl-chat-cli

Thin, deterministic CLI client for `dbl-gateway`.

## What it does

- Connects to `dbl-gateway` over HTTP only.
- Calls `GET /capabilities` on startup.
- Uses `/tail` if advertised, otherwise polls `/snapshot` with backoff.
- Sends `POST /ingress/intent` with `intent_type=chat.message`.
- No policy logic, no provider SDKs, no persistence.

## Usage

```bash
python -m dbl_chat_cli --base-url http://127.0.0.1:8010
```

Optional flags:
- `--model-id` (default: first model from capabilities)
- `--provider` (optional override)
- `--max-output-tokens`
- `--principal-id` (required)
- `--workspace-id` (optional)
- `--lane` (default: user)

## Controls

- Multiline input
- `Enter` to send
- `Ctrl+C` clears the current input; during wait it cancels the current request
- `Ctrl+D` exits

## Limitations by design

- No governance, policy, or provider logic.
- No memory or tools.
- No retries without backoff.
```

### src/dbl_chat_cli/__init__.py

```
__all__ = []
```

### src/dbl_chat_cli/__main__.py

```
from __future__ import annotations

import argparse
import sys

from .client import ChatClient, ClientConfig
from .gateway_api import GatewayAPI
from .repl import repl_loop


def _default_model(caps) -> tuple[str | None, str | None]:
    providers = caps.providers
    if not providers:
        return None, None
    provider = providers[0]
    models = provider.get("models", [])
    if not models:
        return None, None
    return models[0].get("id"), provider.get("id")


def main() -> int:
    parser = argparse.ArgumentParser(prog="dbl-chat-cli")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--model-id")
    parser.add_argument("--provider")
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--principal-id", required=True)
    parser.add_argument("--workspace-id")
    parser.add_argument("--lane", default="user")
    args = parser.parse_args()

    api = GatewayAPI(args.base_url)
    try:
        caps = api.get_capabilities()
    except Exception as exc:
        print(f"Failed to load capabilities: {exc}")
        api.close()
        return 1

    model_id = args.model_id
    provider = args.provider
    if model_id is None:
        model_id, provider_default = _default_model(caps)
        if provider is None:
            provider = provider_default
    if model_id is None:
        print("No model available in capabilities; use --model-id.")
        api.close()
        return 1

    stream = bool(caps.surfaces.get("tail", False))
    config = ClientConfig(
        base_url=args.base_url,
        model_id=model_id,
        provider=provider,
        max_output_tokens=args.max_output_tokens,
        stream=stream,
        principal_id=args.principal_id,
        workspace_id=args.workspace_id,
        lane=args.lane,
    )
    client = ChatClient(api, config, caps)
    try:
        client.prime_offsets()
        repl_loop(client)
    finally:
        api.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### src/dbl_chat_cli/client.py

```
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .gateway_api import Capabilities, GatewayAPI


@dataclass(frozen=True)
class ClientConfig:
    base_url: str
    model_id: str
    provider: str | None
    max_output_tokens: int | None
    stream: bool
    principal_id: str
    workspace_id: str | None
    lane: str


class ChatClient:
    def __init__(self, api: GatewayAPI, config: ClientConfig, caps: Capabilities) -> None:
        self._api = api
        self._config = config
        self._caps = caps
        self._offset = 0
        self._last_index = -1
        self._thread_id = str(uuid.uuid4())
        self._last_turn_id: str | Optional[str] = None

    @property
    def config(self) -> ClientConfig:
        return self._config

    def prime_offsets(self) -> None:
        if not self._caps.surfaces.get("snapshot", False):
            return
        snap = self._api.snapshot(offset=0, limit=1)
        length = int(snap.get("length", 0))
        self._offset = max(0, length)
        events = snap.get("events", [])
        if events:
            idx = events[-1].get("index")
            if isinstance(idx, int):
                self._last_index = idx

    def send_message(self, message: str) -> dict[str, Any]:
        correlation_id = str(uuid.uuid4())
        turn_id = str(uuid.uuid4())
        input_bytes = len(message.encode("utf-8"))
        input_chars = len(message)
        inputs = {
            "principal_id": self._config.principal_id,
            "workspace_id": self._config.workspace_id,
            "intent_type": "chat.message",
            "capability": "chat",
            "model_id": self._config.model_id,
            "provider": self._config.provider,
            "max_output_tokens": self._config.max_output_tokens,
            "input_chars": input_chars,
            "input_bytes": input_bytes,
        }
        inputs = {k: v for k, v in inputs.items() if v is not None}

        envelope = {
            "interface_version": 2,
            "correlation_id": correlation_id,
            "payload": {
                "stream_id": "default",
                "lane": self._config.lane,
                "actor": "dbl-chat-cli",
                "intent_type": "chat.message",
                "thread_id": self._thread_id,
                "turn_id": turn_id,
                "parent_turn_id": self._last_turn_id,
                "payload": {
                    "message": message,
                },
                "inputs": inputs,
                "requested_model_id": self._config.model_id,
            },
        }
        self._last_turn_id = turn_id
        ack = self._api.post_intent(envelope)
        return {"correlation_id": correlation_id, "ack": ack}

    def wait_for_response(self, correlation_id: str) -> Optional[str]:
        if self._config.stream and self._caps.surfaces.get("tail", False):
            return self._wait_tail(correlation_id)
        if self._caps.surfaces.get("snapshot", False):
            return self._wait_poll(correlation_id)
        raise RuntimeError("no supported read surface")

    def _wait_tail(self, correlation_id: str) -> Optional[str]:
        since = max(self._last_index, -1)
        for event in self._api.tail(since=since):
            self._last_index = _update_last_index(self._last_index, event)
            content = _extract_response(event, correlation_id)
            if content is not None:
                return content
        return None

    def _wait_poll(self, correlation_id: str) -> Optional[str]:
        delay = 0.5
        failures = 0
        while True:
            try:
                requested = self._offset
                snap = self._api.snapshot(offset=requested, limit=200)
                events = snap.get("events", [])
                self._offset = requested + len(events)
                for event in events:
                    self._last_index = _update_last_index(self._last_index, event)
                    content = _extract_response(event, correlation_id)
                    if content is not None:
                        return content
                failures = 0
                delay = min(delay * 1.5, 5.0)
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    raise RuntimeError("snapshot not supported") from exc
                failures += 1
                delay = min(delay * 2, 15.0)
                if failures >= 5:
                    raise RuntimeError("polling failed")
            time.sleep(delay)


def _update_last_index(current: int, event: dict[str, Any]) -> int:
    idx = event.get("index")
    if isinstance(idx, int) and idx > current:
        return idx
    return current


def _extract_response(event: dict[str, Any], correlation_id: str) -> Optional[str]:
    if event.get("correlation_id") != correlation_id:
        return None
    kind = event.get("kind")
    payload = event.get("payload")
    if kind == "EXECUTION" and isinstance(payload, dict):
        output = payload.get("output_text")
        if isinstance(output, str) and output.strip():
            return output
        alt_output = payload.get("output") or payload.get("result")
        if isinstance(alt_output, dict):
            text = alt_output.get("text")
            if isinstance(text, str) and text.strip():
                return text
        err = payload.get("error")
        if isinstance(err, dict):
            code = err.get("code", "error")
            message = err.get("message", "")
            return f"(execution_error) {code}: {message}"
        return "(execution) no output_text"
    if kind == "DECISION" and isinstance(payload, dict):
        if payload.get("decision") == "DENY":
            return f"decision: {payload}"
        return None
    return None
```

### src/dbl_chat_cli/gateway_api.py

```
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import httpx


@dataclass(frozen=True)
class Capabilities:
    interface_version: int
    providers: list[dict[str, Any]]
    surfaces: dict[str, bool]


class GatewayAPI:
    def __init__(self, base_url: str, timeout_s: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._client = httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def get_capabilities(self) -> Capabilities:
        url = f"{self._base_url}/capabilities"
        resp = self._client.get(url)
        resp.raise_for_status()
        payload = resp.json()
        return Capabilities(
            interface_version=int(payload.get("interface_version", 0)),
            providers=list(payload.get("providers", [])),
            surfaces=dict(payload.get("surfaces", {})),
        )

    def post_intent(self, envelope: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/ingress/intent"
        resp = self._client.post(url, json=envelope)
        resp.raise_for_status()
        return resp.json()

    def snapshot(self, *, offset: int, limit: int = 200) -> dict[str, Any]:
        url = f"{self._base_url}/snapshot"
        resp = self._client.get(url, params={"offset": offset, "limit": limit})
        if resp.status_code == 404:
            raise httpx.HTTPStatusError("snapshot not found", request=resp.request, response=resp)
        resp.raise_for_status()
        return resp.json()

    def tail(self, *, since: int = -1) -> Iterable[dict[str, Any]]:
        url = f"{self._base_url}/tail"
        with self._client.stream("GET", url, params={"since": since}) as resp:
            resp.raise_for_status()
            data_lines: list[str] = []
            for line in resp.iter_lines():
                if line is None:
                    continue
                text = line.strip()
                if text == "":
                    if data_lines:
                        data = "\n".join(data_lines)
                        data_lines = []
                        try:
                            yield json.loads(data)
                        except json.JSONDecodeError:
                            continue
                    continue
                if text.startswith("data:"):
                    data_lines.append(text[5:].lstrip())
```

### src/dbl_chat_cli/repl.py

```
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import CompleteStyle

from .client import ChatClient


@dataclass
class ReplResult:
    text: str | None
    exit_requested: bool = False


def _build_session() -> PromptSession[str]:
    bindings = KeyBindings()

    @bindings.add("c-m")
    def _(event) -> None:
        event.app.current_buffer.validate_and_handle()

    @bindings.add("c-c")
    def _(event) -> None:
        event.app.current_buffer.reset()
        event.app.exit(result="")

    session = PromptSession(
        message="you> ",
        multiline=True,
        key_bindings=bindings,
        complete_style=CompleteStyle.READLINE_LIKE,
    )
    return session


def repl_loop(client: ChatClient) -> None:
    session = _build_session()
    while True:
        try:
            text = session.prompt()
        except KeyboardInterrupt:
            print("(cancelled)")
            continue
        except EOFError:
            print("(exit)")
            break
        if not text.strip():
            continue
        try:
            result = client.send_message(text)
            correlation_id = result["correlation_id"]
            print("assistant> ", end="", flush=True)
            response = client.wait_for_response(correlation_id)
            if response:
                print(response)
            else:
                print("")
        except KeyboardInterrupt:
            print("\n(cancelled)")
        except Exception as exc:
            print(f"\n(error) {exc}")
```
