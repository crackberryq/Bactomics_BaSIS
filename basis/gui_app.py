"""Optional browser control panel. Scientific computation stays in the CLI."""
from __future__ import annotations

import hashlib
from html import escape
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
from basis.gui_theme import CSS, hero, phase_plan, state_badge, study_configurations
from basis.report_citation import AUTHOR, ORCID, citation_text, write_citations

st.set_page_config(page_title="BaSIS · Research workspace", page_icon="🧬", layout="wide")
workspace = Path(os.environ.get("BASIS_GUI_WORKSPACE", str(Path.home() / "BaSIS-workspace"))).expanduser().resolve()
st.markdown(CSS, unsafe_allow_html=True)
PAGE_LABELS = {"Overview": "Overview", "Samples": "Samples & studies", "Run": "Run / resume",
               "Jobs": "Live progress", "Results & export": "Reports & evidence",
               "Setup": "Tool environments", "Resources": "Databases & models"}


def navigate(destination):
    st.session_state["page"] = destination


page = st.session_state.setdefault("page", "Overview")
if page not in PAGE_LABELS:
    page = st.session_state["page"] = "Overview"
st.sidebar.markdown('<div class="basis-brand"><div class="basis-brand-mark">B</div><div><strong>BaSIS</strong><small>BACTROMICS ENVIRONMENT</small></div></div>', unsafe_allow_html=True)
st.sidebar.markdown('<div class="basis-nav-caption">Research workspace</div>', unsafe_allow_html=True)
for name in ("Overview", "Samples", "Run", "Jobs", "Results & export"):
    st.sidebar.button(PAGE_LABELS[name], key="nav_" + name, on_click=navigate, args=(name,),
                      type="primary" if page == name else "secondary", width="stretch")
with st.sidebar.expander("Advanced preparation", expanded=page in {"Setup", "Resources"}):
    for name in ("Setup", "Resources"):
        st.button(PAGE_LABELS[name], key="nav_" + name, on_click=navigate, args=(name,),
                  type="primary" if page == name else "secondary", width="stretch")
active_study = st.session_state.get("active_study")
st.sidebar.markdown('<div class="basis-sidebar-study">CURRENT STUDY<strong>' + escape(Path(active_study).parent.name if active_study else "No study selected") + '</strong>Choose a study to run or resume its workflow.</div>', unsafe_allow_html=True)
with st.sidebar.expander("Workspace details"):
    st.caption(str(workspace))
    st.caption("Saved jobs reconnect after a browser refresh. Run / resume retains the selected study outputs.")
st.sidebar.caption(f"BaSIS {__version__} · Linux / WSL")
st.sidebar.markdown(f"[{AUTHOR} · ORCID]({ORCID})")
new_attempt = False
HEADINGS = {
    "Overview": ("From genome to evidence", "A connected workspace for your genomic safety studies. Prepare a study, follow its analysis and explore every finding through one evidence report."),
    "Setup": ("Prepare your analysis tools", "Plan, install and verify tool environments with Snakemake. Then check the databases and models required by your study."),
    "Resources": ("Prepare your study resources", "Choose explicitly configured resources, review the plan and let Snakemake coordinate supported downloads and index building."),
    "Samples": ("Bring your samples together", "Import a sample sheet from HybAs or another annotation workflow. Preview every sample before creating your study."),
    "Run": ("Your study. One workflow.", "Choose your evidence layers and start or resume the same study. Snakemake manages preparation, analysis and recovery."),
    "Jobs": ("Every step, in view", "Follow recorded workflow activity and live logs. The workflow continues when you leave this page; return here to reconnect."),
    "Results & export": ("Explore the evidence", "Open your interactive report for genomic context, candidate details, motifs, trees and downloadable evidence."),
}
title, description = HEADINGS[page]
st.markdown('<div class="basis-breadcrumb"><span>Bactromics / <strong>' + escape(PAGE_LABELS[page]) + '</strong></span><span class="basis-local">Local research workspace</span></div>', unsafe_allow_html=True)
st.markdown(hero(title, description, __version__), unsafe_allow_html=True)


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
        st.success(f"{job['state'].replace('_', ' ').capitalize()} · Open Live progress to follow this job.")
        next_step("Follow this job →", "Jobs", "follow_submitted_job")
        if job.get("reconnected"):
            st.info("Reconnected to the saved job. Run / resume starts a new workflow invocation after a finished run, retaining the same study outputs.")
    except (ValueError, OSError, RuntimeError, yaml.YAMLError) as exc:
        st.error(str(exc))


