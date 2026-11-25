"""Install a built wheel without dependencies; prove launcher process ownership.

The deliberately minimal MEDRE stand-in records arguments and handles SIGTERM.
This is a packaging and signal test, not a MEDRE runtime or transport test.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import venv
import zipfile
from pathlib import Path

STAND_IN = """import json, os, signal, sys
from pathlib import Path
if "--help" in sys.argv[1:]:
    # The launcher probes the installed CLI for --status-file before exec.
    print("usage: medre run [--config CONFIG] [--status-file PATH]")
    raise SystemExit(0)
home = Path(os.environ["MEDRE_HOME"])
def stop(_signum, _frame):
    (home / "stopped").write_text("yes")
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
(home / "ready.json").write_text(json.dumps({"pid": os.getpid(), "args": sys.argv[1:], "foreign_config": os.environ.get("MEDRE_CONFIG")}))
while True:
    signal.pause()
"""


def verify(wheel: Path) -> None:
    wheel = wheel.resolve(strict=True)
    with zipfile.ZipFile(wheel) as archive:
        manifest_paths = [
            p
            for p in archive.namelist()
            if p.endswith("/share/openhop/plugins/medre.bridge/openhop-plugin.json")
        ]
        assert len(manifest_paths) == 1
        manifest = json.loads(archive.read(manifest_paths[0]))
        assert manifest["id"] == "medre.bridge"
        assert manifest["runtime"]["entrypoint"] == "medre-openhop"
        assert manifest["config"]["defaults"] == {"live_status": False}
    with tempfile.TemporaryDirectory(prefix="medre-wheel-") as directory:
        root = Path(directory)
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        python = root / "venv/bin/python"
        subprocess.run(
            [
                str(python),
                "-I",
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--no-index",
                str(wheel),
            ],
            check=True,
            timeout=60,
        )
        site = Path(
            subprocess.check_output(
                [
                    str(python),
                    "-I",
                    "-c",
                    "import sysconfig; print(sysconfig.get_path('purelib'))",
                ],
                text=True,
                timeout=10,
            ).strip()
        )
        fake = site / "medre"
        fake.mkdir()
        (fake / "__init__.py").write_text("")
        (fake / "__main__.py").write_text(STAND_IN)
        # The stand-in is intentionally not a real installation. Supply
        # metadata for the three expected roots, with actual RECORD entries,
        # so the same fail-closed dependency gate runs in the wheel smoke.
        for name, version in (
            ("medre", "0.2.3"),
            ("medre-adapter-meshcore", "0.2.0"),
            ("medre-adapter-meshtastic", "0.2.1"),
        ):
            info = site / f"{name.replace('-', '_')}-{version}.dist-info"
            info.mkdir()
            (info / "METADATA").write_text(
                f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            )
            if name.startswith("medre-adapter-"):
                (info / "entry_points.txt").write_text(
                    "[medre.adapters]\nfixture = fixture:spec\n"
                )
            (info / "RECORD").write_text(f"{info.relative_to(site)}/METADATA,,\n")
        data = root / "plugin-data"
        home = data / "medre"
        home.mkdir(parents=True)
        # The launcher refuses a group- or world-accessible MEDRE home.
        home.chmod(0o700)
        config = home / "config.yaml"
        config.write_text("fixture: true\n")
        config.chmod(0o600)
        (data / "config.json").write_text('{"live_status": true}')
        env = dict(
            os.environ,
            OPENHOP_PLUGIN_ID="medre.bridge",
            OPENHOP_PLUGIN_DATA=str(data),
            MEDRE_CONFIG="wrong",
            MEDRE_HOME="wrong",
        )
        command = [str(root / "venv/bin/medre-openhop")]
        process = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        try:
            marker = home / "ready.json"
            deadline = time.monotonic() + 10
            while not marker.is_file():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("installed launcher did not reach stand-in")
                time.sleep(0.02)
            # The marker is a tiny JSON record; tolerate its creation before
            # the writer completes, without a fixed startup sleep.
            while True:
                try:
                    ready = json.loads(marker.read_text())
                    break
                except json.JSONDecodeError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.02)
            assert ready["pid"] == process.pid
            assert ready["foreign_config"] is None
            assert ready["args"] == [
                "run",
                "--config",
                str(config),
                "--status-file",
                str(data / "runtime.json"),
            ]
            duplicate = subprocess.run(
                command, env=env, capture_output=True, text=True, timeout=10
            )
            assert (
                duplicate.returncode == 2
                and "launch holds the plugin lock" in duplicate.stderr
            )
            process.terminate()
            assert process.wait(timeout=10) == 0
            assert (home / "stopped").read_text() == "yes"
            print(
                "PASS installed wheel: fixed paths, isolated interpreter, same PID, exclusive lock, SIGTERM exit"
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            process.communicate(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    verify(parser.parse_args().wheel)
