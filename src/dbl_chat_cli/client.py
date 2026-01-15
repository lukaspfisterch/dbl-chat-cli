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
