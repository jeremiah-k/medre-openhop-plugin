# Qualification gates

- Wheel layout and entry point accepted by the exact openHop manifest reader.
- Clean plugin venv install from controlled wheels; verify distribution versions
  and source revisions. No implicit dependencies on repeater's site-packages.
- Install disabled, settings reset, missing YAML refusal, enable, stop, restart,
  upgrade/rollback and preservation of MEDRE state; test failed dependency
  installation without damaging a working environment.
- Dedicated companion identity: no client eviction, backlog drain, reconnect,
  byte budget, loop prevention, native send failure and independent delivery
  observation. Hardware lab owns these tests; a local command acceptance is
  not RF delivery.
- Shutdown: stop manager while repeater remains alive; measure outstanding work,
  real adapter cleanup, escalation and process groups. Verify outer systemd,
  container and HA add-on deadlines. Explicitly gate full host shutdown and
  multiple simultaneously running plugins until qualified.
- Home Assistant: generic state/control first, then optional status sensors with
  a compatible core. Test absent/invalid/stale heartbeat, stopped plugin,
  unauthorized/missing runtime endpoint and preserved unrelated entities.
- Qualify aarch64 and amd64, Python versions, package resolution and read-only
  redaction. Do not publish catalogue entries or claim supported hardware until
  actual wheel-install and Docker/physical qualification pass.

`python scripts/verify_wheel.py dist/*.whl` performs an isolated installed-wheel
launcher smoke test using an explicitly identified MEDRE stand-in. It verifies
entrypoint execution, PID retention, the launch lock and SIGTERM delivery; it
does not install or qualify MEDRE or its SDK dependencies.
