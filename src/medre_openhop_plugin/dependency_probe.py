"""Read-only dependency inspection for the plugin's isolated interpreter.

The launcher runs this module under the same ``python -I`` interpreter that
will run MEDRE and reads its JSON verdict. Parsing uses pip's bundled PEP 508
implementation because the third-party ``packaging`` distribution is not a
plugin dependency and can be absent from exactly the incomplete environments
this probe inspects. No adapter code or transport module is imported.

The verdict is fail-closed: unavailable inspection, missing or duplicate
required roots, a missing adapter entry point, an incompatible installed
dependency, an incomplete installed-file record, unparseable requirement
metadata and a direct-reference requirement are all reported as blockers
rather than as repairable gaps. Direct references fail closed because a
matching name and version cannot prove a PEP 610 origin.
"""

from __future__ import annotations

import importlib.metadata as metadata
import json
import re
import sys
from pathlib import Path
from typing import Any

# The managed plugin venv must contain the launcher, the core and both MEDRE
# adapters; every other requirement is reachable from these roots.
EXPECTED = {
    "medre-openhop-plugin",
    "medre",
    "medre-adapter-meshcore",
    "medre-adapter-meshtastic",
}
ADAPTER_ENTRY_GROUP = "medre.adapters"


def canonical(name: str) -> str:
    """Return the PEP 503 normalized form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _adapter_roots(installed: dict[str, metadata.Distribution]) -> set[str]:
    """Collect every installed distribution that registers a MEDRE adapter."""
    return {
        key
        for key, dist in installed.items()
        if any(ep.group == ADAPTER_ENTRY_GROUP for ep in dist.entry_points)
    }


def _collect_installed(
    root: Path, extra_site: str
) -> tuple[dict[str, metadata.Distribution], set[str]]:
    """Map one environment's installed distributions by normalized name.

    Distributions outside the venv root are skipped: a venv created with
    system site-packages enabled can borrow foreign copies through the base
    interpreter, and those copies are invisible to MEDRE's isolated
    interpreter, which is exactly the leak this probe guards against. A
    later duplicate overwrites the earlier record and is reported as a
    blocker, so the verdict never depends on which copy wins.
    """
    if extra_site:
        # Synthetic metadata fixtures deliberately use an explicit test-only
        # path here; production inspection always walks sys.prefix.
        distributions = metadata.distributions(path=[extra_site])
    else:
        distributions = metadata.distributions()
    installed: dict[str, metadata.Distribution] = {}
    blocked: set[str] = set()
    for dist in distributions:
        if not Path(dist.locate_file("")).resolve().is_relative_to(root):
            continue
        name = dist.metadata["Name"]
        if not name:
            raise ValueError("Missing distribution name")
        key = canonical(name)
        if key in installed:
            blocked.add(f"duplicate distribution {key}")
        installed[key] = dist
    return installed, blocked


def _verify_installed_files(
    key: str, dist: metadata.Distribution, blocked: set[str]
) -> None:
    """Check the distribution's RECORD presence and recorded file sizes.

    RECORD existence and recorded sizes catch a partially installed
    distribution, which an interrupted installation can leave behind. This
    is a presence check, not a content attestation: full wheel
    qualification happens before installation, and the repair marker
    fences an interrupted repair separately.
    """
    if dist.files is None:
        blocked.add(f"missing installed-file record for {key}")
        return
    for file in dist.files:
        if file.hash is None:
            # RECORD rows without a digest carry no verifiable expectation.
            continue
        path = Path(dist.locate_file(file))
        if not path.is_file() or (
            file.size is not None and path.stat().st_size != file.size
        ):
            blocked.add(f"incomplete installed files for {key}")
            return


def _walk_closure(
    installed: dict[str, metadata.Distribution],
    roots: set[str],
    blocked: set[str],
) -> tuple[set[str], list[dict[str, object]]]:
    """Return the absent index requirements reachable from the given roots.

    The walk follows required distributions with the extras each requirement
    asks for, so a transitive dependency gated on an extra is enforced only
    when that extra is requested. Requirements without an installed,
    compatible distribution become the repair's missing list; incompatible
    installations and requirement metadata the parser rejects become
    blockers, because the repair never replaces an installed distribution
    and never guesses at unreadable metadata.
    """
    from pip._vendor.packaging.requirements import InvalidRequirement, Requirement

    missing: set[str] = set()
    problems: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    verified: set[str] = set()
    pending = [(key, "") for key in sorted(roots)]
    while pending:
        key, extra = pending.pop()
        if (key, extra) in seen:
            continue
        seen.add((key, extra))
        dist = installed[key]
        if key not in verified:
            # RECORD content cannot change between visits, so a distribution
            # reached through several extras is verified once.
            verified.add(key)
            _verify_installed_files(key, dist, blocked)
        for raw in dist.requires or []:
            try:
                req = Requirement(raw)
            except InvalidRequirement:
                # Third-party metadata the parser rejects is that
                # distribution's defect: name it instead of failing the
                # whole inspection with a generic error.
                blocked.add(f"invalid requirement metadata in {key}")
                continue
            if req.marker is not None and not req.marker.evaluate({"extra": extra}):
                continue
            target_key = canonical(req.name)
            if req.url is not None:
                # A name/version match or a URL comparison cannot prove a
                # PEP 610 origin, so the repair must not resolve or reinstall
                # a direct reference. The URL stays out of the verdict: it
                # can carry credentials.
                blocked.add(
                    f"direct-reference requirement {key} -> {target_key}"
                    " requires manual qualification"
                )
                continue
            target = installed.get(target_key)
            if target is None:
                requirement = target_key + str(req.specifier)
                missing.add(requirement)
                problems.append({"dist": key, "unmet": [requirement]})
                continue
            if not req.specifier.contains(target.version, prereleases=True):
                blocked.add(
                    f"installed {target_key} does not satisfy {key}: {req.specifier}"
                )
                continue
            pending.append((target_key, ""))
            pending.extend((target_key, canonical(item)) for item in req.extras)
    return missing, problems


def inspect(extra_site: str = "") -> dict[str, Any]:
    """Inspect one interpreter environment and return the closure verdict.

    ``extra_site`` points the probe at an alternative site-packages
    directory; without it the probe inspects its own isolated interpreter.
    The verdict names the requirements a missing-only repair may install,
    the blockers that require manual repair, and, diagnostically, which
    distribution needs each missing requirement.
    """
    root = Path(extra_site or sys.prefix).resolve()
    installed, blocked = _collect_installed(root, extra_site)
    blocked.update(
        f"missing required distribution {key}" for key in EXPECTED - installed.keys()
    )
    roots = _adapter_roots(installed)
    for key in sorted(EXPECTED - {"medre-openhop-plugin", "medre"}):
        if key not in roots:
            blocked.add(f"missing adapter entry point for {key}")
    missing, problems = _walk_closure(
        installed, roots | (EXPECTED & installed.keys()), blocked
    )
    return {
        "missing": sorted(missing),
        "blocked": sorted(blocked),
        "problems": problems,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(inspect(sys.argv[1])))
    except Exception:
        # A parser or metadata failure is never an empty or healthy verdict.
        raise SystemExit(1) from None
