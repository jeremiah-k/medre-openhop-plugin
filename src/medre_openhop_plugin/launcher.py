"""Launch MEDRE in the manager-owned environment without a child supervisor."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

PLUGIN_ID = "medre.bridge"
STATUS_SCHEMA = "medre.runtime-status.v1"
_MAX_JSON = 16_384


class LaunchError(ValueError):
    """Unsafe or incomplete plugin configuration."""


def _json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise LaunchError("Expected a regular plugin-owned JSON file")
    with path.open("rb") as stream:
        content = stream.read(_MAX_JSON + 1)
    if len(content) > _MAX_JSON:
        raise LaunchError("Plugin JSON file is too large")
    try:
        value = json.loads(content)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise LaunchError("Invalid plugin JSON file") from exc
    if not isinstance(value, dict):
        raise LaunchError("Plugin JSON must be an object")
    return value


def _plugin_data(environ: Mapping[str, str]) -> Path:
    if environ.get("OPENHOP_PLUGIN_ID") != PLUGIN_ID:
        raise LaunchError("Start this plugin through the openHop plugin manager")
    raw = environ.get("OPENHOP_PLUGIN_DATA")
    if not raw:
        raise LaunchError("OPENHOP_PLUGIN_DATA is required")
    data = Path(raw)
    if not data.is_absolute() or data.is_symlink() or not data.is_dir():
        raise LaunchError("Plugin data must be an existing absolute directory")
    return data.resolve(strict=True)


def _settings(data: Path) -> bool:
    path = data / "config.json"
    settings = _json_object(path) if path.exists() or path.is_symlink() else {}
    if settings.keys() - {"live_status"}:
        raise LaunchError("Unsupported plugin settings; only live_status is accepted")
    live_status = settings.get("live_status", False)
    if type(live_status) is not bool:
        raise LaunchError("live_status must be a JSON boolean")
    return live_status


def _lock(data: Path) -> int:
    # The file remains in place across runs; replacing it would split the lock.
    fd = os.open(
        data / ".medre-launch.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise LaunchError("Plugin launch lock is not a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LaunchError("Another managed MEDRE launch holds the plugin lock") from exc
        os.set_inheritable(fd, True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _prepare(data: Path, environ: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
    status = data / "runtime.json"
    if status.exists() or status.is_symlink():
        if _json_object(status).get("schema") != STATUS_SCHEMA:
            raise LaunchError("Existing runtime.json is not a MEDRE status document")
        # Retire the previous run even if new configuration is invalid.
        status.unlink()
    live_status = _settings(data)
    home = data / "medre"
    if home.is_symlink():
        raise LaunchError("MEDRE home must not be a symlink")
    home.mkdir(mode=0o700, exist_ok=True)
    if not home.is_dir():
        raise LaunchError("MEDRE home must be a directory")
    if home.stat().st_mode & 0o077:
        raise LaunchError("MEDRE home must be private (use chmod 700)")
    config = home / "config.yaml"
    if config.is_symlink() or not config.is_file():
        raise LaunchError("Provision the private bridge YAML at data/medre/config.yaml first")
    if config.stat().st_mode & 0o077:
        raise LaunchError("Bridge YAML must not be group/world accessible (use chmod 600)")
    env = {key: value for key, value in environ.items() if not key.startswith("MEDRE_")}
    env["MEDRE_HOME"] = str(home)
    # -I excludes inherited PYTHONPATH/user site (notably the HA wrapper's
    # repeater site-packages), while keeping this plugin's own venv available.
    args = [sys.executable, "-I", "-m", "medre", "run", "--config", str(config)]
    if live_status:
        args.extend(["--status-file", str(status)])
    return args, env


def _check_status_support(args: list[str], env: dict[str, str]) -> None:
    """Optional monitoring requires an installed core that exposes its CLI flag."""
    if "--status-file" not in args:
        return
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-m", "medre", "run", "--help"],
            env=env, capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LaunchError("Could not verify installed MEDRE status support") from exc
    if result.returncode or b"--status-file" not in result.stdout:
        raise LaunchError("Installed MEDRE lacks runtime status support; disable live_status or install a qualified core")


def main() -> int:
    """Replace the launcher with MEDRE, preserving PID, process group and signals."""
    os.umask(0o077)
    fd: int | None = None
    try:
        data = _plugin_data(os.environ)
        fd = _lock(data)
        args, env = _prepare(data, os.environ)
        _check_status_support(args, env)
        os.execve(sys.executable, args, env)
    except LaunchError as exc:
        print(f"MEDRE plugin configuration: {exc}", file=sys.stderr)
    except OSError:
        # Do not print paths, file contents or inherited environment values.
        print(
            "MEDRE plugin could not prepare its private data or launch MEDRE",
            file=sys.stderr,
        )
    finally:
        if fd is not None:
            os.close(fd)
    return 2
