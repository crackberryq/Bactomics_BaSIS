#!/usr/bin/env python3
"""Exercise Snakemake's real Conda deployment/recovery, without biological tools.

This creates small Python environments in a new temporary workspace.  An absent
completion marker simulates an interrupted deployment; a deliberately failing
post-deploy check exercises a genuine deployment failure.  Neither case is a
biological validation or a claim that arbitrary runtime errors repair an env.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


def run(command: list[str], cwd: Path, logfile: Path, *, success: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=os.environ.copy())
    logfile.write_text(result.stdout, encoding="utf-8")
    if (result.returncode == 0) != success:
        raise RuntimeError(f"Unexpected exit {result.returncode}; see {logfile}\n{result.stdout[-6000:]}")
    return result


def scenario(root: Path, *, gated: bool) -> dict:
    root.mkdir(parents=True)
    envs = root / "conda"
    allow = root / "allow-deployment"
    (root / "python.yml").write_text(
        "channels:\n  - conda-forge\ndependencies:\n  - python=3.11\n", encoding="utf-8")
    post = "#!/bin/bash\nset -euo pipefail\npython -c 'import sys; assert sys.version_info[:2] == (3, 11)'\n"
    if gated:
        post += f"test -f {shlex.quote(str(allow))} || {{ echo 'Intentional post-deploy failure'; exit 23; }}\n"
    (root / "python.post-deploy.sh").write_text(post, encoding="utf-8")
    (root / "Snakefile").write_text(
        'rule all:\n    input: "result.txt"\n\n'
        'rule check:\n    output: "result.txt"\n    conda: "python.yml"\n'
        '    shell: "python -c \\\"import sys; print(sys.prefix)\\\" > {output:q}"\n', encoding="utf-8")
    command = [sys.executable, "-m", "snakemake", "--snakefile", str(root / "Snakefile"),
               "--directory", str(root), "--cores", "1", "--use-conda", "--conda-prefix", str(envs)]
    if gated:
        failed = run(command, root, root / "failed-deployment.log", success=False)
        assert "Intentional post-deploy failure" in failed.stdout, \
            "Failure did not reach the intentional post-deploy check"
        assert not (root / "result.txt").exists(), "Native job ran after failed deployment"
        assert not list(envs.glob("*.env_setup_done")), "Failed post-deploy check was accepted"
        assert not [p for p in envs.iterdir() if p.is_dir() and (p / "conda-meta").exists()], \
            "Failed environment directory was not removed"
        allow.write_text("Allow the same post-deploy check to succeed.\n", encoding="utf-8")
        run(command, root, root / "recovered-deployment.log")
    else:
        run(command, root, root / "first-deployment.log")
    markers = list(envs.glob("*.env_setup_done"))
    assert len(markers) == 1, f"Expected one complete environment; found {markers}"
    prefix = Path(str(markers[0]).removesuffix(".env_setup_done"))
    assert (prefix / "conda-meta").is_dir(), "Completion flag does not identify a Conda environment"
    output = root / "result.txt"
    assert Path(output.read_text().strip()).resolve() == prefix.resolve(), "Job used the wrong Python environment"
    if gated:
        return {"post_deploy_failure_blocks_job": True, "failed_environment_removed": True,
                "same_recipe_recovers_after_check_passes": True, "environment": str(prefix)}
    before = (output.read_bytes(), output.stat().st_mtime_ns)
    sentinel = prefix / "interrupted-deployment-sentinel"
    sentinel.write_text("Must disappear when Snakemake recreates the incomplete environment.\n", encoding="utf-8")
    markers[0].unlink()
    recovered = run(command + ["--forceall", "--conda-create-envs-only"], root, root / "incomplete-recovery.log")
    assert "Removing incomplete Conda environment" in recovered.stdout, "Snakemake did not detect incomplete deployment"
    assert not sentinel.exists(), "Incomplete environment was not recreated"
    assert markers[0].is_file(), "Recreated environment was not committed"
    assert before == (output.read_bytes(), output.stat().st_mtime_ns), "Environment preparation reran a completed native job"
    resumed = run(command, root, root / "no-op-resume.log")
    assert "Nothing to be done" in resumed.stdout, "Completed job was not reusable"
    return {"simulated_incomplete_environment_recreated": True,
            "completed_job_preserved": True, "no_op_resume": True, "environment": str(prefix)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path)
    args = parser.parse_args()
    if not shutil.which("conda"):
        parser.error("A real Conda executable must be on PATH; this test does not mock deployment.")
    root = args.workdir.resolve() if args.workdir else Path(tempfile.mkdtemp(prefix="basis-env-recovery-"))
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error(f"Use an empty work directory: {root}")
    summary = {"schema": "basis-snakemake-environment-recovery-v1",
               "snakemake": subprocess.check_output([sys.executable, "-m", "snakemake", "--version"], text=True).strip(),
               "conda": subprocess.check_output(["conda", "--version"], text=True).strip(),
               "native_biological_validation": False, "status": "running"}
    try:
        summary["incomplete_deployment"] = scenario(root / "incomplete", gated=False)
        summary["post_deploy_failure"] = scenario(root / "post-deploy", gated=True)
        summary["status"] = "passed"
        return 0
    except Exception as exc:
        summary.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
        print("Test artifacts:", root, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
