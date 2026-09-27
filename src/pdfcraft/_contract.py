# GENERATED FILE — do not edit.
# Written by packages/contract/scripts/sync-polyglot.mjs from the same
# definitions the PDFCraft API validates requests against.
# Editing it by hand will be silently overwritten on the next sync.

CONTRACT_HASH = "f5af303a2509e0d11d0c6efdc069e9acb80fddcd43a337a4789fa8f538f2588a"

#: Stable machine-readable codes. Branch on these, never on the message.
ERROR_CODES: tuple[str, ...] = (
    "invalid_request",
    "invalid_api_key",
    "payment_required",
    "not_found",
    "render_timeout",
    "render_failed",
    "rate_limited",
    "quota_exceeded",
    "demo_busy",
    "unsupported_file",
    "extraction_failed",
    "internal_error",
)

PAGE_FORMATS: tuple[str, ...] = (
    "A4",
    "A3",
    "A5",
    "Letter",
    "Legal",
    "Tabloid",
)

EMULATE_MEDIA: tuple[str, ...] = (
    "print",
    "screen",
)

OUTPUT_MODES: tuple[str, ...] = (
    "binary",
    "url",
)

EXTRACT_OUTPUT_MODES: tuple[str, ...] = (
    "inline",
    "url",
)

MAX_TIMEOUT_MS = 120000
DEFAULT_TIMEOUT_MS = 30000
