# DeepSafe's selected candidates are discovered only after native evidence is
# normalized. Each layer is an independent Snakemake job with owned native files.
import json
from pathlib import Path
from safety.deep_resources import config_input_paths, load_discovered_inputs
from safety.state_storage import object_paths


def safety_deep_sources(*, finalization=False):
    # Analysis helpers affect analysis jobs. HTML rendering code deliberately
    # does not: refreshing the report must not invalidate native analyses.
    names = ("deep.py", "deep_stages.py", "deep_candidate_jobs.py", "deep_trees.py",
             "deep_alignment_review.py", "deep_consolidation.py", "deep_decisions.py",
             "references.py", "motifs.py", "prosite.py",
             "prosite_profiles.py", "placement.py", "placement_panels.py",
             "placement_native.py", "placement_scoring.py", "placement_diagnostics.py",
             "candidate_labels.py", "deep_workflow.py", "aware.py")
    # Completion/reference summaries are consumed during finalization, not by
    # the preceding analyses. Keep their provenance without rerunning those
    # analyses when only summary formatting or status semantics change.
    if finalization:
        names += ("deep_reporting.py",)
    return [str(ROOT / "workflow/lib/safety/deep.py"),
            str(ROOT / "workflow/lib/safety/state_storage.py"),
            str(ROOT / "workflow/lib/safety/deep_resources.py"),
            str(ROOT / "workflow/lib/safety/runtime.py")] + [
        str(ROOT / "workflow/vendor/basis" / name) for name in names]


def safety_deep_resource_paths(wc):
    discovered = checkpoints.safety_deep_resource_inputs.get(isolate=wc.isolate)
    document = load_discovered_inputs(discovered.output.state)
    # Enumerate live directory membership after config generation. New profile
    # files become declared inputs, and later edits remain visible on resume.
    return config_input_paths(document["deep"])


def safety_deep_targets(wc, phase):
    prepared = checkpoints.safety_deep_prepare.get(isolate=wc.isolate)
    safety_deep_state(prepared.output.state)
    document = json.loads(Path(prepared.output.state).read_text())
    return [safety_deep_state(path) for path in expand(
        "{isolate}/safety/deepsafe/candidates/{candidate}/{phase}.json",
        isolate=[wc.isolate], candidate=document["selected_candidate_ids"], phase=[phase])]


def safety_deep_parent(wc):
    root = wc.isolate + "/safety/deepsafe/"
    if wc.phase == "domains":
        return safety_deep_state(checkpoints.safety_deep_prepare.get(isolate=wc.isolate).output.state)
    if wc.phase == "phylogeny":
        return safety_deep_state(root + "states/tree-plan.json")
    if wc.phase == "placement":
        return safety_deep_state(root + "states/placement-plan.json")
    previous = {"patterns": "phylogeny", "profiles": "patterns", "motifs": "profiles"}[wc.phase]
    return safety_deep_state(root + "candidates/" + wc.candidate + "/" + previous + ".json")


def safety_deep_state(path):
    # The manifest is the stable DAG input. Check referenced objects on resume;
    # the worker verifies their checksums before consuming them. Making the
    # input list depend on whether a non-checkpoint parent exists would cause
    # unnecessary reruns on every transition from an empty to a populated DAG.
    if Path(path).is_file():
        for blob in object_paths(path):
            if not blob.is_file():
                raise ValueError("Missing shared state object: " + str(blob))
    return str(path)


checkpoint safety_deep_resource_inputs:
    input:
        config=lambda wc: CTX.module_config_path(wc.isolate, "deepsafe"),
        source=str(ROOT / "workflow/lib/safety/deep_resources.py")
    output:
        state="{isolate}/safety/deepsafe/states/resource-inputs.json"
    params: python=PYTHON, code=CODE_PATH
    conda: CTX.env("deepsafe")
    threads: 1
    resources: mem_mb=1000
    log: "{isolate}/safety/logs/deepsafe/resource-inputs.log"
    message: "DeepSafe: discover configured family resources for {wildcards.isolate}"
    shell:
        "mkdir -p $(dirname {log:q}); "
        "PYTHONPATH={params.code:q} {params.python:q} -m safety.deep_resources "
        "--config {input.config:q} --output {output.state:q} > {log:q} 2>&1"


