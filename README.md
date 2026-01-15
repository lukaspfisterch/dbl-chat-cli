# dbl-chat-cli

Thin CLI client for [deterministic-ai-gateway](https://github.com/lukaspfisterch/deterministic-ai-gateway).

## What it does

- Connects to `dbl-gateway` over HTTP only.
- Calls `GET /capabilities` on startup.
- Uses `/tail` if advertised, otherwise polls `/snapshot` with backoff.
- Sends `POST /ingress/intent` with `intent_type=chat.message`.
- No policy logic, no provider SDKs, no persistence.

## Installation

Clone the repository and install:

```bash
git clone https://github.com/lukaspfisterch/dbl-chat-cli.git
cd dbl-chat-cli
pip install -e .
```

## Usage

```bash
python -m dbl_chat_cli --base-url http://127.0.0.1:8010
```

powershell
python -m dbl_chat_cli `
  --base-url http://127.0.0.1:8010 `
  --principal-id user-1


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
