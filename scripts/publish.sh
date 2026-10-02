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
# Output is captured and REPLAYED on failure rather than discarded.
#
# This used to end in "run them by hand to see why", which is a check that
# tells you something is wrong and then hides the one thing you need. A release
# script that makes you re-run the failing command to learn anything has not
# saved you a step, it has cost you one — and it cost exactly that, once.
#
# pytest is also checked for separately, because "not installed" and "15 tests
# failed" are different problems and the exit code alone does not say which.
if ! "$PY" -m pytest --version >/dev/null 2>&1; then
  WHICH="$("$PY" -c 'import sys; print(sys.executable)')"
  # A Homebrew or system Python on macOS and most distros is PEP 668
  # "externally managed": pip refuses to install into it at all. Telling
  # someone to run `pip install pytest` there is advice that cannot work, so
  # detect it and name the venv instead — which is the right answer anyway,
  # because step 5 needs build and twine in the same interpreter.
  if "$PY" -c 'import sysconfig,os,sys; sys.exit(0 if os.path.exists(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")) else 1)' 2>/dev/null; then
    die "pytest is not installed, and $WHICH
       is externally managed — pip will refuse to install into it.

       Make a virtualenv ( .venv/ is already gitignored ):
         python3 -m venv .venv && source .venv/bin/activate
         pip install pytest build twine
         $0 ${*:-}

       Keep it active for the upload too; twine runs from it."
  fi
  die "pytest is not installed for $WHICH.
       Install it:  $PY -m pip install pytest
       Or point this script at the interpreter that has it:  PYTHON=python3.12 $0"
fi
if ! "$PY" -m pytest tests/ -q >"$WORK/pytest.log" 2>&1; then
  printf '  \033[31mfail\033[0m tests failed:\n\n' >&2
  sed 's/^/    /' "$WORK/pytest.log" >&2
  printf '\n       re-run with:  %s -m pytest tests/ -q\n' "$PY" >&2
  exit 1
fi
ok "tests pass ($("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"

step '5 · Build from clean'
rm -rf dist build ./*.egg-info src/*.egg-info
if ! "$PY" -m pip install --quiet --upgrade build twine >"$WORK/pip.log" 2>&1; then
  printf '  \033[31mfail\033[0m could not install build/twine:\n\n' >&2
  sed 's/^/    /' "$WORK/pip.log" >&2
  exit 1
fi
if ! "$PY" -m build >"$WORK/build.log" 2>&1; then
  printf '  \033[31mfail\033[0m build failed:\n\n' >&2
  # The last 40 lines, not a path: $WORK is removed by the EXIT trap, so
  # pointing at a log file there would be a path that no longer exists by the
  # time anyone reads the message.
  tail -40 "$WORK/build.log" | sed 's/^/    /' >&2
  printf '\n       re-run with:  %s -m build\n' "$PY" >&2
  exit 1
fi
WHEEL="$(ls dist/*.whl 2>/dev/null | head -1)"
SDIST="$(ls dist/*.tar.gz 2>/dev/null | head -1)"
[ -n "$WHEEL" ] && [ -n "$SDIST" ] || die 'build produced no wheel and sdist'
ok "$(basename "$WHEEL")  +  $(basename "$SDIST")"

step '6 · Metadata as PyPI will render it'
# twine check catches a README that PyPI will refuse to render. You cannot
# re-upload the same version to fix it, so the page stays broken forever.
"$PY" -m twine check dist/* >/dev/null || die 'twine check failed — the PyPI page would render broken'
ok 'long_description renders'

# The check twine does NOT do, and the one that cost a failed 1.4.0 upload.
#
# PyPI validates Metadata-Version against a list it knows, currently topping out
# at 2.4. hatchling 1.28+ emits 2.5. The result is a bare `400 Bad Request`
# AFTER the file transfers to 100%, with no reason in the body — the least
# diagnosable failure in the whole pipeline, and twine check passes happily
# because it only renders the README.
#
# pyproject pins hatchling below 1.28. This asserts the outcome rather than
# trusting the pin, so the two have to be wrong together.
MAX_METADATA_VERSION=2.4
META_VERSION="$("$PY" - "$WHEEL" <<'EOF'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
for line in z.read(name).decode("utf-8", "replace").splitlines():
    if line.lower().startswith("metadata-version:"):
        print(line.split(":", 1)[1].strip())
        break
EOF
)"
if [ "$("$PY" -c "import sys; print(1 if tuple(map(int,'$META_VERSION'.split('.'))) > tuple(map(int,'$MAX_METADATA_VERSION'.split('.'))) else 0)")" = "1" ]; then
  die "the wheel declares Metadata-Version $META_VERSION, and PyPI accepts at most $MAX_METADATA_VERSION.
       Uploading it returns a bare 400 Bad Request with no reason, after the
       file has transferred in full.

       Your build backend is too new. pyproject pins hatchling<1.28 for exactly
       this; if that pin was raised or the isolated build ignored it, put it back:
         requires = [\"hatchling>=1.24,<1.28\"]
       then delete dist/ and run this again."
fi
ok "Metadata-Version $META_VERSION (PyPI accepts up to $MAX_METADATA_VERSION)"

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
