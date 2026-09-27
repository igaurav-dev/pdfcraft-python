#!/usr/bin/env bash
# scripts/publish.sh
#
# Everything between "it works on my machine" and "a stranger can pip install
# it". Mirrors pdfcraft-dev/scripts/publish.sh step for step, because the two
# packages should fail for the same reasons.
#
#   ./scripts/publish.sh --dry-run          # every check, stops before uploading
#   ./scripts/publish.sh                    # checks, then asks, then uploads
#   ./scripts/publish.sh --yes              # no prompt, for CI
#
# Set PDFCRAFT_BASE_URL and PDFCRAFT_API_KEY to also render a real PDF through
# the built wheel. Without them that check is skipped and says so.
#
# THE RULE WITH NO UNDO: a version on PyPI can never be reused, even after you
# delete the release. Everything below exists so you find out before that.
set -euo pipefail

DRY_RUN=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --yes | -y) ASSUME_YES=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PKG_DIR="$PWD"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32mok\033[0m   %s\n' "$1"; }
skip() { printf '  \033[33mskip\033[0m %s\n' "$1"; }
die()  { printf '  \033[31mfail\033[0m %s\n' "$1" >&2; exit 1; }

PY="${PYTHON:-python3}"
NAME=pdfcraft
VERSION="$("$PY" - <<'EOF'
import re, pathlib
text = pathlib.Path("src/pdfcraft/_version.py").read_text()
print(re.search(r'__version__ = "([^"]+)"', text).group(1))
EOF
)"
printf '\033[1m%s %s\033[0m\n' "$NAME" "$VERSION"

step '1 · Repository state'
if git rev-parse --git-dir >/dev/null 2>&1; then
  DIRTY="$(git status --porcelain -- . 2>/dev/null)"
  if [ -n "$DIRTY" ]; then
    printf '  \033[31mfail\033[0m uncommitted changes in %s:\n%s\n' \
      "$PWD" "$(printf '%s\n' "$DIRTY" | sed 's/^/         /')" >&2
    printf '\n         commit them, or add them to .gitignore if they are build junk.\n' >&2
    exit 1
  fi
  ok "clean at $(git rev-parse --short HEAD)"
else
  skip 'not a git checkout'
fi

step '2 · Contract freshness'
# A release whose generated constants are stale ships something that disagrees
# with the server. This is the failure that put the TypeScript SDK 71 lines
# behind the contract while every test was green.
SYNC=../pdfcraft/packages/contract/scripts/sync-polyglot.mjs
if [ -f "$SYNC" ] && command -v node >/dev/null 2>&1; then
  BEFORE="$(cat src/pdfcraft/_contract.py)"
  node "$SYNC" . >/dev/null
  if [ "$BEFORE" != "$(cat src/pdfcraft/_contract.py)" ]; then
    die 'the generated contract was stale and has just been regenerated.
       Review the diff, bump the version, commit, then run this again.'
  fi
  ok 'generated contract matches the API'
else
  skip 'monorepo not beside this checkout — could not verify the contract'
fi

step '3 · Registry'
if command -v curl >/dev/null 2>&1; then
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -L --max-time 15 \
    "https://pypi.org/pypi/${NAME}/${VERSION}/json" || echo 000)"
  case "$CODE" in
    200) die "$NAME $VERSION is already on PyPI. A version can never be reused — bump it." ;;
    404) ok "$VERSION is unclaimed" ;;
    000) skip 'no network — could not check PyPI' ;;
    *)   skip "PyPI answered HTTP $CODE, which proves nothing — check by hand" ;;
  esac
else
  skip 'curl not installed'
fi

step '4 · Tests'
"$PY" -m pytest tests/ -q >/dev/null 2>&1 || die 'tests failed — run them by hand to see why'
ok 'tests pass'

