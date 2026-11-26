# Development

Use the committed launcher for formatting and linting:

```bash
.trunk/trunk check --fix --all
.trunk/trunk check --all --no-fix --ci
```

CI runs the same tests; run `.trunk/trunk upgrade` only for an intentional
toolchain refresh, review the pin changes, then rerun both checks. Tool
versions are owned by `.trunk/trunk.yaml`; Python policy is owned by
`pyproject.toml`. Renovate tracks the inline upstream-source annotations on
external pins; preserve them when editing. The formatter targets the package's
minimum supported Python.

Run the deterministic test suite from any checkout with pytest; the core
checkout's locked environment works without installing the plugin:

```bash
../medre/.venv/bin/python -m pytest -q tests
```

Adjust `../medre` to the core checkout location. Compile checks cover the
package sources and tests:

```bash
python -m compileall -q src tests
```

Verify the installed-wheel launcher contract after packaging or launcher
changes:

```bash
python -m build --wheel
python scripts/verify_wheel.py dist/*.whl
```

## Change notes

Describe behavior, compatibility and validation in the PR. Update the
qualification gates in [docs/qualification.md](docs/qualification.md) together
with behavior that changes them. The repository is an unpublished candidate:
do not publish catalogue entries or enable production routes until the
qualification gates pass in the hardware lab.
