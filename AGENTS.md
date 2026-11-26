# Repository Guidelines

MEDRE openHop plugin is a managed MEDRE deployment for openHop Repeater.
Runtime code is under `src/medre_openhop_plugin`; the package wraps MEDRE and
its transport adapters as an external service and does not register a MEDRE
transport adapter. Core owns shared MEDRE contracts; the hardware lab owns
live qualification. Read [CONTRIBUTING.md](CONTRIBUTING.md) for tool setup and
test commands.

Use the committed executable launcher for quality checks:

```bash
.trunk/trunk check --fix --all
.trunk/trunk check --all --no-fix --ci
```

Run `.trunk/trunk upgrade` when refreshing tools, then fix and check before
committing. Python policy lives in `pyproject.toml`; tool versions live in
`.trunk/trunk.yaml`. Preserve the upstream launcher bytes and the plugin
manifest's schema field.

Run tests from this repository with any Python environment that provides
pytest; deterministic tests only, no service or hardware tiers. Report exactly
which commands ran. Behavior changes include their documentation and
regression tests. PR descriptions carry behavior and validation; release notes
do not apply while the package is an unpublished candidate.

Keep credentials, identities, private MEDRE homes, local state, `.zcode/`,
`.core/` and generated reports outside Git. Preserve genuine commit authorship
and keep each commit focused on one concern.
