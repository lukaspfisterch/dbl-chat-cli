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

