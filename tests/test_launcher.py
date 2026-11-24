"""Exercise the real launcher boundary without opening a radio or network."""

import json
import os
from pathlib import Path

import pytest

from medre_openhop_plugin import launcher


@pytest.fixture
def data(tmp_path: Path) -> Path:
    home = tmp_path / "medre"
    home.mkdir(mode=0o700)
    config = home / "config.yaml"
    config.write_text("private: never print me\n")
    config.chmod(0o600)
    return tmp_path


def test_launch_uses_only_plugin_environment_and_fixed_config(data):
    args, env = launcher._prepare(
        data, {"MEDRE_CONFIG": "wrong", "MEDRE_HOME": "wrong", "MEDRE_ADAPTER__X": "SECRET", "PATH": "/bin"}
    )
    assert args[1:] == ["-I", "-m", "medre", "run", "--config", str(data / "medre/config.yaml")]
    assert env == {"PATH": "/bin", "MEDRE_HOME": str(data / "medre")}


def test_optional_status_retires_stale_owned_observation(data):
    (data / "config.json").write_text('{"live_status": true}')
    status = data / "runtime.json"
    status.write_text(json.dumps({"schema": launcher.STATUS_SCHEMA, "phase": "running"}))
    args, _ = launcher._prepare(data, {})
    assert args[-2:] == ["--status-file", str(status)]
    assert not status.exists()


@pytest.mark.parametrize("settings", ["[]", "{", '{"live_status": 1}', '{"config_file":"/etc/passwd"}'])
def test_invalid_settings_fail_closed(data, settings):
    (data / "config.json").write_text(settings)
    with pytest.raises(launcher.LaunchError):
        launcher._prepare(data, {})


def test_no_configuration_autogeneration(tmp_path):
    with pytest.raises(launcher.LaunchError, match="Provision"):
        launcher._prepare(tmp_path, {})
    assert not (tmp_path / "medre/config.yaml").exists()


def test_refuse_world_readable_config_and_symlink(data):
    config = data / "medre/config.yaml"
    config.chmod(0o644)
    with pytest.raises(launcher.LaunchError, match="chmod 600"):
        launcher._prepare(data, {})
    config.unlink()
    config.symlink_to("/etc/passwd")
    with pytest.raises(launcher.LaunchError):
        launcher._prepare(data, {})


def test_unrelated_status_is_not_removed(data):
    path = data / "runtime.json"
    path.write_text('{"not":"ours"}')
    with pytest.raises(launcher.LaunchError):
        launcher._prepare(data, {})
    assert path.read_text() == '{"not":"ours"}'


def test_duplicate_launcher_is_rejected_and_lock_survives_exec(data):
    fd = launcher._lock(data)
    try:
        assert os.get_inheritable(fd)
        with pytest.raises(launcher.LaunchError, match="Another"):
            launcher._lock(data)
    finally:
        os.close(fd)
    second = launcher._lock(data)
    os.close(second)


def test_requires_manager_environment(data):
    with pytest.raises(launcher.LaunchError):
        launcher._plugin_data({})
    assert launcher._plugin_data({"OPENHOP_PLUGIN_ID": launcher.PLUGIN_ID, "OPENHOP_PLUGIN_DATA": str(data)}) == data


def test_main_execs_without_child_process(data, monkeypatch):
    monkeypatch.setenv("OPENHOP_PLUGIN_ID", launcher.PLUGIN_ID)
    monkeypatch.setenv("OPENHOP_PLUGIN_DATA", str(data))
    calls = []

    class ExecCalled(Exception):
        pass

    def fake_exec(exe, args, env):
        calls.append((exe, args, env))
        raise ExecCalled

    old_umask = os.umask(0o077)
    try:
        monkeypatch.setattr(os, "execve", fake_exec)
        with pytest.raises(ExecCalled):
            launcher.main()
    finally:
        os.umask(old_umask)
    assert len(calls) == 1
    assert calls[0][1][1:4] == ["-I", "-m", "medre"]
    # Failed exec must release the descriptor, without unlinking the lock file.
    os.close(launcher._lock(data))
