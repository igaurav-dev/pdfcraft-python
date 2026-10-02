"""Behavioural tests against a real local HTTP server.

Not mocks of urllib: the things worth testing here are the header we send, the
status codes we retry, and the moment we stop — all of which live in the gap
between our code and the socket. A mock of the transport would assert that our
code calls our code.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pdfcraft import PDFCraft, PDFCraftError
from pdfcraft._contract import (
    A11Y_SEVERITIES,
    CONTRACT_HASH,
    ERROR_CODES,
    FINDING_LAYERS,
    SCAN_STATUSES,
)

STATE: dict[str, object] = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # keep pytest output readable
        pass

    def _record(self) -> dict[str, object]:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        STATE.setdefault("requests", []).append(  # type: ignore[union-attr]
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": json.loads(raw) if raw else None,
            }
        )
        return STATE

    def _reply(self) -> None:
        self._record()
        script = STATE.get("script") or []
        attempt = len(STATE["requests"])  # type: ignore[arg-type]
        step = script[min(attempt - 1, len(script) - 1)] if script else (200, b"%PDF-1.7 ok", {})
        status, body, headers = step
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _reply
    do_POST = _reply


@pytest.fixture()
def server():
    STATE.clear()
    STATE["requests"] = []
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def client(base: str, **kw: object) -> PDFCraft:
    return PDFCraft("sk_live_test", base_url=base, **kw)  # type: ignore[arg-type]


def test_requires_an_api_key() -> None:
    with pytest.raises(PDFCraftError) as caught:
        PDFCraft("")
    assert caught.value.code == "invalid_api_key"


def test_render_returns_bytes_and_sends_the_right_headers(server: str) -> None:
    pdf = client(server).render(html="<h1>hi</h1>", idempotency_key="abc-123")
    assert pdf.startswith(b"%PDF")

    sent = STATE["requests"][0]  # type: ignore[index]
    assert sent["path"] == "/v1/render"
    assert sent["headers"]["authorization"] == "Bearer sk_live_test"
    assert sent["headers"]["idempotency-key"] == "abc-123"
    assert sent["headers"]["user-agent"].startswith("pdfcraft-sdk-python/")
    # The method decides the output mode, not the caller.
    assert sent["body"] == {"html": "<h1>hi</h1>", "output": "binary"}


def test_output_cannot_be_contradicted(server: str) -> None:
    # render(output="url") returning JSON instead of bytes is the kind of
    # silent surprise that costs an afternoon, so it is refused outright.
    with pytest.raises(PDFCraftError) as caught:
        client(server).render(html="<p>x</p>", output="url")
    assert caught.value.code == "invalid_request"
    assert STATE["requests"] == []


def test_maps_the_error_envelope_to_a_typed_exception(server: str) -> None:
    STATE["script"] = [
        (
            429,
            json.dumps(
                {
                    "error": {
                        "code": "quota_exceeded",
                        "message": "Monthly limit reached.",
                        "docs_url": "https://pdfcraft.dev/errors#quota_exceeded",
                    }
                }
            ).encode(),
            {"content-type": "application/json"},
        )
    ]
    with pytest.raises(PDFCraftError) as caught:
        client(server, max_retries=0).render(html="x")
    error = caught.value
    assert error.code == "quota_exceeded"
    assert error.status == 429
    assert error.docs_url.endswith("#quota_exceeded")
    assert error.retryable is True


def test_does_not_retry_a_4xx_that_is_not_429(server: str) -> None:
    STATE["script"] = [(400, b'{"error":{"code":"invalid_request"}}', {})]
    with pytest.raises(PDFCraftError) as caught:
        client(server, max_retries=3).render(html="x")
    assert caught.value.code == "invalid_request"
    # One attempt only: a 400 will fail identically however often we ask.
    assert len(STATE["requests"]) == 1  # type: ignore[arg-type]


def test_retries_a_500_then_succeeds(server: str) -> None:
    STATE["script"] = [
        (500, b'{"error":{"code":"internal_error"}}', {}),
        (200, b"%PDF-1.7 recovered", {}),
    ]
    pdf = client(server, max_retries=3).render(html="x")
    assert pdf.startswith(b"%PDF")
    assert len(STATE["requests"]) == 2  # type: ignore[arg-type]


def test_honours_retry_after_over_its_own_backoff(server: str) -> None:
    STATE["script"] = [
        (429, b'{"error":{"code":"rate_limited"}}', {"retry-after": "0"}),
        (200, b"%PDF-1.7 ok", {}),
    ]
    assert client(server, max_retries=2).render(html="x").startswith(b"%PDF")
    assert len(STATE["requests"]) == 2  # type: ignore[arg-type]


def test_survives_a_non_json_error_body(server: str) -> None:
    # A proxy in front of the API returns an HTML page. Failing to parse it
    # would hide the status code that actually explains the problem.
    STATE["script"] = [(502, b"<html><body>Bad Gateway</body></html>", {})]
    with pytest.raises(PDFCraftError) as caught:
        client(server, max_retries=0).render(html="x")
    assert caught.value.status == 502
    assert caught.value.code == "internal_error"


def test_extract_pdf_base64s_the_bytes(server: str) -> None:
    STATE["script"] = [(200, b'{"id":"ext_1","pages":1,"tables":[]}', {})]
    result = client(server).extract_pdf(b"%PDF-1.7 tiny")
    assert result["id"] == "ext_1"
    body = STATE["requests"][0]["body"]  # type: ignore[index]
    assert body["output"] == "inline"
    import base64

    assert base64.b64decode(body["file"]) == b"%PDF-1.7 tiny"


def test_extraction_ids_go_to_the_extraction_endpoint(server: str) -> None:
    STATE["script"] = [(200, b'{"id":"ext_1","status":"succeeded"}', {})]
    client(server).get_extraction("ext_01ABC")
    assert STATE["requests"][0]["path"] == "/v1/extractions/ext_01ABC"  # type: ignore[index]


def test_generated_contract_is_present_and_plausible() -> None:
    # The generated file is how this package learns about a contract change.
    # If it is stale, everything above still passes and a customer finds out.
    assert len(CONTRACT_HASH) == 64
    assert "quota_exceeded" in ERROR_CODES
    assert "render_timeout" in ERROR_CODES


def test_scan_requires_exactly_one_source(server: str) -> None:
    # Caught here rather than sent. The API would reject it too, but a round
    # trip to be told you contradicted yourself is a round trip wasted — and
    # the error names the argument the caller typed, not a JSON path.
    with pytest.raises(PDFCraftError) as caught:
        client(server).scan(domain="example.gov", sitemap="https://example.gov/sitemap.xml")
    assert caught.value.code == "invalid_request"
    with pytest.raises(PDFCraftError):
        client(server).scan()


def test_scan_posts_the_contract_shape(server: str) -> None:
    STATE["script"] = [(202, b'{"id":"scn_1","status":"queued","discovered":null}', {})]
    accepted = client(server).scan(domain="example.gov", max_documents=500)
    assert accepted["status"] == "queued"
    request = STATE["requests"][0]  # type: ignore[index]
    assert request["path"] == "/v1/a11y/scan"
    # source and options are separate objects; flattening them is the shape the
    # API rejects, and it is the mistake a kwargs-only signature invites.
    assert request["body"] == {
        "source": {"domain": "example.gov"},
        "options": {"max_documents": 500},
    }


def test_scan_ids_go_to_the_scan_endpoint(server: str) -> None:
    STATE["script"] = [(200, b'{"id":"scn_1","status":"succeeded","checked":2}', {})]
    client(server).get_scan("scn_01ABC")
    assert STATE["requests"][0]["path"] == "/v1/a11y/scans/scn_01ABC"  # type: ignore[index]


def test_accessibility_enums_are_generated_not_typed() -> None:
    # A client comparing a severity against a hand-typed string is a client
    # that silently stops matching the day a value is added.
    assert A11Y_SEVERITIES[0] == "blocker"
    assert "succeeded" in SCAN_STATUSES
    assert set(FINDING_LAYERS) == {"machine", "geometric"}
