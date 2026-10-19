# Extend is split at the expensive HDF5 boundary; no nested Conda or scheduler.
from safety.extend import read_json as extend_read_json

PF2_CODE = [str(ROOT / path) for path in (
    "workflow/lib/safety/extend.py", "workflow/lib/safety/runtime.py",
    "workflow/vendor/basis/adapters/extend.py", "workflow/vendor/basis/inputs.py",
    "workflow/vendor/basis/normalize.py", "workflow/vendor/basis/util.py")]
CCF_CODE = [str(ROOT / path) for path in (
    "workflow/lib/safety/extend.py", "workflow/lib/safety/runtime.py",
    "workflow/vendor/basis/adapters/crisprcasfinder.py", "workflow/vendor/basis/inputs.py",
    "workflow/vendor/basis/normalize.py", "workflow/vendor/basis/util.py")]


def extend_metadata(isolate, name):
    return str(Path(isolate) / "safety/metadata" / name)


def extend_native(isolate, module):
    return Path(isolate) / "safety/native" / module


def extend_normalization_inputs(w):
    if not CTX.module_enabled(w.isolate, w.extend_module):
        return []
    return [str(extend_native(w.isolate, w.extend_module) / "parsed.json")]


rule pathogenfinder2_embeddings:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: extend_metadata(w.isolate, "config.json"),
        proteins=lambda w: CTX.samples[w.isolate]["proteins"],
        resources=lambda w: CTX.resource_inputs("pathogenfinder2"),
        code=PF2_CODE
    output:
        embeddings="{isolate}/safety/native/pathogenfinder2/ProteinEmbeddings.h5",
        manifest="{isolate}/safety/native/pathogenfinder2/embeddings_manifest.json"
    params:
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
        PYTHONPATH={params.code:q} python -m safety.extend embed \
            --sample {input.sample:q} --config {input.config:q} \
            --output {output.embeddings:q} --manifest {output.manifest:q} \
            --threads {threads} > {log:q} 2>&1
        """


rule pathogenfinder2_classification:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: extend_metadata(w.isolate, "config.json"),
        embeddings="{isolate}/safety/native/pathogenfinder2/ProteinEmbeddings.h5",
        manifest="{isolate}/safety/native/pathogenfinder2/embeddings_manifest.json",
        resources=lambda w: CTX.resource_inputs("pathogenfinder2"),
        code=PF2_CODE
    output:
        native=directory("{isolate}/safety/native/pathogenfinder2/predictionPF2")
    params:
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
        PYTHONPATH={params.code:q} python -m safety.extend classify \
            --sample {input.sample:q} --config {input.config:q} \
            --embeddings {input.embeddings:q} --manifest {input.manifest:q} \
            --output {output.native:q} --log {log.native:q} \
            --threads {threads} > {log.job:q} 2>&1
        """


rule pathogenfinder2_collect:
    input:
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: extend_metadata(w.isolate, "config.json"),
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


checkpoint crisprcasfinder_contigs:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: extend_metadata(w.isolate, "config.json"),
        assembly=lambda w: CTX.samples[w.isolate]["assembly"],
        code=CCF_CODE
    output:
        contigs=directory("{isolate}/safety/work/crisprcasfinder/contigs")
    params:
        code=CODE_PATH,
        python=PYTHON
    log:
        "{isolate}/safety/logs/crisprcasfinder.contigs.log"
    message:
        "Stage CRISPR/Cas contigs with reversible identifiers: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.extend ccf-prepare \
            --sample {input.sample:q} --config {input.config:q} \
            --output {output.contigs:q} > {log:q} 2>&1
        """


def ccf_contigs_directory(w):
    return checkpoints.crisprcasfinder_contigs.get(isolate=w.isolate).output.contigs


def ccf_contig_outputs(w):
    folder = Path(ccf_contigs_directory(w))
    mapping = extend_read_json(folder / "contigs.json")
    return [str(extend_native(w.isolate, "crisprcasfinder") / key) for key in mapping]


rule crisprcasfinder_contig:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: extend_metadata(w.isolate, "config.json"),
        contigs=ccf_contigs_directory,
        resources=lambda w: CTX.resource_inputs("crisprcasfinder"),
        code=CCF_CODE
    output:
        native=directory("{isolate}/safety/native/crisprcasfinder/{ccf_contig}")
    wildcard_constraints:
        ccf_contig="CCF[0-9]{6}"
    params:
        code=CODE_PATH
    threads:
        lambda w: CTX.module_config(w.isolate, "crisprcasfinder").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "crisprcasfinder").get("memory_mb", 4000)
    conda:
        CTX.env("crisprcasfinder")
    log:
        job="{isolate}/safety/logs/crisprcasfinder/{ccf_contig}.log",
        native="{isolate}/safety/logs/crisprcasfinder/{ccf_contig}.commands.log"
    benchmark:
        "{isolate}/safety/benchmarks/crisprcasfinder/{ccf_contig}.tsv"
    message:
        "Run and verify CRISPRCasFinder contig {wildcards.ccf_contig}: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log.job:q})"
        PYTHONPATH={params.code:q} python -m safety.extend ccf-contig \
            --sample {input.sample:q} --config {input.config:q} \
            --contigs {input.contigs:q} --contig {wildcards.ccf_contig:q} \
            --output {output.native:q} --log {log.native:q} \
            --threads {threads} > {log.job:q} 2>&1
        """


rule crisprcasfinder_collect:
    input:
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: extend_metadata(w.isolate, "config.json"),
        contigs=ccf_contigs_directory,
        native=ccf_contig_outputs,
        code=CCF_CODE
    output:
        "{isolate}/safety/native/crisprcasfinder/parsed.json"
    params:
        code=CODE_PATH,
        python=PYTHON,
        native=lambda w: str(extend_native(w.isolate, "crisprcasfinder"))
    log:
        "{isolate}/safety/logs/crisprcasfinder.collect.log"
    message:
        "Reconcile every CRISPR/Cas contig with the supplied assembly: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.extend ccf-collect \
            --sample {input.sample:q} --config {input.config:q} \
            --contigs {input.contigs:q} --native {params.native:q} \
            --output {output:q} > {log:q} 2>&1
        """


rule normalize_extend:
    input:
        sample=lambda w: extend_metadata(w.isolate, "sample.json"),
        config=lambda w: extend_metadata(w.isolate, "config.json"),
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
        native=lambda w: str(extend_native(w.isolate, w.extend_module) / "parsed.json")
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
            --native {params.native:q} --output {output:q} > {log:q} 2>&1
        """
