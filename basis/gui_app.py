"""Optional browser control panel. Scientific computation stays in the CLI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex

import streamlit as st
import yaml

from basis import __version__
from basis.config import load_config
from basis.environment_setup import recipe_names
from basis.gui_artifacts import ReportServer
from basis.jobs import list_jobs, read_job, scheduler_progress, submit, tail
from basis.report_citation import AUTHOR, ORCID, citation_text, write_citations

st.set_page_config(page_title="BaSIS · Research workspace", page_icon="🧬", layout="wide")
workspace = Path(os.environ.get("BASIS_GUI_WORKSPACE", str(Path.home() / "BaSIS-workspace"))).expanduser().resolve()
st.markdown('''<style>
.stApp {background:#f1f5f8;color:#243d50;}
h1,h2,h3 {color:#101f33;letter-spacing:-.025em;}
section[data-testid="stSidebar"] {background:#101f33;}
section[data-testid="stSidebar"] * {color:#e3edf4;}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {opacity:.8;}
.block-container {max-width:1420px;padding-top:2rem;}
.basis-hero {padding:30px 34px;border-radius:18px;background:radial-gradient(ellipse at 100% 0,#246276,transparent 55%),linear-gradient(115deg,#142d43,#173e52);color:white;margin-bottom:22px;}
.basis-hero h1 {color:white;margin:7px 0 10px;font-size:2.3rem;line-height:1.2;}
.basis-hero p {margin:0;max-width:790px;color:#dae9ef;line-height:1.6;}
.basis-eyebrow {font-size:.7rem;font-weight:750;letter-spacing:.16em;text-transform:uppercase;color:#a9ddd6;}
.basis-step {font-size:.72rem;font-weight:750;letter-spacing:.12em;text-transform:uppercase;color:#087c78;margin-bottom:9px;}
div[data-testid="stMetric"] {background:white;border:1px solid #dce6ec;border-top:3px solid #087c78;border-radius:12px;padding:18px;}
div[data-testid="stVerticalBlockBorderWrapper"]>div {border-radius:14px;}
button[kind="primary"] {background:#087c78;border-color:#087c78;}
button[kind="primary"]:hover {background:#066662;border-color:#066662;}
@media(max-width:760px){.basis-hero{padding:23px;}.basis-hero h1{font-size:1.8rem;}}
</style>''', unsafe_allow_html=True)
PAGE_LABELS = {"Overview": "Start here", "Setup": "1 · Tool environments", "Resources": "Databases & models", "Samples": "2 · Add samples",
               "Run": "3 · Readiness & run", "Jobs": "Progress & jobs", "Results & export": "4 · Explore results"}
page = st.sidebar.radio("Your workspace", list(PAGE_LABELS), format_func=PAGE_LABELS.get, key="page")
st.sidebar.caption(f"BaSIS {__version__} · Local Linux / WSL")
with st.sidebar.expander("Workspace details"):
    st.caption(str(workspace))
    new_attempt = st.checkbox("New attempt after a previous job finished", key="new_attempt")
    st.caption("Select for retries or changed inputs/resources. Active jobs are always reused.")
st.sidebar.markdown(f"[{AUTHOR} · ORCID]({ORCID})")
st.sidebar.caption("Analysis completion and biological interpretation are reported separately.")
HEADINGS = {
    "Overview": ("From genome to evidence", "Prepare your tools, add samples and follow a guided analysis. Explore the findings in your interactive BaSIS report."),
    "Setup": ("Prepare your analysis tools", "Plan, install and verify tool environments with Snakemake. Then check the databases and models required by your study."),
    "Resources": ("Prepare your study resources", "Choose explicitly configured resources, review the plan and let Snakemake coordinate supported downloads and index building."),
    "Samples": ("Bring your samples together", "Import a sample sheet from HybAs or another annotation workflow. Preview every sample before creating your study."),
    "Run": ("Choose your analysis", "Select the evidence layers you need, check readiness and start a reproducible workflow."),
    "Jobs": ("Follow your progress", "See the current activity and live workflow output. Jobs keep running when you leave this page."),
    "Results & export": ("Explore the evidence", "Open your interactive report for genomic context, candidate details, motifs, trees and downloadable evidence."),
}
title, description = HEADINGS[page]
st.markdown(f'<div class="basis-hero"><div class="basis-eyebrow">BaSIS · Research workspace</div><h1>{title}</h1><p>{description}</p></div>', unsafe_allow_html=True)


def navigate(destination):
    st.session_state["page"] = destination


def next_step(label, destination, key):
    st.button(label, on_click=navigate, args=(destination,), key=key)


def environment_inventory(prefix_root):
    """Display recorded verification, never infer readiness from an existing folder."""
    verified = {}
    summaries = list((workspace / ".basis-setup" / "attempts").glob("*/summary.json"))
    summaries += list((workspace / "jobs").glob("*/setup/summary.json"))
    for path in sorted(summaries, key=lambda x: x.stat().st_mtime_ns):
        try:
            data = json.loads(path.read_text())
            if data.get("schema") != "basis-environment-setup-summary-v1":
                continue
            for entry in data.get("environments", []):
                name = entry.get("recipe_name") or entry.get("name", "")
                expected = prefix_root / ("basis-" + name)
                actual = Path(entry["prefix"]).resolve() if entry.get("prefix") else None
                requested = Path(entry["requested_prefix"]).resolve() if entry.get("requested_prefix") else actual
                if actual and (requested == expected.resolve() or
                               entry.get("backend") == "managed" and actual.parent == prefix_root.resolve()):
                    verified[name] = {"status": entry.get("status"), "prefix": actual,
                                      "backend": entry.get("backend", "external")}
        except (OSError, ValueError, TypeError):
            continue
    rows = []
    for name in recipe_names():
        record = verified.get(name, {})
        actual = record.get("prefix", prefix_root / ("basis-" + name))
        exists = actual.is_dir()
        status = "Not installed in selected directory"
        if exists:
            status = {"ready": "Verified during setup", "failed": "Setup verification failed"}.get(
                record.get("status"), "Folder exists · unverified")
        rows.append({"Environment": name, "Installation record": status,
                     "Deployment": record.get("backend", "not recorded"), "Environment path": str(actual)})
    return rows


def launch(request, new=False):
    try:
        job = submit(workspace, request, new_attempt=new or new_attempt)
        st.session_state["selected_job"] = job["id"]
        st.success(f"Job submitted · {job['state']}. Open Progress & jobs to follow it.")
        next_step("Follow this job →", "Jobs", "follow_submitted_job")
        if job.get("reconnected"):
            st.info("Reconnected to a saved job. To recheck changed inputs or resources, select New attempt in the sidebar.")
    except (ValueError, OSError, yaml.YAMLError) as exc:
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
    st.subheader("Your next step")
    cards = st.columns(4)
    for col, number, heading, detail, label, target in zip(
        cards, ("01", "02", "03", "04"),
        ("Tools & resources", "Add samples", "Check & run", "Explore results"),
        ("Install tool environments, then check study resources.",
         "Import Excel, CSV or TSV with sample names or input paths.",
         "Choose Safety, DeepSafe and Extend. Check readiness first.",
         "Open rich reports, trees and the supporting evidence."),
        ("Open setup →", "Add samples →", "Prepare a run →", "Open results →"),
        ("Setup", "Samples", "Run", "Results & export")):
        with col, st.container(border=True):
            st.markdown(f'<div class="basis-step">Step {number}</div>', unsafe_allow_html=True)
            st.markdown("### " + heading)
            st.write(detail)
            next_step(label, target, "start_" + number)
    st.divider()
    a, b, c = st.columns(3)
    a.metric("Saved jobs", len(jobs))
    b.metric("Active jobs", sum(j["state"] in {"queued", "running", "orphaned_active"} for j in jobs))
    c.metric("Tool environment recipes", len(recipe_names()))
    with st.container(border=True):
        st.subheader("Try the workflow first")
        st.write("The synthetic demonstration creates example results without biological tools, databases or GPU inference.")
        if st.button("Run synthetic demonstration", key="run_demo"):
            launch({"kind": "demo", "engine": "snakemake"})
    if jobs:
        with st.expander("Recent activity", expanded=True):
            st.dataframe([{k: j.get(k) for k in ("kind", "state", "created_at")} for j in jobs[:10]], hide_index=True)
            next_step("View saved jobs →", "Jobs", "recent_jobs")

elif page == "Setup":
    st.subheader("1. Install tool environments")
    st.write("Snakemake creates its managed Conda environments from the tool recipes and verifies them. Existing named BaSIS environments in your selected directory are verified and reused without modification.")
    envs = st.multiselect("Select tool environments", recipe_names(), default=["core-amr", "rgi", "diamond", "context", "antismash", "mobsuite", "deepsafe"])
    with st.expander("Advanced installation options"):
        prefix_value = st.text_input("Environment parent directory", value=str(workspace / ".basis-envs"), key="setup_prefix_root")
        st.caption("New environments use Snakemake-managed cache names. To reuse an earlier installation, select its .basis-envs directory containing basis-ENV folders. Only this directory is checked; study configuration paths are unchanged.")
        setup_cores = st.number_input("Concurrent installation jobs", min_value=1, max_value=4, value=1)
        st.caption("Recipes are starting specifications. Captured environment locks are required to restore exact tested package versions.")
    prefix_root = (workspace / Path(prefix_value).expanduser()).resolve() if prefix_value.strip() else workspace / ".basis-envs"
    setup_request = {"kind": "setup", "engine": "snakemake", "envs": envs, "cores": int(setup_cores), "prefix_root": str(prefix_root)}
    a, b = st.columns(2)
    if a.button("Preview setup plan", disabled=not envs):
        launch({**setup_request, "create": False})
    create = b.checkbox("Allow package downloads for the selected environments", key="allow_packages")
    if b.button("Install missing tool environments", type="primary", disabled=not create or not envs):
        launch({**setup_request, "create": True})
    st.info("This step prepares tool environments only. Prepare explicitly configured supported databases on Databases & models, then run study preflight.")
    next_step("Prepare databases & models →", "Resources", "setup_resources")
    with st.expander("Environment installation records"):
        st.caption("Environment directory: " + str(prefix_root))
        st.dataframe(environment_inventory(prefix_root), hide_index=True)
        st.caption("Recorded verification describes the last setup check. An existing folder alone is unverified. Study preflight checks the resources actually declared by your configuration, including tools installed elsewhere.")
    st.divider()
    st.subheader("2. Check your study resources")
    st.write("Have tools or databases from an earlier BaSIS installation? Use the study configuration pointing to those resources and check it here before installing duplicates.")
    cfg = config_field("setup_config")
    a, b = st.columns(2)
    deep = a.checkbox("Check DeepSafe resources", value=True)
    extend = b.checkbox("Check Extend resources", value=True)
    if st.button("Check inputs, tools and databases", disabled=not cfg):
        launch({"kind": "doctor", "config": cfg, "deep": deep, "extend": extend})
    st.caption("Preflight checks declared resources. Native execution and GPU inference are verified by acceptance runs.")
    next_step("Continue to samples →", "Samples", "setup_next")

elif page == "Resources":
    st.subheader("Explicit database preparation")
    st.write("Use a resource configuration describing the selected versions, destinations and verification checks. The plan is read-only; downloads and index building start only when you request preparation.")
    resource_config = st.text_input("Resource configuration path", key="resource_config",
                                    placeholder="/home/jimi/bactomics/resources.yaml")
    configured_resources = {}
    if resource_config:
        try:
            from basis.resource_setup import load_resource_config
            resource_data = load_resource_config(Path(resource_config).expanduser())
            configured_resources = resource_data["resources"]
        except (ValueError, OSError, yaml.YAMLError) as exc:
            st.error(str(exc))
    selected_resources = st.multiselect("Resources to prepare", list(configured_resources),
        format_func=lambda name: f"{name} · {configured_resources[name]['backend']}", key="selected_resources")
    if selected_resources:
        st.dataframe([{"Resource": name, "Backend": configured_resources[name]["backend"],
            "Requested version": configured_resources[name].get("version"),
            "Destination": configured_resources[name].get("destination")} for name in selected_resources], hide_index=True)
    with st.expander("Advanced resource preparation options"):
        resource_cores = st.number_input("Resource preparation CPU budget", min_value=1, value=4)
        resource_conda = st.text_input("Snakemake environment cache (optional)", key="resource_conda_prefix")
        st.caption("A configured resource environment_prefix reuses that Conda environment. Otherwise Snakemake creates the recipe environment in its cache.")
    resource_request = {"kind": "resources", "config": resource_config,
                        "resources": selected_resources, "cores": int(resource_cores)}
    if resource_conda.strip():
        resource_request["conda_prefix"] = str((workspace / Path(resource_conda).expanduser()).resolve())
    a, b = st.columns(2)
    if a.button("Preview resource plan", disabled=not selected_resources):
        launch({**resource_request, "execute": False})
    permit_resources = b.checkbox("Allow downloads and index building for selected resources", key="allow_resources")
    if b.button("Prepare selected resources", type="primary", disabled=not selected_resources or not permit_resources):
        launch({**resource_request, "execute": True})
    st.info("Supported preparation includes AMRFinderPlus, geNomad, antiSMASH, MOB-suite, BacMet and explicitly pinned files. Pinned files alone may still need indexing or tool-specific configuration. Preparation completion does not establish study readiness.")
    st.caption("Source versions and checksums must be declared in the resource configuration. Analysis runs do not silently update these resources. Saved preparation summaries and manifests are available in Progress & jobs.")
    next_step("Check study readiness →", "Setup", "resources_preflight")

elif page == "Samples":
    st.subheader("Create a study from a sample sheet")
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
    root = st.text_input("Where are the sample folders? (optional)", placeholder="/home/jimi/bactomics")
    with st.expander("Import options and existing resource configuration"):
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
    st.caption("Each import writes a new study inside its job folder. Open the import job for its configuration path, then check readiness before analysis.")
    next_step("Continue to readiness & run →", "Run", "samples_next")

elif page == "Run":
    st.subheader("Choose your study")
    cfg = config_field("run_config")
    config = None
    if cfg:
        try:
            config = load_config(Path(cfg).expanduser())
        except (ValueError, OSError, yaml.YAMLError) as exc:
            st.error(str(exc))
    if config is not None and st.session_state.get("budget_source") != cfg:
        for field in ("cores", "memory_mb", "gpus"):
            st.session_state["budget_" + field] = config["execution"][field]
        st.session_state["budget_source"] = cfg
        st.session_state["run_environment_backend"] = "external" if config["execution"].get("environment_backend") == "external" else "auto"
        st.session_state["run_conda_prefix"] = config["execution"].get("conda_prefix") or str(workspace / ".basis-envs")
    st.subheader("Analysis layers")
    a, b, c = st.columns(3)
    with a, st.container(border=True):
        st.markdown("### Safety")
        st.write("Screen with the core modules enabled in your study configuration.")
        st.caption("Baseline evidence · configured modules")
    with b, st.container(border=True):
        deep = st.checkbox("DeepSafe", value=True)
        st.write("Investigate selected candidates using domains, motifs, context and eligible reference analyses.")
    with c, st.container(border=True):
        extend = st.checkbox("Extend", value=True)
        st.write("Add PathogenFinder2 model evidence and prioritise proteins for follow-up.")
    st.caption("Snakemake schedules the required steps and resource budgets. Scientific interpretation and evidence gates remain part of BaSIS.")
    with st.expander("Advanced settings · resources and execution"):
        a, b, c = st.columns(3)
        cores = a.number_input("CPU cores", min_value=1, value=4, key="budget_cores")
        memory = b.number_input("Memory budget (MiB)", min_value=1, value=16000, step=1000, key="budget_memory_mb")
        gpu = c.number_input("Scheduled GPUs", min_value=0, max_value=1, value=0, key="budget_gpus")
        engine = st.selectbox("Execution engine", ("snakemake", "local"),
                              format_func=lambda value: "Snakemake · recommended" if value == "snakemake" else "Local BaSIS runner")
        environment_backend = st.selectbox("Environment deployment", ("auto", "external"), key="run_environment_backend",
            format_func=lambda value: "Automatic · manage missing, reuse configured prefixes" if value == "auto" else "Existing tools only · no automatic environment creation",
            disabled=engine == "local")
        run_conda = st.text_input("Snakemake environment cache", value=str(workspace / ".basis-envs"), key="run_conda_prefix")
        st.caption("Automatic mode uses native Snakemake Conda deployment for stages without a configured command prefix and retains configured per-module deployment overrides. Explicit command prefixes keep using their existing environments. Existing-tools mode overrides deployment for every module and DeepSafe, using configured prefixes or executable PATH without creating environments.")
        if engine == "local":
            st.info("The local runner uses existing tools only; automatic Conda deployment requires the Snakemake engine.")
            environment_backend = "external"
        st.caption("CPU/RAM/GPU values are scheduling budgets. Tool settings and model device choices come from the configuration.")
        if config is not None:
            st.dataframe([{"Module": n, "Enabled in config": m.get("enabled"), "Mode": m.get("mode", "native"),
                           "Executable": m.get("executable"), "Database": m.get("database")} for n, m in config["modules"].items()], hide_index=True)
            st.json({"deep": config["deep"], "sample_module_overrides": config.get("sample_module_overrides", {})})
        fresh = st.checkbox("Create another attempt if these settings have already finished")
    request = {"config": cfg, "deep": deep, "extend": extend, "cores": int(cores), "memory_mb": int(memory), "gpus": int(gpu),
               "environment_backend": environment_backend}
    if run_conda.strip():
        request["conda_prefix"] = str((workspace / Path(run_conda).expanduser()).resolve())
    a, b = st.columns(2)
    if a.button("Check readiness", disabled=config is None):
        launch({**request, "kind": "doctor"}, fresh)
    if b.button("Start analysis", type="primary", disabled=config is None):
        launch({**request, "kind": "run", "engine": engine}, fresh)
    st.caption("Check readiness before the first run or after changing inputs. Each job gets its own configuration, results and placement cache. Repeated clicks reconnect to the same job unless another finished attempt is explicitly requested.")

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
            a, b = st.columns(2)
            a.metric("Job status", job["state"].replace("_", " "))
            b.metric("Exit code", str(job.get("exit_code")) if job.get("exit_code") is not None else "In progress")
            st.caption(job.get("message", "Progress refreshes every three seconds. Reconnect after closing the browser using this workspace."))
            st.markdown("#### Live workflow output")
            st.caption("Snakemake scheduling, native tool activity and completion messages appear here. The saved console log retains the full output.")
            console = tail(job["log"])
            observed = scheduler_progress(console)
            if observed.get("latest_rule"):
                st.write("Latest reported workflow step:", observed["latest_rule"].replace("_", " "))
            if "scheduler_percent" in observed:
                st.progress(observed["scheduler_percent"] / 100,
                    text=f"Scheduler: {observed['completed_steps']} of {observed['scheduled_steps']} steps completed")
                st.caption("Step counts may grow as candidate analyses are scheduled. Scheduler progress does not indicate biological interpretation.")
            st.code(console, language=None)
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
        next_step("Explore available results →", "Results & export", "jobs_results")

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
