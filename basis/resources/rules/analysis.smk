import os
import re
import sys
import basis
from pathlib import Path

from basis.config import ALL_MODULES
from basis.sample_policy import module_decision
from basis.workflow_environments import environment_for_job
from basis.bootstrap import bootstrap_dependencies

PLAN = config
ROOT = PLAN["outdir"]
SAMPLES = [s["sample_id"] for s in PLAN["samples"]]
SAMPLE_MAP = {s["sample_id"]: s for s in PLAN["samples"]}
MODULES = ALL_MODULES
PLAN_PATH = str(Path(ROOT) / "provenance" / "plans" / (PLAN["plan_hash"] + ".json"))
PYTHON = sys.executable
CONTROLLER_SOURCE = str(Path(basis.__file__).resolve().parent.parent)
RUN_ID = os.environ.get("BASIS_SCHEDULER_RUN_ID", PLAN["plan_hash"])
if not RUN_ID or Path(RUN_ID).name != RUN_ID or RUN_ID in {".", ".."}:
    raise ValueError("Invalid scheduler run identifier")
RECEIPTS = str(Path(ROOT) / "provenance" / "scheduler" / RUN_ID)
LAUNCH = os.environ.get("BASIS_SCHEDULER_LAUNCH_DIR", RECEIPTS)
FORCE = "--force" if os.environ.get("BASIS_FORCE") == "1" else ""

wildcard_constraints:
    sample="(?:" + "|".join(re.escape(sid) for sid in SAMPLES) + ")"


def configured_threads(module, sid=None):
    active = PLAN["options"]["deep"] if module == "deepsafe" else module_decision(PLAN, sid, module)["action"] == "run"
    cfg = PLAN["config"]["deep"] if module == "deepsafe" else PLAN["config"]["modules"][module]
    return min(int(cfg.get("threads", 1)), int(PLAN["config"]["execution"]["cores"])) if active else 1


def configured_resource(module, sid, key, default):
    active = PLAN["options"]["deep"] if module == "deepsafe" else module_decision(PLAN, sid, module)["action"] == "run"
    cfg = PLAN["config"]["deep"] if module == "deepsafe" else PLAN["config"]["modules"][module]
    return int(cfg.get(key, default)) if active else (0 if key == "gpus" else 1)


# Completion records are scheduler outputs. Scientific result files remain
# checksum-verified evidence so scheduling never deletes a reusable result.
rule all:
    default_target: True
    input:
        LAUNCH + "/outcome.json"


include: "bootstrap.smk"


rule validate_inputs:
    input:
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/input-validation.ready.json"
    threads: 1
    resources: mem_mb=1
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/validate_inputs.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime validate --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} 2>&1 | tee {log:q}"


rule check_configured_resources:
    input:
        validation=RECEIPTS + "/{sample}/input-validation.ready.json",
        plan=PLAN_PATH,
        prepared=lambda wc: bootstrap_dependencies(PLAN, wc.sample, wc.module)
    output:
        RECEIPTS + "/{sample}/checks/{module}.ready.json"
    wildcard_constraints:
        module="|".join(MODULES + ["deepsafe"])
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, wc.module)
    threads: 1
    resources: mem_mb=1
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/check_{module}.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime resources --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module {wildcards.module:q} 2>&1 | tee {log:q}"


rule setup_ready:
    input:
        checks=expand(RECEIPTS + "/{sample}/checks/{module}.ready.json", sample=SAMPLES, module=MODULES + ["deepsafe"]),
        plan=PLAN_PATH
    output:
        RECEIPTS + "/setup.ready.json"
    threads: 1
    resources: mem_mb=1
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/logs/setup_ready.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime setup --plan {params.plan:q} --run-dir {params.root:q} 2>&1 | tee {log:q}"


