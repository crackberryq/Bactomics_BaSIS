# Candidate-sized jobs. The checkpoint discovers stable candidate identifiers;
# Snakemake owns the lifetime and retries of each ready target.
import json


def deep_candidate_targets(wc, phase):
    checkpoints.deep_prepare.get(sample=wc.sample)
    document = json.loads((Path(RECEIPTS) / wc.sample / "deep-stages/prepare.json").read_text())
    ids = document.get("selected_candidate_ids", [])
    return expand(RECEIPTS + "/{sample}/deep-candidates/{candidate}/{phase}.ready.json",
                  sample=[wc.sample], candidate=ids, phase=[phase])


def deep_candidate_parent(wc):
    if wc.phase == "domains":
        return checkpoints.deep_prepare.get(sample=wc.sample).output.state
    if wc.phase == "phylogeny":
        return RECEIPTS + "/" + wc.sample + "/deep-stages/tree-plan.ready.json"
    if wc.phase == "placement":
        return RECEIPTS + "/" + wc.sample + "/deep-stages/placement-plan.ready.json"
    previous = {"patterns": "phylogeny", "profiles": "patterns", "motifs": "profiles"}[wc.phase]
    return RECEIPTS + "/" + wc.sample + "/deep-candidates/" + wc.candidate + "/" + previous + ".ready.json"


checkpoint deep_prepare:
    input:
        upstream=lambda wc: expand(RECEIPTS + "/{sample}/{module}.ready.json", sample=[wc.sample], module=MODULES),
        check=RECEIPTS + "/{sample}/checks/deepsafe.ready.json",
        setup=ancient(RECEIPTS + "/setup.ready.json"),
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/prepare.ready.json"
    threads: lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda: lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log: LAUNCH + "/{sample}/logs/deep_prepare.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.deep_candidates prepare --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --threads {threads} 2>&1 | tee {log:q}"


rule deep_candidate:
    input:
        previous=deep_candidate_parent,
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-candidates/{candidate}/{phase}.ready.json"
    wildcard_constraints:
        candidate="cand_[a-f0-9]{20}",
        phase="domains|phylogeny|patterns|profiles|motifs|placement"
    threads: lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda: lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log: LAUNCH + "/{sample}/logs/deep_{candidate}_{phase}.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.deep_candidates candidate --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --candidate {wildcards.candidate:q} --phase {wildcards.phase:q} --threads {threads} 2>&1 | tee {log:q}"


rule deep_references:
    input:
        candidates=lambda wc: deep_candidate_targets(wc, "domains"),
        previous=RECEIPTS + "/{sample}/deep-stages/prepare.ready.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/references.ready.json"
    threads: lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda: lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log: LAUNCH + "/{sample}/logs/deep_references.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.deep_candidates references --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --threads {threads} 2>&1 | tee {log:q}"


rule deep_tree_plan:
    input:
        previous=RECEIPTS + "/{sample}/deep-stages/references.ready.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/tree-plan.ready.json"
    threads: lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda: lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log: LAUNCH + "/{sample}/logs/deep_tree_plan.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.deep_candidates tree-plan --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --threads {threads} 2>&1 | tee {log:q}"


rule deep_placement_plan:
    input:
        candidates=lambda wc: deep_candidate_targets(wc, "motifs"),
        previous=RECEIPTS + "/{sample}/deep-stages/tree-plan.ready.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/placement-plan.ready.json"
    threads: lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda: lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log: LAUNCH + "/{sample}/logs/deep_placement_plan.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.deep_candidates placement-plan --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --threads {threads} 2>&1 | tee {log:q}"


rule deep_finalize:
    input:
        candidates=lambda wc: deep_candidate_targets(wc, "placement"),
        previous=RECEIPTS + "/{sample}/deep-stages/placement-plan.ready.json",
        plan=PLAN_PATH
    output:
        state=RECEIPTS + "/{sample}/deep-stages/finalize.ready.json",
        result=RECEIPTS + "/{sample}/deepsafe.ready.json"
    threads: lambda wc: configured_threads("deepsafe", wc.sample)
    resources:
        mem_mb=lambda wc: configured_resource("deepsafe", wc.sample, "memory_mb", 8000)
    conda: lambda wc: environment_for_job(PLAN, wc.sample, "deepsafe")
    params: source=CONTROLLER_SOURCE, python=PYTHON, plan=PLAN_PATH, root=RECEIPTS
    log: LAUNCH + "/{sample}/logs/deep_finalize.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.deep_candidates finalize --plan {params.plan:q} --run-dir {params.root:q} --sample {wildcards.sample:q} --threads {threads} 2>&1 | tee {log:q}"
