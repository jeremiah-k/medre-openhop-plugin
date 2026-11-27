"""Exercise the isolated dependency probe and the bounded repair policy."""

import base64
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from medre_openhop_plugin import launcher


def make_dist(site, name, version="1.0", requires=(), adapter=False, direct_url=None):
    """Create one synthetic distribution, with a real RECORD for its files."""
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    metadata = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    metadata.extend(f"Requires-Dist: {req}" for req in requires)
    (info / "METADATA").write_text("\n".join(metadata) + "\n")
    if adapter:
        (info / "entry_points.txt").write_text(
            "[medre.adapters]\nfixture = fixture:spec\n"
        )
    if direct_url:
        (info / "direct_url.json").write_text(json.dumps({"url": direct_url}))
    rows = []
    for path in info.iterdir():
        data = path.read_bytes()
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        )
        rows.append(f"{path.relative_to(site)},sha256={digest},{len(data)}")
    (info / "RECORD").write_text("\n".join(rows) + "\n")
    return info


@pytest.fixture
def site(tmp_path):
    """A healthy venv layout: launcher, core and two adapters, no SDKs."""
    site = tmp_path / "site-packages"
    make_dist(
        site,
        "medre-openhop-plugin",
        requires=["medre", "medre-adapter-meshcore", "medre-adapter-meshtastic"],
    )
    make_dist(site, "medre")
    make_dist(site, "medre-adapter-meshcore", adapter=True)
    make_dist(site, "medre-adapter-meshtastic", adapter=True)
    return site


def test_complete_closure_ignores_unrelated_and_extra_gated_requirements(site):
    make_dist(site, "unrelated", requires=["not-installed"])
    make_dist(
        site,
        "fixturemesh",
        requires=["fixturedep>=1", "devtool; extra == 'dev'"],
        adapter=True,
    )
    make_dist(site, "fixturedep")
    assert launcher._probe_adapter_closure(str(site))["missing"] == []
    assert launcher._probe_adapter_closure(str(site))["blocked"] == []


def test_requested_extras_are_enforced_through_a_second_hop(site):
    make_dist(site, "fixturemesh", requires=["fixture_dep[crypto]>=1"], adapter=True)
    make_dist(site, "fixture-dep", requires=["deep>=1; extra == 'crypto'"])
    assert launcher._probe_adapter_closure(str(site))["missing"] == ["deep>=1"]


def test_missing_required_roots_fail_closed(tmp_path):
    assert launcher._probe_adapter_closure(str(tmp_path))["blocked"]


def test_incompatible_local_dependency_blocks_repair(site):
    make_dist(site, "fixturemesh", requires=["fixturedep>=2", "missing"], adapter=True)
    make_dist(site, "fixturedep", direct_url="file:///private/test.whl")
    probe = launcher._probe_adapter_closure(str(site))
    assert probe["missing"] == ["missing"]
    assert "fixturedep" in probe["blocked"][0]


def test_direct_reference_is_not_satisfied_by_name(site):
    make_dist(
        site,
        "fixturemesh",
        requires=["fixturedep @ https://example.invalid/wheel.whl"],
        adapter=True,
    )
    make_dist(site, "fixturedep")
    probe = launcher._probe_adapter_closure(str(site))
    assert probe["blocked"] and "https:" not in str(probe)


def test_unparseable_requirement_names_the_distribution_for_manual_repair(
    site, tmp_path, monkeypatch
):
    make_dist(site, "fixturemesh", requires=["fixturedep ("], adapter=True)
    probe = launcher._probe_adapter_closure(str(site))
    assert probe["blocked"] == ["invalid requirement metadata in fixturemesh"]
    assert probe["missing"] == []
    monkeypatch.setattr(launcher, "_repair_marker", lambda: tmp_path / "marker")
    monkeypatch.setattr(launcher, "_probe_adapter_closure", lambda **_: probe)
    monkeypatch.setattr(
        launcher.subprocess, "run", lambda *a, **k: pytest.fail("pip ran")
    )
    with pytest.raises(launcher.LaunchError, match="manual repair"):
        launcher._ensure_adapter_closure({})


def test_missing_recorded_file_blocks(site):
    info = make_dist(site, "fixturemesh", adapter=True)
    (info / "entry_points.txt").write_text(
        "[medre.adapters]\nchanged_fixture = fixture:spec\n"
    )
    assert launcher._probe_adapter_closure(str(site))["blocked"]


def test_failed_probe_is_not_success(monkeypatch):
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"{}"),
    )
    with pytest.raises(launcher.LaunchError, match="inspect"):
        launcher._probe_adapter_closure()


