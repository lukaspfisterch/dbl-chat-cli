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
