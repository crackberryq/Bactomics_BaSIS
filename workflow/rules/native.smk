# Each Snakemake job runs one checked native command in its selected environment.
# Successful payloads and failed diagnostics outlive disposable completion receipts.
from safety.native import MODULES as NATIVE_MODULES, native_dir

VERIFIED_NATIVE_RECEIPTS = set()


def native_metadata(isolate, name):
    return str(Path(isolate) / "safety/metadata" / name)


def native_input_paths(w, module):
    sample = CTX.samples[w.isolate]
    keys = ("assembly", "proteins", "gff", "genbank")
    return [str(sample[key]) for key in keys if sample.get(key)]


def native_parser_sources(module):
    group = ("amr" if module in {"amrfinder", "rgi", "vfdb", "bacmet"} else
             "minced" if module == "minced" else "typing_defense" if module == "mlst" else "context")
    sources = [str(ROOT / "workflow/lib/safety" / name) for name in
            ("native.py", "native_execution.py", "result_store.py", "runtime.py")] + [
            str(ROOT / "workflow/vendor/basis/adapters" / (group + ".py")),
            str(ROOT / "workflow/vendor/basis/inputs.py")]
    if module == "integronfinder":
        sources.append(str(ROOT / "workflow/lib/safety/integron.py"))
    return sources


def native_completion_receipt(isolate, module):
    from safety.result_store import verify_receipt
    receipt = Path(isolate) / "safety/completion" / (module + ".json")
    destination = (Path(isolate) / "safety/native/rgi/database" if module == "rgi_database"
                   else native_dir(isolate, module))
    # The scheduler input set must be identical before and after a first run.
    # Verify retained payloads without making their discovered inventory a new
    # input set on the next invocation; the worker verifies again before parse.
    key = (str(receipt), str(destination))
    if receipt.is_file() and key not in VERIFIED_NATIVE_RECEIPTS:
        verify_receipt(receipt, destination)
        VERIFIED_NATIVE_RECEIPTS.add(key)
    return str(receipt)


def native_normalization_inputs(w):
    if not CTX.module_enabled(w.isolate, w.module):
        return []
    return [native_completion_receipt(w.isolate, w.module)]


def native_completion_targets():
    # Completion receipts are retained targets, rather than disposable
    # intermediates hidden behind already completed reports. A missing receipt
    # therefore schedules verified cache recovery during an ordinary resume.
    result = []
    for isolate in CTX.samples:
        for module in NATIVE_MODULES:
            if CTX.module_enabled(isolate, module):
                result.append(native_completion_receipt(isolate, module))
                if module == "rgi":
                    result.append(native_completion_receipt(isolate, "rgi_database"))
    return result