def config_field(key="config"):
    return st.text_input("Study configuration", key=key,
                         placeholder="/home/jimi/bactomics/study/config.yaml")


@st.cache_resource
def report_server(root, related_roots=()):
    return ReportServer(root, related_roots)


def inspect_results(root, related_roots=()):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        st.info("Select an existing results directory.")
        return
    report = next((root / n for n in ("index.html", "report.html") if (root / n).is_file()), None)
    if report:
        st.link_button("Open interactive report ↗", report_server(str(root), related_roots).url(report.name), type="primary")
        st.caption("Open the complete evidence workspace: findings, genomic context, candidate details, motifs, trees and source files.")
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
            st.dataframe(rows, width="stretch", hide_index=True)
        for record in data.get("deep_records", []):
            if record.get("module") == "deepsafe":
                metrics = record.get("metrics", {})
                st.caption(f"{record.get('sample_id')} DeepSafe · selected {metrics.get('selected_candidates', 0)} · "
                           f"completed {metrics.get('completed_candidates', 0)} · deferred {metrics.get('deferred_candidates', 0)}")
    with st.expander("Download evidence and methods"):
        st.caption("These are saved artifacts from the selected results directory. Related folders must remain available for links in the report.")
        for name in ("evidence.json", "run-summary.json", "findings.tsv", "recommendations.tsv", "methods.txt", "CITATION.cff", "BaSIS.bib", "BaSIS.ris"):
            path = root / name
            if path.is_file() and path.stat().st_size <= 50 * 1024 * 1024:
                st.download_button("Download " + name, path.read_bytes(), file_name=name, key="dl_" + str(path))
    st.code(str(root), language=None)


if page == "Overview":
    jobs = list_jobs(workspace)
    studies = study_configurations(workspace, jobs)
    a, b, c = st.columns(3)
    a.metric("Studies in this workspace", len(studies))
    b.metric("Active jobs", sum(j["state"] in {"queued", "running", "orphaned_active"} for j in jobs))
    c.metric("Saved job records", len(jobs))
    st.markdown(phase_plan(), unsafe_allow_html=True)
    a, b = st.columns([1.2, 1])
    with a, st.container(border=True):
        st.markdown('<div class="basis-step">Start a study</div>', unsafe_allow_html=True)
        st.subheader("Bring your samples together")
        st.write("Open a HybAs sample folder or import an Excel, CSV or TSV sheet. Existing BaSIS studies can be selected directly.")
        next_step("Add samples or open a study →", "Samples", "start_01")
        next_step("Run / resume an existing study →", "Run", "start_02")
    with b, st.container(border=True):
        st.markdown('<div class="basis-step">Continue your work</div>', unsafe_allow_html=True)
        st.subheader("Pick up where you left off")
        st.write("Reconnect to an active workflow, inspect a stopped run, or explore your saved evidence.")
        next_step("Follow live progress →", "Jobs", "start_03")
        next_step("Open reports & evidence →", "Results & export", "start_04")
    if jobs:
        st.subheader("Recent activity")
        st.dataframe([{"Operation": j.get("kind"), "Status": j.get("state", "unknown").replace("_", " "),
                       "Started (UTC)": j.get("created_at", "")[:19],
                       "Study": Path(j.get("request", {}).get("config") or j.get("outdir") or ".").parent.name}
                      for j in jobs[:8]], width="stretch", hide_index=True)
    with st.expander("Explore a synthetic demonstration"):
        st.write("Create example reports without biological tools, databases or GPU inference.")
        if st.button("Run synthetic demonstration", key="run_demo"):
            launch({"kind": "demo", "engine": "snakemake"})

