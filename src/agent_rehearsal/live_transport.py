"""The sole credential/network boundary, constructed only after explicit live opt-in."""

from __future__ import annotations

import os
import re


def api_key_present() -> bool:
    """Presence only; the value never leaves this module."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def validate_api_key() -> None:
    """Reject malformed keys without disclosing any characters (positions are 1-based)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY is missing")
    positions = [
        str(i) for i, char in enumerate(key, 1) if not re.fullmatch(r"[A-Za-z0-9_-]", char)
    ]
    if positions:
        raise ValueError(
            "ANTHROPIC_API_KEY has invalid characters at positions (1-based): "
            + ", ".join(positions)
        )


class TransportFailure(Exception):
    """Credential-free description of a failed dispatch: class, status, request id only.

    rejected=True only for an HTTP 4xx error response: the API refused the request before
    processing it, and failed requests are not billed. Timeouts, dropped connections and
    5xx responses stay uncertain because the request may have been processed.
    """

    def __init__(
        self,
        exception_class: str,
        status_code: int | None = None,
        request_id: str | None = None,
    ):
        self.exception_class = exception_class
        self.status_code = status_code
        self.request_id = request_id
        self.rejected = status_code is not None and 400 <= status_code < 500
        status = f"HTTP {status_code} " if status_code is not None else ""
        self.reason = f"{status}{exception_class}"
        super().__init__(self.reason)

    def record(self) -> dict:
        return {
            "exception_class": self.exception_class,
            "status_code": self.status_code,
            "request_id": self.request_id,
            "billing": "not_billed_rejected" if self.rejected else "uncertain",
        }


def classify(exc: BaseException) -> TransportFailure:
    """Map an SDK exception to a TransportFailure without keeping its message text."""
    import anthropic

    name = type(exc).__name__
    if isinstance(exc, anthropic.APIStatusError):
        request_id = getattr(exc, "request_id", None)
        if not (isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_\-]{1,128}", request_id)):
            request_id = None
        return TransportFailure(name, int(exc.status_code), request_id)
    # APITimeoutError, APIConnectionError and anything unexpected: no status, uncertain.
    return TransportFailure(name)


class AnthropicTransport:
    """Lazy optional SDK; fixed endpoint, no environment-selected gateway or retries."""

    def __init__(self):
        validate_api_key()
        from anthropic import Anthropic

        self.client = Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            base_url="https://api.anthropic.com",
            max_retries=0,
            timeout=60,
        )

    def __call__(self, request: dict) -> dict:
        # Client decision tools only; no caching, server tools, streaming or enabled thinking.
        try:
            message = self.client.messages.create(**request)
        except Exception as exc:
            # `from None`: the SDK exception text and chained context never propagate.
            raise classify(exc) from None
        return message.model_dump(mode="json")

    def close(self) -> None:
        self.client.close()
