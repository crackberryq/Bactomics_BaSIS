"""Installed GUI and detached-job acceptance; synthetic data only."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile


def main():
    import basis
    from basis.jobs import list_jobs, read_job, submit
    from basis.release import verify_install
    from basis.report_citation import ORCID, write_citations
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parent
    out = root / "interface-checks"
    out.mkdir(exist_ok=False)
    summary = {"schema": "basis-interface-acceptance-v1", "version": basis.__version__,
               "python": sys.version, "prefix": sys.prefix, "package": basis.__file__,
               "streamlit": importlib.metadata.version("streamlit"), "checks": {},
               "native_analyses_run": False, "biological_validation": "not_performed"}
    try:
        summary["checks"]["package_integrity"] = verify_install()
        assert summary["checks"]["package_integrity"]["status"] == "passed"
        print("Checking interface pages…", flush=True)
        workspace = out / "workspace"
        os.environ["BASIS_GUI_WORKSPACE"] = str(workspace)
        app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=60)
        for page in ("Overview", "Setup", "Samples", "Run", "Jobs", "Results & export"):
            app.session_state["page"] = page
            app.run(timeout=60)
            assert not app.exception, f"Page failed: {page}: {app.exception}"
            summary["checks"][page] = "passed"
            print("  " + page + ": passed", flush=True)
        assert not list_jobs(workspace), "Opening pages unexpectedly launched jobs"
        citations = write_citations(out / "citations")
        assert ORCID in Path(citations["cite_cff"]).read_text()
        print("Running detached synthetic demonstration; no native tools or GPU…", flush=True)
        first = submit(workspace, {"kind": "demo"})
        again = submit(workspace, {"kind": "demo"})
        assert first["id"] == again["id"]
        start = time.monotonic()
        last_print = start
        while time.monotonic() - start < 600:
            job = read_job(workspace, first["id"])
            if job["state"] not in {"queued", "running"}:
                break
            if time.monotonic() - last_print >= 15:
                print("  Job:", job["state"], "| elapsed:", round(time.monotonic()-start), "s", flush=True)
                last_print = time.monotonic()
            time.sleep(1)
        assert job["state"] == "completed", job
        # New interpreter reconnects using disk state, not Streamlit session state.
        raw = subprocess.check_output([sys.executable, "-I", "-m", "basis", "jobs", "show",
                "--workspace", str(workspace), "--job", first["id"]], text=True)
        assert json.loads(raw)["state"] == "completed"
        app.session_state["page"] = "Jobs"
        app.session_state["selected_job"] = first["id"]
        app.run(timeout=60)
        assert not app.exception, "Saved-job page failed"
        app.session_state["page"] = "Results & export"
        app.run(timeout=60)
        assert not app.exception, "Saved-results page failed"
        summary["checks"]["detached_demo"] = job
        summary["checks"]["duplicate_submission"] = "same_job"
        summary["checks"]["new_interpreter_reconnect"] = "passed"
        summary["checks"]["saved_job_and_results_pages"] = "passed"
        evidence = Path(job["outdir"]) / "results/evidence.json"
        summary["evidence_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
        summary["status"] = "passed"
    except Exception as exc:
        summary.update(status="failed", error=str(exc))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    archive = out / "interface-summary.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as z:
        z.write(out / "summary.json", "summary.json")
        for p in out.glob("workspace/jobs/*/console.log"):
            z.write(p, "jobs/"+p.parent.name+"/console.log")
        for p in (out / "citations").glob("*"):
            z.write(p, "citations/"+p.name)
    print("INTERFACE CHECK:", summary["status"], flush=True)
    print("UPLOAD:", archive, flush=True)
    try:
        windows = subprocess.check_output(["wslpath", "-w", str(archive)], text=True).strip()
        print("WINDOWS UPLOAD PATH:", windows, flush=True)
    except (OSError, subprocess.CalledProcessError):
        pass
    return 0 if summary["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