elif page == "Setup":
    st.subheader("Prepare tool environments in advance")
    st.info("New studies install required environments during the normal run. Use this optional page to prepare them in advance.")
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
    st.subheader("Start from a HybAs sample")
    st.write("Select the sample folder or its annotation directory. BaSIS imports the available annotation and recorded taxonomy.")
    hybas_dir = st.text_input("HybAs sample folder", key="hybas_dir", placeholder="/home/jimi/bactomics/isolate2")
    with st.expander("Study location and scope"):
        init_directory = st.text_input("New study directory (optional)", key="init_directory", placeholder="Leave blank to save in this workspace")
        init_preset = st.selectbox("Safety preset", ("full", "core"), key="init_preset",
                                  format_func=lambda value: "Full · all supported screening modules" if value == "full" else "Core · baseline screening modules")
    if st.button("Create study from HybAs", key="init_hybas", type="primary", disabled=not hybas_dir.strip()):
        launch({"kind": "init", "hybas_dir": hybas_dir, "preset": init_preset,
                **({"directory": init_directory} if init_directory.strip() else {})})
    st.caption("Study creation records inputs and configuration. Installation and analysis start when you choose Run / resume.")
    st.divider()
    st.subheader("Or import a sample sheet")
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
    st.caption("Imported studies appear in the saved-study selector on Run / resume. New studies use automatic preparation unless you supply an existing base configuration.")
    next_step("Continue to run analysis →", "Run", "samples_next")

