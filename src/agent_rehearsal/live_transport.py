"""The sole credential/network boundary, constructed only after explicit live opt-in."""

from __future__ import annotations

import os


def api_key_present() -> bool:
    """Presence only; the value never leaves this module."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class AnthropicTransport:
    """Lazy optional SDK; fixed endpoint, no environment-selected gateway or retries."""

    def __init__(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY is required for an approved live run")
        from anthropic import Anthropic

        self.client = Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            base_url="https://api.anthropic.com",
            max_retries=0,
            timeout=60,
        )

    def __call__(self, request: dict) -> dict:
        # No tools, cache_control, batch, beta features, streaming, or enabled thinking.
        return self.client.messages.create(**request).model_dump(mode="json")

    def close(self) -> None:
        self.client.close()