checkpoint safety_deep_prepare:
    input:
        setup=lambda wc: ancient(CTX.setup_target(wc.isolate)),
        records=lambda wc: CTX.evidence_targets(wc.isolate),
        sample="{isolate}/safety/metadata/sample.json",
        config=lambda wc: CTX.module_config_path(wc.isolate, "deepsafe"),
        family_inputs=lambda wc: checkpoints.safety_deep_resource_inputs.get(isolate=wc.isolate).output.state,
        family_resources=safety_deep_resource_paths,
        source=safety_deep_sources()
    output:
        state="{isolate}/safety/deepsafe/states/prepare.json",
        native=directory("{isolate}/safety/deepsafe/native/prepare")
    params: python=PYTHON, code=CODE_PATH
    conda: CTX.env("deepsafe")
    threads: lambda wc: CTX.deep_config(wc.isolate)["threads"]
    resources: mem_mb=lambda wc: CTX.deep_config(wc.isolate)["memory_mb"]
    log: "{isolate}/safety/logs/deepsafe/prepare.log"
    benchmark: "{isolate}/safety/benchmarks/deepsafe/prepare.tsv"
    message: "DeepSafe: select candidates and extract context for {wildcards.isolate}"
    shell:
        "PYTHONPATH={params.code:q} {params.python:q} -m safety.deep prepare "
        "--isolate {wildcards.isolate:q} --output {output.state:q} --native {output.native:q} "
        "--family-inputs {input.family_inputs:q} --records {input.records:q} --config {input.config:q} "
        "--threads {threads} --log {log:q} >> {log:q} 2>&1"


rule safety_deep_candidate:
    input:
        previous=safety_deep_parent,
        source=safety_deep_sources()
    output:
        state="{isolate}/safety/deepsafe/candidates/{candidate}/{phase}.json",
        native=directory("{isolate}/safety/deepsafe/native/{phase}/{candidate}")
    wildcard_constraints:
        candidate="cand_[a-f0-9]{20}",
        phase="domains|phylogeny|patterns|profiles|motifs|placement"
    params: python=PYTHON, code=CODE_PATH
    conda: CTX.env("deepsafe")
    threads: lambda wc: CTX.deep_config(wc.isolate)["threads"]
    resources: mem_mb=lambda wc: CTX.deep_config(wc.isolate)["memory_mb"]
    log: "{isolate}/safety/logs/deepsafe/{candidate}/{phase}.log"
    benchmark: "{isolate}/safety/benchmarks/deepsafe/{candidate}/{phase}.tsv"
    message: "DeepSafe {wildcards.phase}: {wildcards.candidate}"
    shell:
        "PYTHONPATH={params.code:q} {params.python:q} -m safety.deep {wildcards.phase:q} "
        "--isolate {wildcards.isolate:q} --parent {input.previous:q} --candidate {wildcards.candidate:q} "
        "--output {output.state:q} --native {output.native:q} --threads {threads} "
        "--log {log:q} >> {log:q} 2>&1"


rule safety_deep_references:
    input:
        parent=lambda wc: safety_deep_state(wc.isolate + "/safety/deepsafe/states/prepare.json"),
        candidates=lambda wc: safety_deep_targets(wc, "domains"),
        source=safety_deep_sources()
    output:
        state="{isolate}/safety/deepsafe/states/references.json",
        native=directory("{isolate}/safety/deepsafe/native/references")
    params: python=PYTHON, code=CODE_PATH
    conda: CTX.env("deepsafe")
    threads: lambda wc: CTX.deep_config(wc.isolate)["threads"]
    resources: mem_mb=lambda wc: CTX.deep_config(wc.isolate)["memory_mb"]
    log: "{isolate}/safety/logs/deepsafe/references.log"
    benchmark: "{isolate}/safety/benchmarks/deepsafe/references.tsv"
    message: "DeepSafe: retrieve comparator evidence for {wildcards.isolate}"
    shell:
        "PYTHONPATH={params.code:q} {params.python:q} -m safety.deep references "
        "--isolate {wildcards.isolate:q} --parent {input.parent:q} --children {input.candidates:q} "
        "--output {output.state:q} --native {output.native:q} --threads {threads} "
        "--log {log:q} >> {log:q} 2>&1"