elif page == "Run":
    st.subheader("Choose your study")
    saved_studies = study_configurations(workspace, list_jobs(workspace))
    if saved_studies:
        def choose_saved_study():
            chosen = st.session_state.get("saved_study")
            if chosen:
                st.session_state["run_config"] = chosen
                st.session_state["active_study"] = chosen
        st.selectbox("Saved studies", [""] + saved_studies, key="saved_study", on_change=choose_saved_study,
                     format_func=lambda value: "Select a saved study…" if not value else str(Path(value).parent))
    if "run_config" not in st.session_state and active_study:
        st.session_state["run_config"] = active_study
    cfg = config_field("run_config")
    config = None
    if cfg:
        try:
            config = load_config(Path(cfg).expanduser())
            st.session_state["active_study"] = cfg
        except (ValueError, OSError, yaml.YAMLError) as exc:
            st.error(str(exc))
    if config is not None and st.session_state.get("budget_source") != cfg:
        for field in ("cores", "memory_mb", "gpus"):
            st.session_state["budget_" + field] = config["execution"][field]
        st.session_state["budget_source"] = cfg
        st.session_state["run_environment_backend"] = "external" if config["execution"].get("environment_backend") == "external" else "auto"
        st.session_state["run_conda_prefix"] = config["execution"].get("conda_prefix") or str(workspace / ".basis-envs")
    if config is not None and config.get("resources", {}).get("policy") == "auto":
        st.info("Automatic setup is enabled. All tools, databases and models required for this run are prepared and verified before sample analysis starts. First-run downloads can be large.")
    st.markdown(phase_plan(), unsafe_allow_html=True)
    if config is not None:
        with st.expander("Samples in this study"):
            try:
                from basis.inputs import load_samples_diagnostic
                entries = load_samples_diagnostic(config["samples"])
                st.dataframe([{"Sample": entry["declaration"].get("sample_id", ""),
                               "Taxon": (entry.get("sample") or entry["declaration"]).get("taxon") or "Not declared",
                               "Input declaration": "; ".join(entry["errors"]) or "Resolved · validation runs before analysis"}
                              for entry in entries], hide_index=True, width="stretch")
            except (OSError, ValueError, KeyError) as exc:
                st.caption("Sample preview: " + str(exc))
    resume_options = st.session_state.pop("resume_options", None)
    if resume_options and resume_options.get("config") == cfg:
        for key in ("cores", "memory_mb", "gpus"):
            if key in resume_options:
                st.session_state["budget_" + key] = resume_options[key]
        st.session_state["run_deep"] = resume_options.get("deep", False)
        st.session_state["run_extend"] = resume_options.get("extend", False)
        st.session_state["run_environment_backend"] = resume_options.get("environment_backend", "auto")
        if resume_options.get("conda_prefix"):
            st.session_state["run_conda_prefix"] = resume_options["conda_prefix"]
    st.subheader("Analysis layers")
    a, b, c = st.columns(3)
    with a, st.container(border=True):
        st.markdown('<div class="basis-step">01 · Included</div>', unsafe_allow_html=True)
        st.markdown("### Safety")
        st.write("Screen with the core modules enabled in your study configuration.")
        st.caption("Baseline evidence · configured modules")
    with b, st.container(border=True):
        st.markdown('<div class="basis-step">02 · Candidate review</div>', unsafe_allow_html=True)
        deep = st.checkbox("DeepSafe", value=True, key="run_deep")
        st.write("Investigate selected candidates using domains, motifs, context and eligible reference analyses.")
    with c, st.container(border=True):
        st.markdown('<div class="basis-step">03 · Model evidence</div>', unsafe_allow_html=True)
        extend = st.checkbox("Extend", value=True, key="run_extend")
        st.write("Add PathogenFinder2 model evidence and prioritise proteins for follow-up.")
    st.caption("Snakemake schedules the required steps and resource budgets. Scientific interpretation and evidence gates remain part of BaSIS.")
    with st.expander("Advanced settings · resources and execution"):
        a, b, c = st.columns(3)
        cores = a.number_input("CPU cores", min_value=1, value=4, key="budget_cores")
        memory = b.number_input("Memory budget (MiB)", min_value=1, value=16000, step=1000, key="budget_memory_mb")
        gpu = c.number_input("Scheduled GPUs", min_value=0, max_value=1, value=0, key="budget_gpus")
        environment_backend = st.selectbox("Environment deployment", ("auto", "external"), key="run_environment_backend",
            format_func=lambda value: "Automatic · manage missing, reuse configured prefixes" if value == "auto" else "Existing tools only · verify configured environments")
        run_conda = st.text_input("Snakemake environment cache", value=str(workspace / ".basis-envs"), key="run_conda_prefix")
        st.caption("Snakemake is the workflow engine. Automatic mode creates missing environments from pinned recipes and reuses configured prefixes; existing-tools mode only verifies the supplied environments.")
        st.caption("CPU/RAM/GPU values are scheduling budgets. Tool settings and model device choices come from the configuration.")
        if config is not None:
            st.dataframe([{"Module": n, "Enabled in config": m.get("enabled"), "Mode": m.get("mode", "native"),
                           "Executable": m.get("executable"), "Database": m.get("database")} for n, m in config["modules"].items()], hide_index=True)
            st.json({"deep": config["deep"], "sample_module_overrides": config.get("sample_module_overrides", {})})
    request = {"config": cfg, "deep": deep, "extend": extend, "cores": int(cores), "memory_mb": int(memory), "gpus": int(gpu),
               "environment_backend": environment_backend}
    if run_conda.strip():
        request["conda_prefix"] = str((workspace / Path(run_conda).expanduser()).resolve())
    a, b = st.columns(2)
    if a.button("Check readiness", disabled=config is None):
        launch({**request, "kind": "doctor"}, True)
    if b.button("Run / resume study", type="primary", disabled=config is None, key="run_study"):
        launch({**request, "kind": "run", "engine": "snakemake"}, True)
    st.caption("Input checks run automatically. Run / resume retains the configured study output directory; Snakemake reruns missing, incomplete or invalidated work. Repeated clicks reconnect while the same request is active.")
    if config is not None:
        with st.expander("Study files and CLI starting point"):
            command = ["basis", "run", "-c", cfg, "--engine", "snakemake", "--cores", str(int(cores)), "-v", "--output", "summary"]
            command += (["-d"] if deep else []) + (["-e"] if extend else [])
            st.code(shlex.join(command), language="bash")
            st.caption("This command uses the source configuration. GUI budget and environment choices are captured in the job configuration; Live progress shows its exact launch command.")
            st.caption("Study outputs: " + str(config.get("outdir", "not configured")))
            st.caption("Sample sheet: " + str(config.get("samples", "not configured")))
            if Path(cfg).expanduser().is_file():
                st.download_button("Download source configuration", Path(cfg).expanduser().read_bytes(), "config.yaml", key="source_config_download")