rule rgi_load_database:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "rgi"),
        resources=lambda w: CTX.resource_inputs("rgi"),
        parser=native_parser_sources("rgi")
    output:
        receipt="{isolate}/safety/completion/rgi_database.json"
    params:
        native=lambda w: str(Path(w.isolate) / "safety/native/rgi/database"),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/rgi_database"),
        code=CODE_PATH,
        python=PYTHON
    conda: CTX.env("rgi")
    threads: 1
    log: "{isolate}/safety/logs/rgi_load_database.log"
    benchmark: "{isolate}/safety/benchmarks/rgi_load_database.tsv"
    message: "Prepare or reuse the verified isolate-local CARD database: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module rgi --load-database --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule amrfinder:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "amrfinder"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "amrfinder"),
        resources=lambda w: CTX.resource_inputs("amrfinder"),
        parser=native_parser_sources("amrfinder")
    output:
        receipt="{isolate}/safety/completion/amrfinder.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "amrfinder")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/amrfinder"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "amrfinder").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "amrfinder").get("memory_mb", 4000)
    conda:
        CTX.env("amrfinder")
    log:
        "{isolate}/safety/logs/amrfinder.log"
    benchmark:
        "{isolate}/safety/benchmarks/amrfinder.tsv"
    message:
        "Run amrfinder and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module amrfinder --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule rgi:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "rgi"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "rgi"),
        database="{isolate}/safety/completion/rgi_database.json",
        resources=lambda w: CTX.resource_inputs("rgi"),
        parser=native_parser_sources("rgi")
    output:
        receipt="{isolate}/safety/completion/rgi.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "rgi")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/rgi"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "rgi").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "rgi").get("memory_mb", 4000)
    conda:
        CTX.env("rgi")
    log:
        "{isolate}/safety/logs/rgi.log"
    benchmark:
        "{isolate}/safety/benchmarks/rgi.tsv"
    message:
        "Run rgi and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module rgi --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} \
            --database-receipt {input.database:q} --sources {input.parser:q} > {log:q} 2>&1
        """

rule vfdb:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "vfdb"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "vfdb"),
        resources=lambda w: CTX.resource_inputs("vfdb"),
        parser=native_parser_sources("vfdb")
    output:
        receipt="{isolate}/safety/completion/vfdb.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "vfdb")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/vfdb"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "vfdb").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "vfdb").get("memory_mb", 4000)
    conda:
        CTX.env("vfdb")
    log:
        "{isolate}/safety/logs/vfdb.log"
    benchmark:
        "{isolate}/safety/benchmarks/vfdb.tsv"
    message:
        "Run vfdb and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module vfdb --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule bacmet:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "bacmet"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "bacmet"),
        resources=lambda w: CTX.resource_inputs("bacmet"),
        parser=native_parser_sources("bacmet")
    output:
        receipt="{isolate}/safety/completion/bacmet.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "bacmet")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/bacmet"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "bacmet").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "bacmet").get("memory_mb", 4000)
    conda:
        CTX.env("bacmet")
    log:
        "{isolate}/safety/logs/bacmet.log"
    benchmark:
        "{isolate}/safety/benchmarks/bacmet.tsv"
    message:
        "Run bacmet and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module bacmet --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule antismash:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "antismash"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "antismash"),
        resources=lambda w: CTX.resource_inputs("antismash"),
        parser=native_parser_sources("antismash")
    output:
        receipt="{isolate}/safety/completion/antismash.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "antismash")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/antismash"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "antismash").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "antismash").get("memory_mb", 4000)
    conda:
        CTX.env("antismash")
    log:
        "{isolate}/safety/logs/antismash.log"
    benchmark:
        "{isolate}/safety/benchmarks/antismash.tsv"
    message:
        "Run antismash and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module antismash --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule genomad:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "genomad"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "genomad"),
        resources=lambda w: CTX.resource_inputs("genomad"),
        parser=native_parser_sources("genomad")
    output:
        receipt="{isolate}/safety/completion/genomad.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "genomad")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/genomad"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "genomad").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "genomad").get("memory_mb", 4000)
    conda:
        CTX.env("genomad")
    log:
        "{isolate}/safety/logs/genomad.log"
    benchmark:
        "{isolate}/safety/benchmarks/genomad.tsv"
    message:
        "Run genomad and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module genomad --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule mobsuite:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "mobsuite"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "mobsuite"),
        resources=lambda w: CTX.resource_inputs("mobsuite"),
        parser=native_parser_sources("mobsuite")
    output:
        receipt="{isolate}/safety/completion/mobsuite.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "mobsuite")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/mobsuite"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "mobsuite").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "mobsuite").get("memory_mb", 4000)
    conda:
        CTX.env("mobsuite")
    log:
        "{isolate}/safety/logs/mobsuite.log"
    benchmark:
        "{isolate}/safety/benchmarks/mobsuite.tsv"
    message:
        "Run mobsuite and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module mobsuite --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule integronfinder:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "integronfinder"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "integronfinder"),
        resources=lambda w: CTX.resource_inputs("integronfinder"),
        parser=native_parser_sources("integronfinder")
    output:
        receipt="{isolate}/safety/completion/integronfinder.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "integronfinder")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/integronfinder"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "integronfinder").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "integronfinder").get("memory_mb", 4000)
    conda:
        CTX.env("integronfinder")
    log:
        "{isolate}/safety/logs/integronfinder.log"
    benchmark:
        "{isolate}/safety/benchmarks/integronfinder.tsv"
    message:
        "Run integronfinder and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.integron \
            --module integronfinder --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule isescan:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "isescan"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "isescan"),
        resources=lambda w: CTX.resource_inputs("isescan"),
        parser=native_parser_sources("isescan")
    output:
        receipt="{isolate}/safety/completion/isescan.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "isescan")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/isescan"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "isescan").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "isescan").get("memory_mb", 4000)
    conda:
        CTX.env("isescan")
    log:
        "{isolate}/safety/logs/isescan.log"
    benchmark:
        "{isolate}/safety/benchmarks/isescan.tsv"
    message:
        "Run isescan and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module isescan --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule icescreen:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "icescreen"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "icescreen"),
        resources=lambda w: CTX.resource_inputs("icescreen"),
        parser=native_parser_sources("icescreen")
    output:
        receipt="{isolate}/safety/completion/icescreen.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "icescreen")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/icescreen"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "icescreen").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "icescreen").get("memory_mb", 4000)
    conda:
        CTX.env("icescreen")
    log:
        "{isolate}/safety/logs/icescreen.log"
    benchmark:
        "{isolate}/safety/benchmarks/icescreen.tsv"
    message:
        "Run icescreen and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module icescreen --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule minced:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "minced"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "minced"),
        resources=lambda w: CTX.resource_inputs("minced"),
        parser=native_parser_sources("minced")
    output:
        receipt="{isolate}/safety/completion/minced.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "minced")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/minced"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "minced").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "minced").get("memory_mb", 4000)
    conda:
        CTX.env("minced")
    log:
        "{isolate}/safety/logs/minced.log"
    benchmark:
        "{isolate}/safety/benchmarks/minced.tsv"
    message:
        "Run minced and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module minced --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule mlst:
    input:
        setup=lambda w: ancient(CTX.setup_target(w.isolate)),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, "mlst"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "mlst"),
        resources=lambda w: CTX.resource_inputs("mlst"),
        parser=native_parser_sources("mlst")
    output:
        receipt="{isolate}/safety/completion/mlst.json"
    params:
        native=lambda w: str(native_dir(w.isolate, "mlst")),
        store=lambda w: str(Path(w.isolate) / "safety/results-cache/mlst"),
        code=CODE_PATH,
        python=PYTHON
    threads:
        lambda w: CTX.module_config(w.isolate, "mlst").get("threads", 4)
    resources:
        mem_mb=lambda w: CTX.module_config(w.isolate, "mlst").get("memory_mb", 4000)
    conda:
        CTX.env("mlst")
    log:
        "{isolate}/safety/logs/mlst.log"
    benchmark:
        "{isolate}/safety/benchmarks/mlst.tsv"
    message:
        "Run mlst and verify its native outputs: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native_execution \
            --module mlst --sample {input.sample:q} --config {input.config:q} \
            --native {params.native:q} --receipt {output.receipt:q} --store {params.store:q} \
            --threads {threads} --sources {input.parser:q} > {log:q} 2>&1
        """

rule normalize_native:
    input:
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: CTX.module_config_path(w.isolate, w.module),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        native=native_normalization_inputs,
        parser=lambda w: native_parser_sources(w.module) + [str(ROOT / "workflow/vendor/basis/normalize.py"),
                                                        str(ROOT / "workflow/vendor/basis/util.py")]
    output:
        "{isolate}/safety/evidence/{module}.json"
    wildcard_constraints:
        module="(?:" + "|".join(NATIVE_MODULES) + ")"
    params:
        receipt=lambda w: str(Path(w.isolate) / "safety/completion" / (w.module + ".json")),
        native=lambda w: str(native_dir(w.isolate, w.module)),
        code=CODE_PATH,
        python=PYTHON
    log:
        "{isolate}/safety/logs/{module}.normalize.log"
    message:
        "Normalize {wildcards.module} evidence and preserve scope: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={params.code:q} {params.python:q} -m safety.native parse \
            --module {wildcards.module:q} --sample {input.sample:q} --config {input.config:q} \
            --receipt {params.receipt:q} --validation {input.validation:q} --native {params.native:q} --output {output:q} > {log:q} 2>&1
        """
