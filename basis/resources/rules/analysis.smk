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
        RECEIPTS + "/outcome.json"


include: "bootstrap.smk"


rule validate_inputs:
    input:
        plan=PLAN_PATH,
        context=RECEIPTS + "/run-context.json"
    output:
        RECEIPTS + "/{sample}/input-validation.json"
    threads: 1
    resources: mem_mb=1
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime validate --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q}"


rule check_configured_resources:
    input:
        validation=RECEIPTS + "/{sample}/input-validation.json",
        plan=PLAN_PATH,
        prepared=lambda wc: bootstrap_dependencies(PLAN, wc.sample, wc.module)
    output:
        RECEIPTS + "/{sample}/checks/{module}.json"
    wildcard_constraints:
        module="|".join(MODULES + ["deepsafe"])
    threads: 1
    resources: mem_mb=1
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime resources --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module {wildcards.module:q}"


rule amrfinder:
    input:
        check=RECEIPTS + "/{sample}/checks/amrfinder.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/amrfinder.json"
    threads:
        lambda wc: configured_threads("amrfinder", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("amrfinder", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("amrfinder", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "amrfinder")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module amrfinder --threads {threads} {params.force}"


rule rgi:
    input:
        check=RECEIPTS + "/{sample}/checks/rgi.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/rgi.json"
    threads:
        lambda wc: configured_threads("rgi", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("rgi", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("rgi", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "rgi")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module rgi --threads {threads} {params.force}"


rule vfdb:
    input:
        check=RECEIPTS + "/{sample}/checks/vfdb.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/vfdb.json"
    threads:
        lambda wc: configured_threads("vfdb", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("vfdb", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("vfdb", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "vfdb")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module vfdb --threads {threads} {params.force}"


rule antismash:
    input:
        check=RECEIPTS + "/{sample}/checks/antismash.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/antismash.json"
    threads:
        lambda wc: configured_threads("antismash", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("antismash", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("antismash", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "antismash")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module antismash --threads {threads} {params.force}"


rule genomad:
    input:
        check=RECEIPTS + "/{sample}/checks/genomad.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/genomad.json"
    threads:
        lambda wc: configured_threads("genomad", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("genomad", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("genomad", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "genomad")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module genomad --threads {threads} {params.force}"


rule mobsuite:
    input:
        check=RECEIPTS + "/{sample}/checks/mobsuite.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/mobsuite.json"
    threads:
        lambda wc: configured_threads("mobsuite", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("mobsuite", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("mobsuite", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "mobsuite")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module mobsuite --threads {threads} {params.force}"


rule integronfinder:
    input:
        check=RECEIPTS + "/{sample}/checks/integronfinder.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/integronfinder.json"
    threads:
        lambda wc: configured_threads("integronfinder", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("integronfinder", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("integronfinder", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "integronfinder")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module integronfinder --threads {threads} {params.force}"


rule bacmet:
    input:
        check=RECEIPTS + "/{sample}/checks/bacmet.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/bacmet.json"
    threads:
        lambda wc: configured_threads("bacmet", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("bacmet", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("bacmet", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "bacmet")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module bacmet --threads {threads} {params.force}"


rule isescan:
    input:
        check=RECEIPTS + "/{sample}/checks/isescan.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/isescan.json"
    threads:
        lambda wc: configured_threads("isescan", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("isescan", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("isescan", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "isescan")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module isescan --threads {threads} {params.force}"


rule icescreen:
    input:
        check=RECEIPTS + "/{sample}/checks/icescreen.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/icescreen.json"
    threads:
        lambda wc: configured_threads("icescreen", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("icescreen", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("icescreen", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "icescreen")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module icescreen --threads {threads} {params.force}"


rule crisprcasfinder:
    input:
        check=RECEIPTS + "/{sample}/checks/crisprcasfinder.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/crisprcasfinder.json"
    threads:
        lambda wc: configured_threads("crisprcasfinder", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("crisprcasfinder", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("crisprcasfinder", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "crisprcasfinder")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module crisprcasfinder --threads {threads} {params.force}"


rule minced:
    input:
        check=RECEIPTS + "/{sample}/checks/minced.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/minced.json"
    threads:
        lambda wc: configured_threads("minced", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("minced", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("minced", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "minced")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module minced --threads {threads} {params.force}"


rule mlst:
    input:
        check=RECEIPTS + "/{sample}/checks/mlst.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/mlst.json"
    threads:
        lambda wc: configured_threads("mlst", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("mlst", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("mlst", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "mlst")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module mlst --threads {threads} {params.force}"


rule pathogenfinder2:
    input:
        check=RECEIPTS + "/{sample}/checks/pathogenfinder2.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/{sample}/pathogenfinder2.json"
    threads:
        lambda wc: configured_threads("pathogenfinder2", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("pathogenfinder2", wc.sample, "memory_mb", 4000),
        gpu=lambda wc: configured_resource("pathogenfinder2", wc.sample, "gpus", 0)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "pathogenfinder2")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime module --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --module pathogenfinder2 --threads {threads} {params.force}"


rule deep_prepare:
    input:
        upstream=lambda wc: expand(RECEIPTS + "/{sample}/{module}.json", sample=[wc.sample], module=MODULES),
        check=RECEIPTS + "/{sample}/checks/deepsafe.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/prepare.json"
    threads:
        lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime deep-stage --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --stage prepare --threads {threads} {params.force}"


rule deep_domains:
    input:
        previous=RECEIPTS + "/{sample}/deep-stages/prepare.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/domains.json"
    threads:
        lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime deep-stage --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --stage domains --threads {threads} {params.force}"


rule deep_references:
    input:
        previous=RECEIPTS + "/{sample}/deep-stages/domains.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/references.json"
    threads:
        lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime deep-stage --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --stage references --threads {threads} {params.force}"


rule deep_phylogeny:
    input:
        previous=RECEIPTS + "/{sample}/deep-stages/references.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/phylogeny.json"
    threads:
        lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime deep-stage --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --stage phylogeny --threads {threads} {params.force}"


rule deep_patterns:
    input:
        previous=RECEIPTS + "/{sample}/deep-stages/phylogeny.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/patterns.json"
    threads:
        lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime deep-stage --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --stage patterns --threads {threads} {params.force}"


rule deep_profiles:
    input:
        previous=RECEIPTS + "/{sample}/deep-stages/patterns.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/profiles.json"
    threads:
        lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime deep-stage --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --stage profiles --threads {threads} {params.force}"


rule deep_motifs:
    input:
        previous=RECEIPTS + "/{sample}/deep-stages/profiles.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/motifs.json"
    threads:
        lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime deep-stage --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --stage motifs --threads {threads} {params.force}"


rule deep_placement:
    input:
        previous=RECEIPTS + "/{sample}/deep-stages/motifs.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/placement.json"
    threads:
        lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime deep-stage --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --stage placement --threads {threads} {params.force}"


rule deep_finalize:
    input:
        previous=RECEIPTS + "/{sample}/deep-stages/placement.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/finalize.json",
        result=RECEIPTS + "/{sample}/deepsafe.json"
    threads:
        lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda:
        lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS, force=FORCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime deep-stage --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --stage finalize --threads {threads} {params.force}"


rule consolidate_evidence:
    input:
        upstream=expand(RECEIPTS + "/{sample}/{module}.json", sample=SAMPLES, module=MODULES),
        deep=expand(RECEIPTS + "/{sample}/deepsafe.json", sample=SAMPLES),
        plan=PLAN_PATH
    output:
        RECEIPTS + "/consolidated.json"
    threads: 1
    resources: mem_mb=1
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime consolidate --plan {params.plan:q} --run-dir {params.root:q}"


rule render_report:
    input:
        snapshot=RECEIPTS + "/consolidated.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/report-artifacts.json"
    threads: 1
    resources: mem_mb=1
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime report --plan {params.plan:q} --run-dir {params.root:q}"


rule capture_provenance:
    input:
        snapshot=RECEIPTS + "/consolidated.json",
        report=RECEIPTS + "/report-artifacts.json",
        plan=PLAN_PATH
    output:
        RECEIPTS + "/outcome.json"
    threads: 1
    resources: mem_mb=1
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.workflow_runtime provenance --plan {params.plan:q} --run-dir {params.root:q}"
