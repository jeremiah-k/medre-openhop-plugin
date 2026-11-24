# MEDRE openHop plugin

Candidate managed deployment of MEDRE as an external openHop Repeater service
plugin. Uses the existing MeshCore TCP adapter; openHop owns the companion
identity, packet routing and radio. MEDRE owns cross-network routes. Neither
process imports the other project's Python modules.

**Qualification status:** development candidate; not published or qualified for
live RF or Home Assistant OS deployment. Do not enable production routes until
the MEDRE hardware lab's openHop interoperability work passes.

## Install and configure

1. Build a wheel from the pinned source environment (`uv build --wheel`). The
   wheel contains the manager's schema-1 manifest and settings defaults. Its
   direct requirements match the November 24 MEDRE stack. Use a controlled
   wheel source for reproducible installs; the plugin manager's wheel checksum
   does not lock transitive dependencies. Check Python >=3.11 and native SDK
   availability on the actual target (including ARM64).
2. Install the wheel from openHop's Plugins dashboard. Keep the new plugin
   disabled. Create a **dedicated** virtual companion in openHop with its own
   identity, channel configuration and private TCP listener. No connectivity
   probes or second clients against a companion already in use.
3. As the repeater service account, create the private YAML at
   `<plugin data>/medre/config.yaml` (mode 0600), using the MEDRE configuration
   guide. `examples/bridge.yaml` is a disabled-route starting point; replace
   its placeholders with actual provisioned channel indices and endpoints.
   The manager's settings JSON is not a secret store. Keep credentials, keys,
   and transport state in MEDRE's private home; do not put them into settings.
4. Validate using the plugin venv's `python -I -m medre config check --config
   PATH` and `routes plan --config PATH`, with `MEDRE_HOME` set to the private
   MEDRE home. Do not run a second runtime for health checks. Review routes,
   explicitly enable the intended test route, and enable/start from openHop.
5. Use the existing dashboard or HA plugin lifecycle services to stop/start.
   The initial five-second upstream stop budget is shorter than MEDRE's
   default ten-second drain window. Apply and qualify the separate openHop
   timeout candidate before relying on managed stop. Until whole-host shutdown
   ordering and outer budgets are qualified, stop MEDRE explicitly while
   openHop is still running, verify clean exit, then stop openHop.

## Persistence and supervision

The manager supplies `OPENHOP_PLUGIN_ID=medre.bridge` and `OPENHOP_PLUGIN_DATA`.
The launcher rejects missing/unsafe configuration, holds a nonblocking launch
lock across `exec`, sets a dedicated `MEDRE_HOME` and replaces itself with the
installed MEDRE interpreter. It creates no subprocess supervisor, runs no
shell, and strips inherited `MEDRE_*` settings to avoid another bridge's config
or credentials leaking into this one. Python isolated mode excludes inherited
`PYTHONPATH` and user-site imports. The manager retains signal and process-group
ownership. The launch lock protects this wrapper, not arbitrary standalone
MEDRE invocations; do not run another bridge against the same identity or state.

`data/medre/` survives plugin upgrades. Back it up only under the project's
supported stopped-state procedure. Uninstall with data deletion is destructive.

## Home Assistant and status

Generic installed-plugin state, start/stop and settings work with the existing
HA integration. The optional `live_status` setting defaults to `false` so the
plugin is compatible with unmodified MEDRE 0.2.3. Enable it **only** when the
installed MEDRE supports `run --status-file` (the accompanying core candidate).
It publishes the content-free heartbeat to `data/runtime.json`; the manager's
existing runtime endpoint reads it. The accompanying HA candidate adds
read-only sensors. An unsupported CLI flag fails startup rather than silently
claiming telemetry works. A stale heartbeat is not current health, and process
state is not recipient delivery evidence.

No openHop credentials, companion private key, MQTT broker, HA token or new
transport adapter is required. The companion TCP endpoint is unauthenticated;
confine it to the private network or loopback namespace. The plugin is trusted
code running under the repeater account, not a security sandbox.

## Development

`PYTHONPATH=src python -m pytest -q` executes launcher tests without a radio.
Build/verify/install the wheel, qualify actual core and SDK versions, then run
lab campaigns before publishing a catalogue entry. See `docs/qualification.md`.
