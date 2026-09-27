"""PDFCraft — HTML to PDF, and PDF to structured JSON, in one call.

    from pdfcraft import PDFCraft

    client = PDFCraft("sk_live_...")
    pdf = client.render(html="<h1>hello</h1>")
    data = client.extract_pdf(pdf)

Zero dependencies. Every failure raises `PDFCraftError`; branch on `.code`.
"""

from ._client import DEFAULT_BASE_URL, PDFCraft
from ._contract import ERROR_CODES, PAGE_FORMATS
from ._errors import PDFCraftError
from ._version import __version__

__all__ = [
    "PDFCraft",
    "PDFCraftError",
    "ERROR_CODES",
    "PAGE_FORMATS",
    "DEFAULT_BASE_URL",
    "__version__",
]
