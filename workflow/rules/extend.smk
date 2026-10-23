# Extend is split at the expensive HDF5 boundary; no nested Conda or scheduler.
PF2_CODE = [str(ROOT / path) for path in (
    "workflow/lib/safety/extend.py", "workflow/lib/safety/runtime.py",
    "workflow/vendor/basis/adapters/extend.py", "workflow/vendor/basis/inputs.py",
    "workflow/vendor/basis/normalize.py", "workflow/vendor/basis/util.py")]
CCF_CODE = [str(ROOT / path) for path in (
    "workflow/lib/safety/extend.py", "workflow/lib/safety/ccf_assembly.py",
    "workflow/lib/safety/result_store.py", "workflow/lib/safety/runtime.py",
    "workflow/vendor/basis/adapters/crisprcasfinder.py", "workflow/vendor/basis/inputs.py",
    "workflow/vendor/basis/normalize.py", "workflow/vendor/basis/util.py")]


def extend_metadata(isolate, name):
    return str(Path(isolate) / "safety/metadata" / name)


def extend_native(isolate, module):
    return Path(isolate) / "safety/native" / module


def extend_result_store(isolate, module):
    from safety.storage import native_result_store
    selected = native_result_store(ROOT, config, isolate, module)
    receipt = Path(isolate) / "safety/completion" / (module + ".json")
    if receipt.is_file():
        # Placement changes must not strand a completed cache and rerun native
        # work. Existing DAG receipt checks still verify the payload contents.
        from safety.result_store import _record
        row = _record(receipt, destination=extend_native(isolate, module))
        return str(Path(row["payload_root"]).parents[2])
    return str(selected)


VERIFIED_EXTEND_RECEIPTS = set()


def extend_completion_receipt(isolate):
    from safety.result_store import verify_receipt
    receipt = Path(isolate) / "safety/completion/crisprcasfinder.json"
    destination = extend_native(isolate, "crisprcasfinder")
    key = (str(receipt), str(destination))
    if receipt.is_file() and key not in VERIFIED_EXTEND_RECEIPTS:
        verify_receipt(receipt, destination)
        VERIFIED_EXTEND_RECEIPTS.add(key)
    return str(receipt)


def extend_completion_targets():
    # Keep the receipt as a terminal target so deletion schedules verified cache
    # recovery even when a completed report already exists.
    return [extend_completion_receipt(isolate) for isolate in CTX.samples
            if CTX.module_enabled(isolate, "crisprcasfinder")]


def extend_normalization_inputs(w):
    if not CTX.module_enabled(w.isolate, w.extend_module):
        return []
    if w.extend_module == "crisprcasfinder":
        return [extend_completion_receipt(w.isolate)]
    return [str(extend_native(w.isolate, w.extend_module) / "parsed.json")]


rule pathogenfinder2_embeddings:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "pathogenfinder2"),
        proteins=lambda w: CTX.samples[w.isolate]["proteins"],
        resources=lambda w: CTX.resource_inputs("pathogenfinder2"),
        code=PF2_CODE
    output:
        embeddings="{isolate}/safety/native/pathogenfinder2/ProteinEmbeddings.h5",
        manifest="{isolate}/safety/native/pathogenfinder2/embeddings_manifest.json"
    params:
        timeout=lambda w: CTX.module_config(w.isolate, "pathogenfinder2").get("timeout_seconds", 14400),
        heartbeat=lambda w: CTX.module_config(w.isolate, "pathogenfinder2").get("heartbeat_seconds", 30),
        command_log=lambda w: str(Path(w.isolate) / "safety/logs/pathogenfinder2.embeddings.runtime.log"),
        command_record=lambda w: str(Path(w.isolate) / "safety/logs/pathogenfinder2.embeddings.runtime.json"),
        python=PYTHON,
        code=CODE_PATH
    threads:
        lambda w: CTX.module_config(w.isolate, "pathogenfinder2").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "pathogenfinder2").get("memory_mb", 16000),
        gpu=lambda w: CTX.module_config(w.isolate, "pathogenfinder2").get("gpus", 0)
    conda:
        CTX.env("pathogenfinder2")
    log:
        "{isolate}/safety/logs/pathogenfinder2.embeddings.log"
    benchmark:
        "{isolate}/safety/benchmarks/pathogenfinder2.embeddings.tsv"
    message:
        "Create and verify reusable full-proteome embeddings: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.runtime run \
            --timeout-seconds {params.timeout} --heartbeat-seconds {params.heartbeat} \
            --threads {threads} --log {params.command_log:q} --record {params.command_record:q} \
            -- env PYTHONPATH={params.code:q} python -m safety.extend embed \
            --sample {input.sample:q} --config {input.config:q} \
            --output {output.embeddings:q} --manifest {output.manifest:q} \
            --threads {threads} > {log:q} 2>&1
        """


rule pathogenfinder2_classification:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "pathogenfinder2"),
        embeddings="{isolate}/safety/native/pathogenfinder2/ProteinEmbeddings.h5",
        manifest="{isolate}/safety/native/pathogenfinder2/embeddings_manifest.json",
        resources=lambda w: CTX.resource_inputs("pathogenfinder2"),
        code=PF2_CODE + [str(ROOT / "workflow/lib/safety/pf2_classify.py")]
    output:
        native=directory("{isolate}/safety/native/pathogenfinder2/predictionPF2")
    params:
        timeout=lambda w: CTX.module_config(w.isolate, "pathogenfinder2").get("timeout_seconds", 14400),
        heartbeat=lambda w: CTX.module_config(w.isolate, "pathogenfinder2").get("heartbeat_seconds", 30),
        command_log=lambda w: str(Path(w.isolate) / "safety/logs/pathogenfinder2.classification.runtime.log"),
        command_record=lambda w: str(Path(w.isolate) / "safety/logs/pathogenfinder2.classification.runtime.json"),
        python=PYTHON,
        code=CODE_PATH
    threads:
        lambda w: CTX.module_config(w.isolate, "pathogenfinder2").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "pathogenfinder2").get("memory_mb", 16000),
        gpu=lambda w: CTX.module_config(w.isolate, "pathogenfinder2").get("gpus", 0)
    conda:
        CTX.env("pathogenfinder2")
    log:
        job="{isolate}/safety/logs/pathogenfinder2.classification.log",
        native="{isolate}/safety/logs/pathogenfinder2.classification.commands.log"
    benchmark:
        "{isolate}/safety/benchmarks/pathogenfinder2.classification.tsv"
    message:
        "Run the pretrained PF2 ensemble from verified embeddings: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log.job:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.runtime run \
            --timeout-seconds {params.timeout} --heartbeat-seconds {params.heartbeat} \
            --threads {threads} --log {params.command_log:q} --record {params.command_record:q} \
            -- env PYTHONPATH={params.code:q} python -m safety.pf2_classify \
            --sample {input.sample:q} --config {input.config:q} \
            --embeddings {input.embeddings:q} --manifest {input.manifest:q} \
            --output {output.native:q} --log {log.native:q} \
            --threads {threads} > {log.job:q} 2>&1
        """


