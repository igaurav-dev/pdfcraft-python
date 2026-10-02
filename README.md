# PDFCraft for Python

HTML to PDF, PDF to structured JSON, and accessibility triage for a whole document estate.
The official Python client for [PDFCraft](https://pdfcraft.dev).

**Zero dependencies.** The whole client is `urllib.request` plus a retry loop, so it installs
into a Lambda or a slim container without dragging a transitive tree behind it.

```bash
pip install pdfcraft-dev
```

Installs as `pdfcraft-dev`, imports as `pdfcraft` — the same split as
`python-dateutil`/`dateutil`. The plain name was taken on PyPI by an unrelated
project, and the npm package is `@pdfcraft-dev/pdf`, so the two registries at
least agree with each other.

## Render

```python
from pdfcraft import PDFCraft

client = PDFCraft("sk_live_...")

pdf = client.render(html="<h1>Invoice 1042</h1>")
open("invoice.pdf", "wb").write(pdf)
```

A URL instead of HTML, with page options:

```python
pdf = client.render(
    url="https://example.com/report",
    options={
        "format": "A4",
        "margin": {"top": "20mm", "bottom": "20mm"},
        "printBackground": True,
        "waitFor": {"selector": "#chart-ready", "networkIdle": True},
    },
    filename="report.pdf",
)
```

Need a link rather than bytes — for an email, or a file too big to hold in memory:

```python
result = client.render_to_url(html=invoice_html)
print(result["url"], result["expires_at"], result["pages"])
```

## Extract

A PDF in, its tables and labelled fields out, each with a bounding box:

```python
data = client.extract_pdf(open("statement.pdf", "rb").read())

for table in data["tables"]:
    print(table["header"], len(table["rows"]), table["confidence"])
```

Ask for specific fields by name and type:

```python
data = client.extract(
    file=base64_pdf,
    schema={"invoice_total": "currency", "due_date": "date", "po_number": "string"},
)
print(data["fields"]["invoice_total"]["value"])
```

There is **no OCR**. A scanned document has no text layer, comes back as `extraction_failed`,
and is not billed. Nothing is guessed by a model, so the same document always produces the
same answer — which is the point if you are reconciling numbers.

Extraction is billed per page read, so `options={"pages": "1-3"}` narrows the bill as well as
the work.

## Async

For documents slow enough that you would rather not hold the connection:

```python
job = client.render_async(url="https://example.com/huge", webhookUrl="https://you/hook")
status = client.get_render(job["id"])
```

`get_extraction` polls extractions. An extraction id is not a render id — each endpoint 404s
on the other's ids, deliberately.

## Accessibility

Point it at a domain and it finds every PDF, checks each against PDF/UA and WCAG 2.1 AA, and
returns a report ranked by severity weighted by reach, with a remediation cost range.

```python
import time

scan = client.scan(domain="example.gov", max_documents=500)
while scan["status"] not in ("succeeded", "failed"):
    time.sleep(10)
    scan = client.get_scan(scan["id"])

for doc in scan["documents"][:10]:          # already ranked — this is the fix list
    print(doc["severity"], doc["score"], doc["url"])
    print(f"  ${doc['cost_low_usd']:.0f}-${doc['cost_high_usd']:.0f} to remediate")
```

Exactly one source: `domain=`, `sitemap=` or `urls=`. Passing none or two raises
`PDFCraftError` before anything is sent, because a round trip to be told you contradicted
yourself is a round trip wasted.

A scan runs for minutes — one request per second per host is a rule we do not break — so it
returns an id immediately and you poll. `max_documents` is **clamped to your plan rather than
refused**; `discovered` minus `checked` is what was found and not looked at, which is also the
upgrade prompt.

`report_url` on a finished scan is a share token. Anyone holding it can read the full HTML
report, and `GET /r/<token>/pdf` renders the same report to PDF through the render API. Treat
it as a credential, not an identifier.

Each finding carries `severity` (`blocker`, `major`, `minor`), the `wcag` criteria it breaks, a
`message` written for whoever approves the budget, and `technical_detail` for whoever does the
work. `occurrences` is volume, not severity — one check failing 1,535 times is one thing wrong,
fixed once, so never rank on it.

```python
from pdfcraft import A11Y_SEVERITIES, FINDING_LAYERS, SCAN_STATUSES
```

Those are generated from the same contract the API validates against, so comparing against them
beats comparing against a string you typed.

Accessibility is a **separate subscription** from rendering. An account can hold either, both or
neither, and the free tier is a real scan of 25 documents with full findings.

## Errors

Every failure raises `PDFCraftError`. Branch on `.code`, which is stable; the message is
written for a human and may be reworded.

```python
from pdfcraft import PDFCraft, PDFCraftError

try:
    pdf = client.render(html=page)
except PDFCraftError as error:
    if error.code == "quota_exceeded":
        ...          # out of renders this period
    elif error.code == "render_failed":
        ...          # their HTML broke — billable, and worth logging
    elif error.retryable:
        ...          # already retried; this is after the last attempt
    else:
        raise
```

`error.docs_url` points at the page explaining that specific code.

### What is and is not billed

A render is billable if Chromium actually ran. Successes and `render_failed` count;
`render_timeout`, `internal_error` and every 4xx that never reached the browser do not.

## Retries

Retries happen automatically on `429` and `5xx` and on network failures — never on a `4xx`
other than `429`, because those fail identically however often you ask. `Retry-After` is
honoured when the API sends it, otherwise the backoff is exponential with jitter.

```python
client = PDFCraft("sk_live_...", max_retries=0)      # off
client = PDFCraft("sk_live_...", timeout=200.0)      # seconds
client = PDFCraft("sk_live_...", base_url="https://gateway.internal")
```

## Typing

Ships `py.typed`. Responses are plain dicts rather than dataclasses: the API's response shape
grows, and a dict that gains a key is a non-event where a frozen dataclass is a crash. The
option and error enumerations you might want to validate against are exported:

```python
from pdfcraft import ERROR_CODES, PAGE_FORMATS
```

Those are generated from the same definitions the API validates requests against, so they
cannot drift from the server.

## Links

- Docs — https://pdfcraft.dev/docs/
- Every error code, cause and fix — https://pdfcraft.dev/errors/
- Extraction guides on 27 real document shapes — https://pdfcraft.dev/guides/
- TypeScript SDK — `@pdfcraft-dev/pdf`
- Go SDK — `github.com/igaurav-dev/pdfcraft-go`

MIT licensed.