step '5 · Build from clean'
rm -rf dist build ./*.egg-info src/*.egg-info
"$PY" -m pip install --quiet --upgrade build twine >/dev/null 2>&1 \
  || die 'could not install build/twine'
"$PY" -m build >/dev/null 2>&1 || die 'build failed — run "python3 -m build" to see why'
WHEEL="$(ls dist/*.whl 2>/dev/null | head -1)"
SDIST="$(ls dist/*.tar.gz 2>/dev/null | head -1)"
[ -n "$WHEEL" ] && [ -n "$SDIST" ] || die 'build produced no wheel and sdist'
ok "$(basename "$WHEEL")  +  $(basename "$SDIST")"

step '6 · Metadata as PyPI will render it'
# twine check catches a README that PyPI will refuse to render. You cannot
# re-upload the same version to fix it, so the page stays broken forever.
"$PY" -m twine check dist/* >/dev/null || die 'twine check failed — the PyPI page would render broken'
ok 'long_description renders'

CONTENTS="$("$PY" -c "import zipfile,sys; print('\n'.join(zipfile.ZipFile(sys.argv[1]).namelist()))" "$WHEEL")"
for required in pdfcraft/__init__.py pdfcraft/py.typed; do
  case "$CONTENTS" in *"$required"*) ;; *) die "$required is missing from the wheel" ;; esac
done
case "$CONTENTS" in
  *tests/*) die 'tests/ leaked into the wheel — check [tool.hatch.build.targets.wheel]' ;;
esac
ok 'wheel contains the package and py.typed, and nothing it should not'

step '7 · Install it as a stranger would'
"$PY" -m venv "$WORK/venv" >/dev/null
"$WORK/venv/bin/pip" install --quiet "$WHEEL" >/dev/null 2>&1 || die 'install from the wheel failed'
DEPS="$("$WORK/venv/bin/pip" list --format=freeze 2>/dev/null | grep -vcE '^(pip|setuptools|wheel|pdfcraft)==' || true)"
[ "${DEPS:-0}" -eq 0 ] || die "expected zero runtime dependencies, found $DEPS"
ok 'installs with zero dependencies'

"$WORK/venv/bin/python" - <<'EOF' || die 'import failed'
from pdfcraft import PDFCraft, PDFCraftError, ERROR_CODES, __version__
assert callable(PDFCraft) and issubclass(PDFCraftError, Exception)
assert "quota_exceeded" in ERROR_CODES
EOF
ok 'import works and the public names are present'

step '8 · Render a real PDF through the wheel'
if [ -n "${PDFCRAFT_API_KEY:-}" ] && [ -n "${PDFCRAFT_BASE_URL:-}" ]; then
  "$WORK/venv/bin/python" - <<'EOF' || die 'the built wheel could not render against the API'
import os, sys
from pdfcraft import PDFCraft
client = PDFCraft(os.environ["PDFCRAFT_API_KEY"], base_url=os.environ["PDFCRAFT_BASE_URL"])
pdf = client.render(html="<h1>preflight</h1>")
if not pdf.startswith(b"%PDF-"):
    sys.exit("not a pdf")
sys.stderr.write(f"    {len(pdf)} bytes\n")
EOF
  ok "rendered against $PDFCRAFT_BASE_URL"
else
  skip 'set PDFCRAFT_BASE_URL and PDFCRAFT_API_KEY to render for real'
fi

step '9 · Upload'
if [ "$DRY_RUN" -eq 1 ]; then
  printf '  every check passed. Re-run without --dry-run to upload %s %s.\n' "$NAME" "$VERSION"
  exit 0
fi
if [ "$ASSUME_YES" -eq 0 ]; then
  printf '  upload %s %s to PyPI? This cannot be undone. [y/N] ' "$NAME" "$VERSION"
  read -r answer </dev/tty
  case "$answer" in
    y | Y | yes) ;;
    *) printf '  nothing uploaded.\n'; exit 0 ;;
  esac
fi
"$PY" -m twine upload dist/*
ok "published — https://pypi.org/project/$NAME/$VERSION/"
printf '\n  tag the commit so this build is reproducible:\n    git tag -a v%s -m "%s %s" && git push origin v%s\n' \
  "$VERSION" "$NAME" "$VERSION" "$VERSION"
