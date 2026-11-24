"""Exercise the real wheel reader, heartbeat writer, storage/API projection and HA parser.

Only cached runtime facts are doubled; this is not a running bridge or HA instance.
"""
from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


def verify(core: Path, repeater: Path, ha: Path, wheel: Path) -> None:
    sys.path.insert(0, str(repeater.resolve()))
    from repeater.plugins.manager import PluginManager
    from repeater.plugins.manifest import load_manifest_from_wheel
    from repeater.plugins.storage import PluginStorage

    status_type = runpy.run_path(str(core / "src/medre/runtime/status_file.py"))[
        "RuntimeStatusFile"
    ]
    parse = runpy.run_path(str(ha / "custom_components/pymc_repeater/medre_status.py"))[
        "parse_runtime"
    ]
    manifest = load_manifest_from_wheel(wheel)
    assert manifest.id == "medre.bridge"
    assert manifest.config_defaults == {"live_status": False}
    with tempfile.TemporaryDirectory(prefix="medre-status-contract-") as directory:
        storage = PluginStorage(Path(directory) / "plugins")
        storage.write_state(manifest.id, {"version": manifest.version, "enabled": True})
        manager = PluginManager(storage)
        release = Path(directory) / "release"
        release.mkdir()
        manager.runtime._extract_config_default_file(wheel, release)
        assert json.loads((release / "config.default.json").read_text()) == {
            "live_status": False
        }
        path = storage.runtime_path(manifest.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = status_type(path)
        writer.initialize()
        assert parse(manager.get_runtime(manifest.id), expected_pid=os.getpid())[
            "phase"
        ] == "starting"
        writer.app = SimpleNamespace(
            started_adapter_ids=["private-adapter"],
            live_health=SimpleNamespace(
                runtime_health="degraded",
                poll_timestamp_wall=datetime.now(timezone.utc).isoformat(),
                detail="PRIVATE",
            ),
            boot_summary=None,
            route_eligibility=SimpleNamespace(registered=("private-route",)),
            startup_readiness=SimpleNamespace(
                skipped=(SimpleNamespace(route_id="private-route"),)
            ),
        )
        writer.publish("running")
        payload = manager.get_runtime(manifest.id)
        observation = parse(payload, expected_pid=os.getpid())
        assert observation["runtime_health"] == "degraded"
        assert observation["adapters_started"] == 1
        assert observation["startup_routes_blocked"] == 1
        assert "private" not in json.dumps(observation).lower()
        assert parse(payload, expected_pid=os.getpid() + 1) is None
        assert parse(payload, expected_pid=os.getpid(), now=time.time() + 31) is None
        writer.publish("stopped")
        stopped = parse(manager.get_runtime(manifest.id), expected_pid=os.getpid())
        assert stopped["phase"] == "stopped"
        assert stopped["runtime_health"] is None
    print("PASS exact source contract: wheel manifest/defaults, status writer, manager reader, HA parser, PID and staleness rejection")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core", "repeater", "ha", "wheel"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    verify(**vars(parser.parse_args()))
