"""Every failure from the API arrives as one exception type. Branch on ``.code``.

Mirrors ``src/errors.ts`` in the TypeScript SDK deliberately, down to which
statuses count as retryable, so a bug report against one client can be read by
someone holding the other.
"""

from __future__ import annotations

import json
from typing import Any


class PDFCraftError(Exception):
    """A PDFCraft API failure.

    ``code`` is the stable machine-readable string from the API's error
    envelope — ``quota_exceeded``, ``render_timeout`` and so on — or
    ``network_error`` when the request never reached us. Branch on it rather
    than on the message, which is written for humans and may be reworded.
    """

    __slots__ = ("code", "status", "docs_url", "retryable")

    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        docs_url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.docs_url = docs_url
        # A 4xx other than 429 will fail identically however many times we ask.
        self.retryable = code == "network_error" or status == 429 or status >= 500

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PDFCraftError(code={self.code!r}, status={self.status})"


def to_error(status: int, body: bytes) -> PDFCraftError:
    """Turn an error response into a typed exception.

    Tolerates a non-JSON body on purpose: a proxy or load balancer in front of
    the API returns an HTML error page, and a client that raises
    ``JSONDecodeError`` there hides the status code that would have explained
    the problem.
    """
    payload: Any = None
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        payload = None

    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        error = {}

    return PDFCraftError(
        str(error.get("code") or "internal_error"),
        str(error.get("message") or f"PDFCraft responded {status}"),
        status,
        error.get("docs_url") or None,
    )
