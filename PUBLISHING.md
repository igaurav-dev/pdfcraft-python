# Publishing `pdfcraft` to PyPI

The name **`pdfcraft` is unclaimed** (checked with `pip index versions pdfcraft`, which
returns nothing). Note that an unrelated project called **`pdf-craft`** exists at 2.2.0 — a
different name under PyPI's normalisation rules, but close enough that the README and the
project description should make clear which one this is.

## Once, to set up

1. Create the PyPI project by uploading the first release (below). There is no way to reserve
   a name without publishing.
2. Prefer a **Trusted Publisher** (PyPI → the project → Publishing) over a long-lived API
   token, so no credential sits on disk. A token works too; scope it to this project only.

## Every release

```bash
# 1. The contract, first. A release whose generated constants are stale ships
#    something that disagrees with the server.
node ../pdfcraft/packages/contract/scripts/sync-polyglot.mjs .
git diff --stat src/pdfcraft/_contract.py     # empty is fine; non-empty needs a version bump

# 2. Bump the version in ONE place.
$EDITOR src/pdfcraft/_version.py

# 3. Test.
PYTHONPATH=src python3 -m pytest tests/ -q

# 4. Build both artefacts.
python3 -m pip install --upgrade build twine
python3 -m build                       # -> dist/*.whl and dist/*.tar.gz

# 5. Check the rendered README before it is public; PyPI will not let you
#    re-upload the same version to fix a broken one.
python3 -m twine check dist/*

# 6. Upload.
python3 -m twine upload dist/*
```

## The rule that has no undo

**A version number on PyPI can never be reused**, even after deleting the release. A broken
1.3.0 means shipping 1.3.1, and 1.3.0 stays visible forever. `twine check` and a real
`pip install` from `dist/` are the two minutes that prevent it:

```bash
python3 -m venv /tmp/verify && /tmp/verify/bin/pip install dist/*.whl
/tmp/verify/bin/python -c "import pdfcraft; print(pdfcraft.__version__)"
```

## Keep in step

The three SDKs share a version line so a reader can tell at a glance whether their Python
client knows about the same contract as their TypeScript one. When the contract changes, sync
and release all three, or none.
