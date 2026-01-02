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
