"""Optional browser control panel. Scientific computation stays in the CLI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import streamlit as st

from basis import __version__
from basis.config import load_config
from basis.environment_setup import recipe_names
from basis.gui_artifacts import ReportServer
from basis.jobs import list_jobs, read_job, submit, tail
from basis.report_citation import AUTHOR, ORCID, citation_text, write_citations

st.set_page_config(page_title="BaSIS · Research workspace", page_icon="🧬", layout="wide")
workspace = Path(os.environ.get("BASIS_GUI_WORKSPACE", str(Path.home() / "BaSIS-workspace"))).expanduser().resolve()
st.markdown('''<style>
.stApp {background: #f5f8fb;} h1,h2,h3 {color:#123448;}
section[data-testid="stSidebar"] {background:#102f40;}
section[data-testid="stSidebar"] * {color:#edf5fa;}
.basis-hero {padding:24px 30px;border-radius:18px;background:linear-gradient(110deg,#102f40,#185c68);color:white;margin-bottom:20px;}
.basis-hero h1 {color:white;margin:0;font-size:2.1rem;}
.basis-hero p {margin:8px 0 0;opacity:.88;}
div[data-testid="stMetric"] {background:white;border:1px solid #dfe8ee;border-radius:12px;padding:16px;}
</style>''', unsafe_allow_html=True)
st.markdown('''<div class="basis-hero"><h1>BaSIS</h1><p>Bacterial Adaptive Safety Investigation System · Research workspace</p></div>''', unsafe_allow_html=True)
page = st.sidebar.radio("Workspace", ["Overview", "Setup", "Samples", "Run", "Jobs", "Results & export"], key="page")
st.sidebar.caption(f"Version {__version__} · Local Linux / WSL")
st.sidebar.caption(str(workspace))
st.sidebar.markdown(f"[{AUTHOR} · ORCID]({ORCID})")
st.sidebar.caption("Genomic evidence supports investigation. Execution completion is separate from biological interpretation.")
new_attempt = st.sidebar.checkbox("New attempt after a previous job finished", key="new_attempt")
st.sidebar.caption("Select for retries or when input/resource files have changed. Active jobs are always reused.")


def launch(request, new=False):
    try:
        job = submit(workspace, request, new_attempt=new or new_attempt)
        st.session_state["selected_job"] = job["id"]
        st.success(f"Job {job['id']} · {job['state']}. Open Jobs to follow progress.")
        if job.get("reconnected"):
            st.info("Reconnected to a saved job. To recheck changed inputs or resources, select New attempt in the sidebar.")
    except (ValueError, OSError) as exc:
        st.error(str(exc))


def config_field(key="config"):
    return st.text_input("Configuration path on this computer", key=key,
                         placeholder="/home/jimi/bactomics/study/config.yaml")


@st.cache_resource
def report_server(root, related_roots=()):
    return ReportServer(root, related_roots)


def inspect_results(root, related_roots=()):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        st.info("Select an existing results directory.")
        return
    evidence_path = root / "evidence.json"
    if evidence_path.is_file() and evidence_path.stat().st_size > 256 * 1024 * 1024:
        st.info("This evidence file exceeds the interface preview limit. Open the saved report for its summaries.")
    elif evidence_path.is_file():
        data = json.loads(evidence_path.read_text())
        st.write("Requested scope:", data.get("run", {}).get("completeness", "not recorded"))
        rows = []
        for record in data.get("records", []) + data.get("deep_records", []):
            rows.append({"Sample": record.get("sample_id"), "Module": record.get("module"),
                         "Execution": record.get("execution_status"), "Error": record.get("error")})
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        for record in data.get("deep_records", []):
            if record.get("module") == "deepsafe":
                metrics = record.get("metrics", {})
                st.caption(f"{record.get('sample_id')} DeepSafe · selected {metrics.get('selected_candidates', 0)} · "
                           f"completed {metrics.get('completed_candidates', 0)} · deferred {metrics.get('deferred_candidates', 0)}")
    report = next((root / n for n in ("index.html", "report.html") if (root / n).is_file()), None)
    if report:
        st.link_button("Open interactive report ↗", report_server(str(root), related_roots).url(report.name), type="primary")
        st.caption("Only the selected report folder and explicitly added related folders are served. Keep these folders together when sharing.")
    for name in ("evidence.json", "run-summary.json", "findings.tsv", "recommendations.tsv", "methods.txt", "CITATION.cff", "BaSIS.bib", "BaSIS.ris"):
        path = root / name
        if path.is_file() and path.stat().st_size <= 50 * 1024 * 1024:
            st.download_button("Download " + name, path.read_bytes(), file_name=name, key="dl_" + str(path))
    st.code(str(root), language=None)


if page == "Overview":
    jobs = list_jobs(workspace)
    a, b, c = st.columns(3)
    a.metric("Saved jobs", len(jobs))
    b.metric("Active jobs", sum(j["state"] in {"queued", "running", "orphaned_active"} for j in jobs))
    c.metric("Environment recipes", len(recipe_names()))
    st.subheader("One workspace, from inputs to evidence")
    st.write("Prepare your tools, import a sample sheet, review the configuration, and run Safety with optional DeepSafe and Extend. The existing reports contain candidate details, motifs, trees and genomic context.")
    st.info("New here? Run the synthetic demonstration first. It exercises the interface without biological tools, downloads or GPU inference.")
    if st.button("Run synthetic demonstration", type="primary"):
        launch({"kind": "demo"})
    if jobs:
        st.dataframe([{k: j.get(k) for k in ("id", "kind", "state", "created_at")} for j in jobs[:10]], hide_index=True)

elif page == "Setup":
    st.subheader("Tools and resource readiness")
    st.write("The Python installation supplies BaSIS. Native tools use separate Conda environments; databases and models are configured in your study.")
    st.caption("Recipes are starting specifications. Use your captured environment locks when reproducing a validated setup.")
    envs = st.multiselect("Environments", recipe_names(), default=["core-amr", "rgi", "diamond", "context", "antismash", "mobsuite", "deepsafe"])
    a, b = st.columns(2)
    if a.button("Preview installation commands"):
        launch({"kind": "setup", "envs": envs, "create": False})
    create = b.checkbox("Download packages and create selected environments")
    if b.button("Create environments", disabled=not create):
        launch({"kind": "setup", "envs": envs, "create": True})
    st.caption("Existing environment directories are preserved and must be checked separately. This action does not download databases or reconfigure a study.")
    st.dataframe([{"Environment": n, "Prefix exists": (workspace / ".basis-envs" / ("basis-"+n)).is_dir()} for n in recipe_names()], hide_index=True)
    st.divider()
    cfg = config_field("setup_config")
    deep = st.checkbox("Check DeepSafe resources", value=True)
    extend = st.checkbox("Check Extend resources", value=True)
    if st.button("Run input and resource preflight", disabled=not cfg):
        launch({"kind": "doctor", "config": cfg, "deep": deep, "extend": extend})
    st.caption("Preflight checks declared resources; successful GPU inference and native execution require separate acceptance runs.")

elif page == "Samples":
    st.subheader("Import a batch")
    st.write("Use Excel, CSV or TSV. File paths refer to this Linux/WSL computer. Preview preserves identifiers and reports row-level issues before creating a study.")
    source = st.text_input("Sample-sheet path", placeholder="/home/jimi/bactomics/samples.xlsx")
    uploaded = st.file_uploader("Or upload a sheet using absolute input paths", type=["xlsx", "csv", "tsv"])
    if uploaded:
        body = uploaded.getvalue()
        if len(body) > 20 * 1024 * 1024:
            st.error("Sample-sheet upload exceeds 20 MiB.")
            st.stop()
        folder = workspace / "uploads" / hashlib.sha256(body).hexdigest()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("samples" + Path(uploaded.name).suffix.lower())
        if not path.exists():
            path.write_bytes(body)
        source = str(path)
        st.caption("Uploaded sheet paths must be absolute, or use exact sample-name discovery with the root below. Relative paths from the original sheet folder are not preserved.")
    root = st.text_input("Annotation discovery root (optional)", placeholder="/home/jimi/bactomics")
    sheet = st.text_input("Excel worksheet (required if workbook has several sheets)")
    mode = st.selectbox("Input mode", ["auto", "hybas", "external"])
    base = st.text_input("Base configuration with tool/resource settings (optional)")
    request = {"kind": "import", "file": source, "input_mode": mode,
               **({"root": root} if root else {}), **({"sheet": sheet} if sheet else {}),
               **({"base_config": base} if base else {})}
    a, b = st.columns(2)
    if a.button("Preview sheet", disabled=not source):
        launch({**request, "preview": True})
    if b.button("Create new study", disabled=not source):
        launch({**request, "preview": False})
    st.caption("Each import writes a new study inside its job folder. Review imported module modes, topology and exclusions, then run preflight before analysis.")

elif page == "Run":
    st.subheader("Configure an analysis")
    cfg = config_field("run_config")
    config = None
    if cfg:
        try:
            config = load_config(Path(cfg).expanduser())
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    if config is not None and st.session_state.get("budget_source") != cfg:
        for field in ("cores", "memory_mb", "gpus"):
            st.session_state["budget_" + field] = config["execution"][field]
        st.session_state["budget_source"] = cfg
    deep = st.checkbox("DeepSafe · focused follow-up", value=True)
    extend = st.checkbox("Extend · PathogenFinder2", value=True)
    a, b, c = st.columns(3)
    cores = a.number_input("CPU cores", min_value=1, value=4, key="budget_cores")
    memory = b.number_input("Memory budget (MiB)", min_value=1, value=16000, step=1000, key="budget_memory_mb")
    gpu = c.number_input("Scheduled GPUs", min_value=0, max_value=1, value=0, key="budget_gpus")
    valid = False
    if config is not None:
        try:
            st.dataframe([{"Module": n, "Enabled in config": m.get("enabled"), "Mode": m.get("mode", "native"),
                           "Executable": m.get("executable"), "Database": m.get("database")} for n, m in config["modules"].items()], hide_index=True)
            st.caption("Extend is requested by its checkbox. Imported results remain imported. CPU/RAM/GPU values are scheduling budgets; per-tool settings and model device choices come from the configuration.")
            with st.expander("DeepSafe settings and sample exclusions"):
                st.json({"deep": config["deep"], "sample_module_overrides": config.get("sample_module_overrides", {})})
            valid = True
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    request = {"config": cfg, "deep": deep, "extend": extend, "cores": int(cores), "memory_mb": int(memory), "gpus": int(gpu)}
    fresh = st.checkbox("Create another attempt if these settings have already finished")
    a, b = st.columns(2)
    if a.button("Check before running", disabled=not valid):
        launch({**request, "kind": "doctor"}, fresh)
    if b.button("Start analysis", type="primary", disabled=not valid):
        launch({**request, "kind": "run"}, fresh)
    st.caption("A private configuration, results folder and placement cache are created per job. Existing studies are preserved. Repeated clicks reconnect to the same job unless you explicitly request another finished attempt.")

elif page == "Jobs":
    st.subheader("Progress and saved jobs")
    jobs = list_jobs(workspace)
    if not jobs:
        st.info("No jobs yet. Start with the synthetic demonstration on Overview.")
    else:
        ids = [j["id"] for j in jobs]
        initial = st.session_state.get("selected_job")
        chosen = st.selectbox("Job", ids, index=ids.index(initial) if initial in ids else 0,
                             format_func=lambda value: next(f"{j['created_at'][:19]} · {j['kind']} · {j['state']} · {value[:8]}" for j in jobs if j["id"] == value))

        @st.fragment(run_every=3)
        def monitor():
            job = read_job(workspace, chosen)
            st.write("**Status:**", job["state"], " · **Exit code:**", job.get("exit_code"))
            st.caption(job.get("message", "Progress refreshes every three seconds. Reconnect after closing the browser using this workspace."))
            st.code(tail(job["log"]), language=None)
            with st.expander("Command and reproducibility record"):
                st.code(shlex.join(job["command"]), language="bash")
                st.json(job)
            directory = Path(job["directory"])
            if (directory / "config.yaml").is_file():
                st.download_button("Download run configuration", (directory / "config.yaml").read_bytes(), "config.yaml")
            if Path(job["log"]).is_file():
                st.caption("Full log: " + job["log"])
            if job.get("outdir"):
                st.code(job["outdir"], language=None)
            if job["state"] == "failed":
                st.code(tail(directory / "worker.log"), language=None)
        monitor()

elif page == "Results & export":
    st.subheader("Evidence, reports and citations")
    jobs = [j for j in list_jobs(workspace) if j.get("outdir")]
    if jobs:
        chosen = st.selectbox("Saved job", [j["id"] for j in jobs], format_func=lambda value: next(j["kind"] + " · " + j["created_at"][:19] for j in jobs if j["id"] == value))
        job = next(j for j in jobs if j["id"] == chosen)
        default = str(Path(job["outdir"]) / "results") if job["kind"] == "demo" else job["outdir"]
    else:
        default = ""
    folder = st.text_input("Results directory (saved job or an existing study)", value=default)
    related = st.text_area("Related result directories (one per line; needed for links to other study folders)")
    related_roots = tuple(p.strip() for p in related.splitlines() if p.strip())
    if folder:
        try:
            inspect_results(folder, related_roots)
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    if related_roots and st.button("Build a control panel for the related result directories"):
        launch({"kind": "panel", "results": list(related_roots)})
    st.divider()
    st.write(citation_text())
    st.markdown(f"Author: [{AUTHOR}]({ORCID})")
    citations = write_citations(workspace / "citations" / __version__)
    for key, path in citations.items():
        p = Path(path)
        st.download_button("Download " + p.name, p.read_bytes(), p.name, key="citation_" + key)
    cfg = st.text_input("Configuration to capture for reproducibility (optional)")
    if st.button("Create reproducibility audit", disabled=not cfg):
        launch({"kind": "audit", "config": cfg})
    st.caption("The audit records configured identities and gaps; it does not bundle large databases or establish a successful restore on another computer.")
