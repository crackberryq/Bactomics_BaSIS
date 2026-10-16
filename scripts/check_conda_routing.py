"""Exercise native routing with real Conda, without installing biological tools.

Run with the controller Python (3.11+) and an installed Conda 25.9.1 or
26.1.1. An offline, empty Conda prefix contains a POSIX executable fixture and
an activation hook; it intentionally has no Python. The activated subprocess
first demonstrates the unfixed behavior, then checks BaSIS's production runner
and identity probe. No persistent Conda configuration is changed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_NAME = "basis-conda-routing-fixture"
PROBE = (
    "import json,os,shutil; "
    "print(json.dumps({'prefix':os.environ.get('CONDA_PREFIX'),"
    "'executable':shutil.which('" + FIXTURE_NAME + "')}))"
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def run(command, *, env=None, timeout=60):
    result = subprocess.run(command, env=env, text=True, capture_output=True,
                            timeout=timeout)
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def check_activated(args):
    sys.path.insert(0, str(ROOT))
    from basis.execution import CommandRunner
    from basis.scheduler_persistence import capture_tool_identity, file_inventory
    from basis.util import hash_object, sha256_file

    target = Path(args.prefix).resolve()
    fixture = target / "bin" / FIXTURE_NAME
    parent = dict(os.environ)
    require(Path(parent["CONDA_PREFIX"]).resolve() == target,
            "The controller must start with the exact target already active")
    require(parent.get("CONDA_AUTO_ACTIVATE_BASE") == "true",
            "The control run must retain automatic base activation")
    require("CONDA_AUTO_ACTIVATE" not in parent,
            "Conda aliases must not both be supplied")
    require(parent.get("BASIS_ROUTING_HOOK_COUNT") == "1",
            "The real Conda activation hook must run in the parent")
    require(not (target / "bin/python").exists(), "Target must remain Pythonless")
    require(Path(sys.prefix).resolve() != target,
            "Identity bookkeeping must use the external controller Python")

    prefix = [args.conda, "run", "--no-capture-output", "--prefix", str(target)]
    baseline = json.loads(run(prefix + [sys.executable, "-I", "-c", PROBE]).stdout)
    require(Path(baseline["executable"]).resolve() == fixture,
            f"Control run did not resolve the target executable: {baseline}")
    expected_baseline = (Path(args.base_prefix).resolve()
                         if args.expected_conda_version == "26.1.1" else target)
    require(Path(baseline["prefix"]).resolve() == expected_baseline,
            f"Unexpected Conda {args.expected_conda_version} control behavior: {baseline}")

    config = {"command_prefix": prefix, "executable": FIXTURE_NAME,
              "timeout_seconds": 60}
    runner = CommandRunner(config, target.parent / "commands.log")
    identity = capture_tool_identity("vfdb", config, runner)
    require(identity["prefix"] == str(target), f"Wrong identity prefix: {identity}")
    require(identity["executables"] == {str(fixture): sha256_file(fixture)},
            f"Wrong executable identity: {identity}")
    require(identity["environment_sha256"] == hash_object(file_inventory(target)),
            "Environment fingerprint must hash the selected target")
    require(identity["reuse_supported"], "The verified target must support reuse")

    # -version catches Conda's global pre-parser regression; spaces and -- must
    # also reach the executable unchanged through the controller exec bridge.
    native_args = ["-version", "--", "literal argument with spaces"]
    observed = runner.run([FIXTURE_NAME, *native_args]).stdout.splitlines()
    require(observed == ["native fixture", str(target), "2", *native_args],
            f"Native CLI arguments, activated prefix, or hook count differ: {observed}")
    require(all(row["environment_isolation"]["applied"] for row in runner.commands),
            "Both production calls must apply child-only isolation")
    require(dict(os.environ) == parent, "Native calls changed the parent environment")
    print(json.dumps({
        "conda_version": args.expected_conda_version,
        "baseline": "prefix mismatch reproduced" if expected_baseline != target else "correct target",
        "identity_prefix": identity["prefix"],
        "pythonless_target": True,
        "activation_hook_reran": True,
        "native_arguments_preserved": True,
        "parent_environment_unchanged": True,
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conda", default=shutil.which("conda"))
    parser.add_argument("--expected-conda-version", required=True,
                        choices=("25.9.1", "26.1.1"))
    parser.add_argument("--activated", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prefix", help=argparse.SUPPRESS)
    parser.add_argument("--base-prefix", help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(os.name == "posix", "This integration check requires POSIX Conda")
    require(sys.version_info >= (3, 11), "Use a Python 3.11+ controller")
    require(args.conda, "Conda is required; supply --conda /absolute/path/to/conda")
    args.conda = str(Path(args.conda).resolve())
    if args.activated:
        check_activated(args)
        return

    original = dict(os.environ)
    child_env = dict(original)
    # These two names are aliases. Conda rejects both in the same environment.
    child_env.pop("CONDA_AUTO_ACTIVATE", None)
    child_env["CONDA_AUTO_ACTIVATE_BASE"] = "true"
    child_env["CONDA_DEFAULT_ACTIVATION_ENV"] = "base"
    child_env["CONDA_EXE"] = args.conda
    child_env.pop("BASIS_ROUTING_HOOK_COUNT", None)
    version = run([args.conda, "--version"], env=child_env).stdout.strip()
    require(version == "conda " + args.expected_conda_version,
            f"Expected Conda {args.expected_conda_version}, got {version!r}")
    base_prefix = run([args.conda, "info", "--base"], env=child_env).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="basis real conda ") as directory:
        target = Path(directory) / "native target"
        run([args.conda, "create", "--yes", "--offline", "--no-default-packages",
             "--prefix", str(target)], env=child_env, timeout=120)
        (target / "bin").mkdir(exist_ok=True)
        fixture = target / "bin" / FIXTURE_NAME
        fixture.write_text(
            '#!/bin/sh\nprintf "%s\\n" "native fixture" "$CONDA_PREFIX" '
            '"$BASIS_ROUTING_HOOK_COUNT" "$@"\n', encoding="utf-8")
        fixture.chmod(0o755)
        hook_dir = target / "etc/conda/activate.d"
        hook_dir.mkdir(parents=True)
        (hook_dir / "basis-routing-fixture.sh").write_text(
            'export BASIS_ROUTING_HOOK_COUNT=$((${BASIS_ROUTING_HOOK_COUNT:-0} + 1))\n',
            encoding="utf-8")
        # Conda performs the activation itself. The controller then inherits a
        # genuine already-active target, including its stack and hook state.
        activated = run([
            "bash", "--noprofile", "--norc", "-c",
            'set -e\neval "$("$1" shell.bash hook)"\nconda activate "$2"\n'
            'exec "$3" "$4" --activated --conda "$1" --prefix "$2" '
            '--expected-conda-version "$5" --base-prefix "$6"\n',
            "basis-conda-routing", args.conda, str(target), sys.executable,
            str(Path(__file__).resolve()), args.expected_conda_version, base_prefix,
        ], env=child_env, timeout=240)
        print(activated.stdout, end="")
        if activated.stderr:
            print(activated.stderr, end="", file=sys.stderr)
    require(dict(os.environ) == original, "Integration setup changed its parent environment")


if __name__ == "__main__":
    main()