rule pathogenfinder2_collect:
    input:
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "pathogenfinder2"),
        embeddings="{isolate}/safety/native/pathogenfinder2/ProteinEmbeddings.h5",
        manifest="{isolate}/safety/native/pathogenfinder2/embeddings_manifest.json",
        native="{isolate}/safety/native/pathogenfinder2/predictionPF2",
        code=PF2_CODE
    output:
        "{isolate}/safety/native/pathogenfinder2/parsed.json"
    params:
        code=CODE_PATH
    conda:
        CTX.env("pathogenfinder2")
    threads: 1
    resources:
        mem_mb=2000
    log:
        "{isolate}/safety/logs/pathogenfinder2.collect.log"
    message:
        "Verify PF2 scores and complete attention coverage: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} python -m safety.extend pf2-collect \
            --sample {input.sample:q} --config {input.config:q} \
            --embeddings {input.embeddings:q} --manifest {input.manifest:q} \
            --native {input.native:q} --output {output:q} > {log:q} 2>&1
        """


rule crisprcasfinder_assembly:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "crisprcasfinder"),
        assembly=lambda w: CTX.samples[w.isolate]["assembly"],
        resources=lambda w: CTX.resource_inputs("crisprcasfinder"),
        code=CCF_CODE
    output:
        receipt="{isolate}/safety/completion/crisprcasfinder.json"
    params:
        code=CODE_PATH,
        python=PYTHON,
        native=lambda w: str(extend_native(w.isolate, "crisprcasfinder")),
        store=lambda w: extend_result_store(w.isolate, "crisprcasfinder")
    threads:
        lambda w: CTX.module_config(w.isolate, "crisprcasfinder").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "crisprcasfinder").get("memory_mb", 4000)
    conda:
        CTX.env("crisprcasfinder")
    log:
        "{isolate}/safety/logs/crisprcasfinder.assembly.log"
    benchmark:
        "{isolate}/safety/benchmarks/crisprcasfinder.assembly.tsv"
    message:
        "Run and verify CRISPRCasFinder on the complete assembly: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.ccf_assembly \
            --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.code:q} > {log:q} 2>&1
        """


rule normalize_extend:
    input:
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, w.extend_module),
        validation=lambda w: extend_metadata(w.isolate, "validation.json"),
        native=extend_normalization_inputs,
        code=lambda w: PF2_CODE if w.extend_module == "pathogenfinder2" else CCF_CODE
    output:
        "{isolate}/safety/evidence/{extend_module}.json"
    wildcard_constraints:
        extend_module="(?:pathogenfinder2|crisprcasfinder)"
    params:
        code=CODE_PATH,
        python=PYTHON,
        native=lambda w: str(extend_native(w.isolate, w.extend_module) / "parsed.json"),
        receipt=lambda w: str(Path(w.isolate) / "safety/completion/crisprcasfinder.json")
            if w.extend_module == "crisprcasfinder" else ""
    log:
        "{isolate}/safety/logs/{extend_module}.normalize.log"
    message:
        "Normalize {wildcards.extend_module} evidence and retain scope: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.extend normalize \
            --sample {input.sample:q} --config {input.config:q} \
            --validation {input.validation:q} --module {wildcards.extend_module:q} \
            --native {params.native:q} --receipt={params.receipt:q} --output {output:q} > {log:q} 2>&1
        """
