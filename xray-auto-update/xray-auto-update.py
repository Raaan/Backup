#!/usr/bin/env python3
"""Check and optionally apply the latest stable Xray release through LA Portal."""
import argparse
import importlib
import json
import os
from pathlib import Path
import re
import sys


def version_number(tag):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag)
    if not match:
        raise ValueError(f"Unrecognized Xray version: {tag}")
    return tuple(map(int, match.groups()))


def installed_version(core):
    versions = []
    for path in core.CORES["xray"]["files"]:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"Missing or unexpected Xray binary: {path}")
        output = core.run([path, "version"])
        match = re.match(r"Xray\s+(\d+\.\d+\.\d+)(?=\s|$)", output)
        if not match:
            raise ValueError(f"Could not determine Xray version at {path}")
        versions.append(version_number(match[1]))
    if len(set(versions)) != 1:
        raise ValueError("Xray instances have different versions; review them before updating")
    return versions[0]


def was_rolled_back(core, version):
    for journal in core.STATE.glob("*/journal.json"):
        record = json.loads(journal.read_text())
        if record.get("component") != "xray":
            continue
        release = record.get("release", {})
        if record.get("status") == "rolled-back" and release.get("version") == version:
            return True
        if record.get("status") == "committed" and release.get("rollback_of"):
            original = core.STATE / release["rollback_of"] / "journal.json"
            if original.is_file() and json.loads(original.read_text()).get("release", {}).get("version") == version:
                return True
    return False


def check(core, apply):
    current = installed_version(core)
    latest = core.release("xray")["version"]
    print(f"Installed Xray: {'.'.join(map(str, current))}; upstream stable: {latest}", flush=True)
    if version_number(latest) <= current:
        print("No newer stable version")
        return
    if was_rolled_back(core, latest):
        raise ValueError(f"{latest} was previously rolled back; review before retrying manually")
    if not apply:
        print("Update available; rerun with --apply to install")
        return
    if installed_version(core) != current:
        raise ValueError("Installed version changed during the check; retry later")
    core.upgrade("xray", latest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("/root/LA_Portal"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("Run as root on the Linux host running LA Portal")
    repo = args.repo.resolve()
    if not (repo / "scripts/core_update.py").is_file():
        parser.error(f"LA Portal core updater missing in {repo}")
    if not Path("/var/lib/la-portal/install.json").is_file():
        parser.error("LA Portal installation record missing")
    sys.path.insert(0, str(repo))
    core = importlib.import_module("scripts.core_update")
    try:
        check(core, args.apply)
    except Exception as error:
        parser.exit(1, f"Xray update stopped: {error}\n")


if __name__ == "__main__":
    main()