def test_only_missing_packages_are_installed_without_dependencies(
    site, tmp_path, monkeypatch
):
    make_dist(
        site,
        "fixturemesh",
        requires=["fixturedep>=1"],
        adapter=True,
        direct_url="file:///private/local.whl",
    )
    broken = launcher._probe_adapter_closure(str(site))
    marker = tmp_path / "marker"
    monkeypatch.setattr(launcher, "_repair_marker", lambda: marker)
    probes = iter([broken, {"missing": [], "blocked": [], "problems": []}])
    monkeypatch.setattr(launcher, "_probe_adapter_closure", lambda **_: next(probes))
    calls = []

    def run(argv, **kwargs):
        assert marker.exists()
        calls.append((argv, kwargs["env"]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    launcher._ensure_adapter_closure(
        {
            "PYTHONPATH": "bad",
            "PYTHONHOME": "bad",
            "MEDRE_CONFIG": "bad",
            "PIP_TARGET": "bad",
            "PIP_FORCE_REINSTALL": "1",
            "PIP_INDEX_URL": "https://index.example/simple",
            "PIP_CONFIG_FILE": "/ops/pip.ini",
            "PATH": "/bin",
        }
    )
    argv, env = calls[0]
    assert argv[-2:] == ["--", "fixturedep>=1"]
    assert "--no-deps" in argv and "--only-binary=:all:" in argv
    assert "--force-reinstall" not in argv
    assert not any(key.startswith(("PYTHON", "MEDRE_")) for key in env)
    assert "PIP_TARGET" not in env and "PIP_FORCE_REINSTALL" not in env
    assert env["PATH"] == "/bin"
    assert env["PIP_INDEX_URL"] == "https://index.example/simple"
    assert env["PIP_CONFIG_FILE"] == os.devnull
    assert not marker.exists()


def test_marker_with_complete_closure_clears_and_launches(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    marker.touch()
    monkeypatch.setattr(launcher, "_repair_marker", lambda: marker)
    monkeypatch.setattr(
        launcher,
        "_probe_adapter_closure",
        lambda **_: {"missing": [], "blocked": [], "problems": []},
    )
    monkeypatch.setattr(
        launcher.subprocess, "run", lambda *a, **k: pytest.fail("pip ran")
    )
    launcher._ensure_adapter_closure({})
    assert not marker.exists()


def test_incomplete_closure_after_marker_receives_one_bounded_repair(
    tmp_path, monkeypatch
):
    marker = tmp_path / "marker"
    marker.touch()
    monkeypatch.setattr(launcher, "_repair_marker", lambda: marker)
    probes = iter(
        [
            {"missing": ["fixturedep"], "blocked": [], "problems": []},
            {"missing": [], "blocked": [], "problems": []},
        ]
    )
    monkeypatch.setattr(launcher, "_probe_adapter_closure", lambda **_: next(probes))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    launcher._ensure_adapter_closure({})
    assert len(calls) == 1
    assert calls[0][-1] == "fixturedep"
    assert not marker.exists()


def test_marker_with_inconsistent_install_fails_closed_without_pip(
    tmp_path, monkeypatch
):
    marker = tmp_path / "marker"
    marker.touch()
    monkeypatch.setattr(launcher, "_repair_marker", lambda: marker)
    monkeypatch.setattr(
        launcher,
        "_probe_adapter_closure",
        lambda **_: {
            "missing": ["other"],
            "blocked": ["incomplete installed files for medre"],
            "problems": [],
        },
    )
    monkeypatch.setattr(
        launcher.subprocess, "run", lambda *a, **k: pytest.fail("pip ran")
    )
    with pytest.raises(launcher.LaunchError, match="manual repair"):
        launcher._ensure_adapter_closure({})
    assert marker.exists()


def test_failing_repair_leaves_marker_for_the_next_start(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    monkeypatch.setattr(launcher, "_repair_marker", lambda: marker)
    monkeypatch.setattr(
        launcher,
        "_probe_adapter_closure",
        lambda **_: {"missing": ["fixturedep"], "blocked": [], "problems": []},
    )
    monkeypatch.setattr(
        launcher.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1)
    )
    with pytest.raises(launcher.LaunchError, match="failed"):
        launcher._ensure_adapter_closure({})
    assert marker.exists()


def test_blocked_closure_never_invokes_pip(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_repair_marker", lambda: tmp_path / "marker")
    monkeypatch.setattr(
        launcher,
        "_probe_adapter_closure",
        lambda **_: {
            "missing": ["other"],
            "blocked": ["incompatible local"],
            "problems": [],
        },
    )
    monkeypatch.setattr(
        launcher.subprocess, "run", lambda *a, **k: pytest.fail("pip ran")
    )
    with pytest.raises(launcher.LaunchError, match="manual repair"):
        launcher._ensure_adapter_closure({})


def test_repair_verification_respects_remaining_budget(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    monkeypatch.setattr(launcher, "_repair_marker", lambda: marker)
    clock = iter([0.0, 0.0, 299.0, 299.0])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock))
    timeouts = []

    def probe(*, timeout):
        timeouts.append(timeout)
        return {"missing": [] if marker.exists() else ["fixturedep"], "blocked": []}

    monkeypatch.setattr(launcher, "_probe_adapter_closure", probe)
    monkeypatch.setattr(launcher, "_install_requirements", lambda *_: None)
    launcher._ensure_adapter_closure({})
    assert timeouts == [30, 1]