rule safety_deep_tree_plan:
    input:
        parent=lambda wc: safety_deep_state(wc.isolate + "/safety/deepsafe/states/references.json"),
        source=safety_deep_sources()
    output:
        state="{isolate}/safety/deepsafe/states/tree-plan.json",
        native=directory("{isolate}/safety/deepsafe/native/tree-plan")
    params: python=PYTHON, code=CODE_PATH
    conda: CTX.env("deepsafe")
    threads: lambda wc: CTX.deep_config(wc.isolate)["threads"]
    resources: mem_mb=lambda wc: CTX.deep_config(wc.isolate)["memory_mb"]
    log: "{isolate}/safety/logs/deepsafe/tree-plan.log"
    message: "DeepSafe: apply existing tree eligibility and budgets for {wildcards.isolate}"
    shell:
        "PYTHONPATH={params.code:q} {params.python:q} -m safety.deep tree-plan "
        "--isolate {wildcards.isolate:q} --parent {input.parent:q} --output {output.state:q} "
        "--native {output.native:q} --threads {threads} --log {log:q} >> {log:q} 2>&1"


rule safety_deep_placement_plan:
    input:
        parent=lambda wc: safety_deep_state(wc.isolate + "/safety/deepsafe/states/tree-plan.json"),
        candidates=lambda wc: safety_deep_targets(wc, "motifs"),
        source=safety_deep_sources()
    output:
        state="{isolate}/safety/deepsafe/states/placement-plan.json",
        native=directory("{isolate}/safety/deepsafe/native/placement-plan")
    params: python=PYTHON, code=CODE_PATH
    conda: CTX.env("deepsafe")
    threads: lambda wc: CTX.deep_config(wc.isolate)["threads"]
    resources: mem_mb=lambda wc: CTX.deep_config(wc.isolate)["memory_mb"]
    log: "{isolate}/safety/logs/deepsafe/placement-plan.log"
    message: "DeepSafe: audit existing placement panels for {wildcards.isolate}"
    shell:
        "PYTHONPATH={params.code:q} {params.python:q} -m safety.deep placement-plan "
        "--isolate {wildcards.isolate:q} --parent {input.parent:q} --children {input.candidates:q} "
        "--output {output.state:q} --native {output.native:q} --threads {threads} "
        "--log {log:q} >> {log:q} 2>&1"


rule safety_deep_finalize:
    input:
        parent=lambda wc: safety_deep_state(wc.isolate + "/safety/deepsafe/states/placement-plan.json"),
        candidates=lambda wc: safety_deep_targets(wc, "placement"),
        source=safety_deep_sources(finalization=True)
    output:
        state="{isolate}/safety/deepsafe/states/finalize.json",
        evidence="{isolate}/safety/evidence/deepsafe.json",
        native=directory("{isolate}/safety/deepsafe/native/finalize")
    params: python=PYTHON, code=CODE_PATH
    conda: CTX.env("deepsafe")
    threads: lambda wc: CTX.deep_config(wc.isolate)["threads"]
    resources: mem_mb=lambda wc: CTX.deep_config(wc.isolate)["memory_mb"]
    log: "{isolate}/safety/logs/deepsafe/finalize.log"
    benchmark: "{isolate}/safety/benchmarks/deepsafe/finalize.tsv"
    message: "DeepSafe: consolidate selected and deferred evidence for {wildcards.isolate}"
    shell:
        "PYTHONPATH={params.code:q} {params.python:q} -m safety.deep finalize "
        "--isolate {wildcards.isolate:q} --parent {input.parent:q} --children {input.candidates:q} "
        "--output {output.state:q} --native {output.native:q} --evidence {output.evidence:q} "
        "--threads {threads} --log {log:q} >> {log:q} 2>&1"