rule amrfinder:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/amrfinder.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/amrfinder.ready.json"
    threads:
        lambda wc: configured_threads("amrfinder", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("amrfinder", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("amrfinder", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "amrfinder")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/amrfinder.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module amrfinder --threads {threads} 2>&1 | tee {log:q}"


rule rgi:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/rgi.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/rgi.ready.json"
    threads:
        lambda wc: configured_threads("rgi", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("rgi", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("rgi", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "rgi")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/rgi.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module rgi --threads {threads} 2>&1 | tee {log:q}"


rule vfdb:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/vfdb.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/vfdb.ready.json"
    threads:
        lambda wc: configured_threads("vfdb", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("vfdb", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("vfdb", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "vfdb")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/vfdb.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module vfdb --threads {threads} 2>&1 | tee {log:q}"


rule antismash:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/antismash.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/antismash.ready.json"
    threads:
        lambda wc: configured_threads("antismash", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("antismash", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("antismash", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "antismash")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/antismash.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module antismash --threads {threads} 2>&1 | tee {log:q}"


rule genomad:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/genomad.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/genomad.ready.json"
    threads:
        lambda wc: configured_threads("genomad", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("genomad", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("genomad", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "genomad")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/genomad.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module genomad --threads {threads} 2>&1 | tee {log:q}"


rule mobsuite:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/mobsuite.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/mobsuite.ready.json"
    threads:
        lambda wc: configured_threads("mobsuite", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("mobsuite", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("mobsuite", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "mobsuite")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/mobsuite.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module mobsuite --threads {threads} 2>&1 | tee {log:q}"


rule integronfinder:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/integronfinder.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/integronfinder.ready.json"
    threads:
        lambda wc: configured_threads("integronfinder", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("integronfinder", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("integronfinder", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "integronfinder")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/integronfinder.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module integronfinder --threads {threads} 2>&1 | tee {log:q}"


rule bacmet:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/bacmet.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/bacmet.ready.json"
    threads:
        lambda wc: configured_threads("bacmet", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("bacmet", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("bacmet", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "bacmet")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/bacmet.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module bacmet --threads {threads} 2>&1 | tee {log:q}"


rule isescan:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/isescan.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/isescan.ready.json"
    threads:
        lambda wc: configured_threads("isescan", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("isescan", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("isescan", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "isescan")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/isescan.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module isescan --threads {threads} 2>&1 | tee {log:q}"


rule icescreen:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/icescreen.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/icescreen.ready.json"
    threads:
        lambda wc: configured_threads("icescreen", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("icescreen", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("icescreen", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "icescreen")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/icescreen.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module icescreen --threads {threads} 2>&1 | tee {log:q}"


rule crisprcasfinder:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/crisprcasfinder.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/crisprcasfinder.ready.json"
    threads:
        lambda wc: configured_threads("crisprcasfinder", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("crisprcasfinder", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("crisprcasfinder", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "crisprcasfinder")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/crisprcasfinder.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module crisprcasfinder --threads {threads} 2>&1 | tee {log:q}"


rule minced:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/minced.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/minced.ready.json"
    threads:
        lambda wc: configured_threads("minced", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("minced", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("minced", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "minced")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/minced.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module minced --threads {threads} 2>&1 | tee {log:q}"


rule mlst:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/mlst.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/mlst.ready.json"
    threads:
        lambda wc: configured_threads("mlst", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("mlst", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("mlst", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "mlst")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/mlst.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module mlst --threads {threads} 2>&1 | tee {log:q}"


rule pathogenfinder2:
    input:
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        check=RECEIPTS + "/{sample}/checks/pathogenfinder2.ready.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/pathogenfinder2.ready.json"
    threads:
        lambda wc: configured_threads("pathogenfinder2", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("pathogenfinder2", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("pathogenfinder2", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "pathogenfinder2")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/{sample}/logs/pathogenfinder2.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module pathogenfinder2 --threads {threads} 2>&1 | tee {log:q}"


include: "deep_candidates.smk"


rule consolidate_evidence:
    input:
        upstream=expand(RECEIPTS + "/{sample}/{module}.ready.json", sample=SAMPLES, module=MODULES),
        launch=LAUNCH + "/run-context.json",
        deep=expand(RECEIPTS + "/{sample}/deepsafe.ready.json", sample=SAMPLES),
        plan=PLAN_PATH
    output:
        LAUNCH + "/consolidated.json"
    threads: 1
    resources: mem_mb=1
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/logs/consolidate_evidence.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime consolidate --plan {params.plan:q} --run-dir {params.root:q} 2>&1 | tee {log:q}"


rule render_report:
    input:
        snapshot=LAUNCH + "/consolidated.json",
        plan=PLAN_PATH
    output:
        LAUNCH + "/report-artifacts.json"
    threads: 1
    resources: mem_mb=1
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/logs/render_report.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime report --plan {params.plan:q} --run-dir {params.root:q} 2>&1 | tee {log:q}"


rule capture_provenance:
    input:
        snapshot=LAUNCH + "/consolidated.json",
        report=LAUNCH + "/report-artifacts.json",
        plan=PLAN_PATH
    output:
        LAUNCH + "/outcome.json"
    threads: 1
    resources: mem_mb=1
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log:
        LAUNCH + "/logs/capture_provenance.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime provenance --plan {params.plan:q} --run-dir {params.root:q} 2>&1 | tee {log:q}"
