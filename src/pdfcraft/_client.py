"""The PDFCraft client.

Mirrors the TypeScript SDK's surface method for method, so the docs can show
the same call in both languages and mean it. Where the two differ it is
because Python idiom demands it: snake_case, keyword arguments, and bytes
rather than ``Uint8Array``.

Zero dependencies, on purpose. The whole client is ``urllib.request`` plus
about forty lines of retry logic, and an SDK that drags ``requests`` and its
transitive tree into a customer's lockfile to save those forty lines is a bad
trade — especially for anyone installing into a Lambda or a slim container.
The TypeScript SDK makes the same choice with ``fetch``.
"""

from __future__ import annotations

import base64
import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence

from ._errors import PDFCraftError, to_error
from ._version import __version__

DEFAULT_BASE_URL = "https://api.pdfcraft.dev"
DEFAULT_MAX_RETRIES = 3
# Just past the API's own 120s ceiling, so a server-side timeout surfaces as
# the API's `render_timeout` — which tells you what happened — rather than as a
# client-side socket timeout, which tells you nothing.
DEFAULT_TIMEOUT = 130.0

JsonDict = dict[str, Any]


class PDFCraft:
    """A PDFCraft API client.

    >>> pdf = PDFCraft("sk_live_...").render(html="<h1>hello</h1>")
    >>> pdf[:4]
    b'%PDF'
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        if not api_key:
            raise PDFCraftError("invalid_api_key", "An API key is required.", 401)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._timeout = timeout
        # Injectable so the tests can run without a network, and so anyone
        # behind a corporate proxy can hand in their own opener.
        self._opener = opener or urllib.request.build_opener()

    # ── rendering ─────────────────────────────────────────────────────────

    def render(self, *, idempotency_key: str | None = None, **input: Any) -> bytes:
        """Render HTML or a URL and return the PDF bytes.

        The method decides the output mode, so passing ``output`` yourself
        could only contradict it — it is rejected rather than ignored, because
        ``render(output="url")`` quietly returning JSON is the kind of surprise
        that costs an afternoon.
        """
        body = _with_output(input, "binary")
        return self._request("POST", "/v1/render", body, idempotency_key).read()

    def render_to_url(self, *, idempotency_key: str | None = None, **input: Any) -> JsonDict:
        """Store the PDF and return a signed link instead of the bytes."""
        body = _with_output(input, "url")
        return _json(self._request("POST", "/v1/render", body, idempotency_key))

    def render_async(self, *, idempotency_key: str | None = None, **input: Any) -> JsonDict:
        """Queue a render; the webhook fires when it settles."""
        return _json(self._request("POST", "/v1/render/async", dict(input), idempotency_key))

    def get_render(self, render_id: str) -> JsonDict:
        return _json(self._request("GET", f"/v1/renders/{_quote(render_id)}"))

    def usage(self) -> JsonDict:
        """Current period usage and quota."""
        return _json(self._request("GET", "/v1/usage"))

    # ── extraction ────────────────────────────────────────────────────────

    def extract(self, *, idempotency_key: str | None = None, **input: Any) -> JsonDict:
        """A PDF in, its tables and labelled fields out, each with a bounding box.

        ``file`` takes base64 PDF bytes or an https URL to one; pass ``url`` or
        ``html`` instead and the page is rendered first, then extracted — one
        call, one charge. Billed per page read, so ``options={"pages": ...}``
        narrows the bill as well as the work.

        There is no OCR. A scan has no text layer and comes back as
        ``extraction_failed``, unbilled. Nothing is guessed by a model, so the
        same document always produces the same answer.
        """
        body = _with_output(input, "inline")
        return _json(self._request("POST", "/v1/extract", body, idempotency_key))

    def extract_to_url(self, *, idempotency_key: str | None = None, **input: Any) -> JsonDict:
        body = _with_output(input, "url")
        return _json(self._request("POST", "/v1/extract", body, idempotency_key))

    def extract_async(self, *, idempotency_key: str | None = None, **input: Any) -> JsonDict:
        return _json(self._request("POST", "/v1/extract/async", dict(input), idempotency_key))

    def get_extraction(self, extraction_id: str) -> JsonDict:
        """Poll one extraction.

        An extraction id is not a render id: ``get_render`` will 404 on one and
        this will 404 on a render id, deliberately.
        """
        return _json(self._request("GET", f"/v1/extractions/{_quote(extraction_id)}"))

    def extract_pdf(
        self,
        pdf: bytes,
        *,
        idempotency_key: str | None = None,
        **input: Any,
    ) -> JsonDict:
        """Convenience for the common case: hand it PDF bytes, it does the base64.

        Kept out of ``extract`` itself so that call stays a plain dict you can
        log, diff or replay.
        """
        return self.extract(
            file=base64.b64encode(pdf).decode("ascii"),
            idempotency_key=idempotency_key,
            **input,
        )

    # ── accessibility ─────────────────────────────────────────────────────

    def scan(
        self,
        *,
        domain: str | None = None,
        sitemap: str | None = None,
        urls: Sequence[str] | None = None,
        **options: Any,
    ) -> JsonDict:
        """Start an accessibility scan. Returns at once with an id to poll.

        Exactly one source. A scan of a thousand documents at one request per
        second per host has a floor measured in minutes, so there is nothing to
        return but an id and somewhere to look.

            scan = client.scan(domain="example.gov", max_documents=500)
            while scan["status"] not in ("succeeded", "failed"):
                time.sleep(10)
                scan = client.get_scan(scan["id"])

        ``max_documents`` is clamped to your plan rather than refused, and the
        gap between what was found and what was checked is reported back as
        ``discovered`` minus ``checked``.
        """
        source = {
            key: value
            for key, value in (("domain", domain), ("sitemap", sitemap), ("urls", list(urls) if urls else None))
            if value is not None
        }
        if len(source) != 1:
            raise PDFCraftError(
                "invalid_request",
                "exactly one of domain, sitemap or urls is required",
                400,
            )
        body: JsonDict = {"source": source}
        if options:
            body["options"] = dict(options)
        return _json(self._request("POST", "/v1/a11y/scan", body))

    def get_scan(self, scan_id: str) -> JsonDict:
        """Poll one scan.

        Returns progress while it runs and the full result — every document,
        ranked, with findings and cost — once it succeeds. ``report_url`` is a
        share token: anyone with it can read the report, no account needed.
        """
        return _json(self._request("GET", f"/v1/a11y/scans/{_quote(scan_id)}"))

    # ── transport ─────────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "authorization": f"Bearer {self._api_key}",
            "user-agent": f"pdfcraft-sdk-python/{__version__}",
        }
        if payload is not None:
            headers["content-type"] = "application/json"
        if idempotency_key:
            headers["idempotency-key"] = idempotency_key

        last: PDFCraftError | None = None
        for attempt in range(self._max_retries + 1):
            request = urllib.request.Request(
                f"{self._base_url}{path}", data=payload, headers=headers, method=method
            )
            try:
                return self._opener.open(request, timeout=self._timeout)
            except urllib.error.HTTPError as response:
                last = to_error(response.code, response.read())
                if not last.retryable or attempt == self._max_retries:
                    raise last from None
                time.sleep(_retry_after(response) or _backoff(attempt))
            except (urllib.error.URLError, TimeoutError, OSError) as cause:
                last = PDFCraftError("network_error", f"Could not reach PDFCraft: {cause}", 0)
                if attempt == self._max_retries:
                    raise last from None
                time.sleep(_backoff(attempt))

        raise last or PDFCraftError("internal_error", "Request failed.", 500)


def _with_output(input: Mapping[str, Any], mode: str) -> JsonDict:
    if "output" in input:
        raise PDFCraftError(
            "invalid_request",
            "Do not pass `output`; the method you call decides it.",
            400,
        )
    return {**input, "output": mode}


def _json(response: Any) -> JsonDict:
    return json.loads(response.read().decode("utf-8"))


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _backoff(attempt: int) -> float:
    """Exponential with jitter, matching the TypeScript SDK's curve exactly."""
    return 0.5 * (2**attempt) * (0.75 + random.random() * 0.5)


def _retry_after(response: Any) -> float | None:
    header = response.headers.get("retry-after") if hasattr(response, "headers") else None
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except (TypeError, ValueError):
        return None