elif page == "Jobs":
    st.subheader("Progress and saved jobs")
    jobs = list_jobs(workspace)
    if not jobs:
        st.markdown('<div class="basis-empty"><strong>Your workflow activity will appear here.</strong>Create or select a study, then use Run / resume. This page reconnects to saved jobs and displays their actual logs.</div>', unsafe_allow_html=True)
        next_step("Choose a study →", "Run", "empty_jobs_run")
    else:
        ids = [j["id"] for j in jobs]
        initial = st.session_state.get("selected_job")
        chosen = st.selectbox("Job", ids, index=ids.index(initial) if initial in ids else 0,
                             format_func=lambda value: next(f"{j['created_at'][:19]} · {j['kind']} · {j['state']} · {value[:8]}" for j in jobs if j["id"] == value))

        @st.fragment(run_every=3)
        def monitor():
            job = read_job(workspace, chosen)
            st.markdown(state_badge(job["state"]), unsafe_allow_html=True)
            a, b, c = st.columns(3)
            a.metric("Operation", job.get("kind", "not recorded"))
            b.metric("Job status", job["state"].replace("_", " "))
            c.metric("Exit code", str(job.get("exit_code")) if job.get("exit_code") is not None else "Pending")
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
            with st.container(height=420, border=True):
                st.code(console, language=None)
            if job.get("kind") == "run" and job.get("state") == "running":
                if st.button("Stop this workflow", key="stop_" + chosen):
                    try:
                        from basis.jobs import stop_job
                        stopped = stop_job(workspace, chosen)
                        st.info(stopped.get("message", "Stop request handled; inspect the recorded outcome below."))
                        with st.expander("Verified stop record", expanded=True):
                            st.json(stopped.get("stop_result", stopped))
                    except (OSError, ValueError, RuntimeError) as exc:
                        st.error(str(exc))
                st.caption("Stopping verifies the recorded launcher identity and its descendants. Other studies and unrelated processes are not selected.")
            if job.get("kind") == "run" and job.get("state") not in {"running", "queued", "orphaned_active"}:
                source = job.get("request", {}).get("config")
                if source and st.button("Resume this study →", key="resume_" + chosen):
                    st.session_state["active_study"] = source
                    st.session_state["run_config"] = source
                    st.session_state["resume_options"] = job.get("request", {})
                    st.session_state["page"] = "Run"
                    st.rerun()
            if job.get("kind") in {"import", "init"} and job.get("outdir"):
                imported_config = Path(job["outdir"]) / "config.yaml"
                if imported_config.is_file():
                    if st.button("Use this study →", key="use_" + chosen):
                        st.session_state["active_study"] = str(imported_config)
                        st.session_state["run_config"] = str(imported_config)
                        st.session_state["page"] = "Run"
                        st.rerun()
            if job.get("outdir"):
                report_root = Path(job["outdir"]) / "results" if job.get("kind") == "demo" else Path(job["outdir"])
                report = next((report_root / n for n in ("index.html", "report.html") if (report_root / n).is_file()), None)
                if report:
                    st.link_button("Open latest study report ↗", report_server(str(report_root)).url(report.name), type="primary")
                    st.caption("This is the latest report saved in the study directory. Its timestamp and scope may belong to a different attempt; the job log above remains specific to this launch.")
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
        if job.get("kind") == "run" and job.get("state") not in {"completed", "incomplete"}:
            st.info("Saved study reports can come from an earlier attempt. Check the report timestamp and scope against this job before interpretation.")
        default = str(Path(job["outdir"]) / "results") if job["kind"] == "demo" else job["outdir"]
    else:
        default = ""
    st.caption("The study directory contains its latest saved report. To identify the generating run, check the report timestamp and evidence provenance.")
    folder = st.text_input("Results directory (saved job or an existing study)", value=default)
    with st.expander("Linked reports and portable control panel"):
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
