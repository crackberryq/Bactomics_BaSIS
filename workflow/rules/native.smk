# Native tools run directly inside their Snakemake-deployed environments.
# Python below only checks and normalizes saved files; it never launches tools.
from safety.native import MODULES as NATIVE_MODULES, command_shell, native_dir, rgi_load_shell


def native_metadata(isolate, name):
    return str(Path(isolate) / "safety/metadata" / name)


def native_input_paths(w, module):
    sample = CTX.samples[w.isolate]
    keys = ("assembly", "proteins", "gff", "genbank")
    return [str(sample[key]) for key in keys if sample.get(key)]


def native_parser_sources(module):
    group = ("amr" if module in {"amrfinder", "rgi", "vfdb", "bacmet"} else
             "minced" if module == "minced" else "typing_defense" if module == "mlst" else "context")
    return [str(ROOT / "workflow/lib/safety/native.py"),
            str(ROOT / "workflow/vendor/basis/adapters" / (group + ".py")),
            str(ROOT / "workflow/vendor/basis/inputs.py")]


def native_normalization_inputs(w):
    return [str(native_dir(w.isolate, w.module))] if CTX.module_enabled(w.isolate, w.module) else []


rule rgi_load_database:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        resources=lambda w: CTX.resource_inputs("rgi"),
        source=str(ROOT / "workflow/lib/safety/native.py")
    output:
        db=directory("{isolate}/safety/native/rgi/database")
    params:
        command=lambda w: rgi_load_shell(CTX.module_config(w.isolate, "rgi"), Path(w.isolate) / "safety/native/rgi/database")
    conda:
        CTX.env("rgi")
    threads: 1
    log:
        "{isolate}/safety/logs/rgi_load_database.log"
    benchmark:
        "{isolate}/safety/benchmarks/rgi_load_database.tsv"
    message:
        "Prepare the isolate-local CARD database for RGI: {wildcards.isolate}"
    shell:
        r"""
        mkdir -p "$(dirname {log:q})"
        (
            {params.command}
        ) > {log:q} 2>&1
        """

rule amrfinder:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "amrfinder"),
        resources=lambda w: CTX.resource_inputs("amrfinder"),
        parser=native_parser_sources("amrfinder")
    output:
        native=directory("{isolate}/safety/native/amrfinder")
    params:
        command=lambda w, threads: command_shell("amrfinder", CTX.samples[w.isolate], CTX.module_config(w.isolate, "amrfinder"), native_dir(w.isolate, "amrfinder"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module amrfinder --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule rgi:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "rgi"),
        database="{isolate}/safety/native/rgi/database",
        resources=lambda w: CTX.resource_inputs("rgi"),
        parser=native_parser_sources("rgi")
    output:
        native=directory("{isolate}/safety/native/rgi/results")
    params:
        command=lambda w, threads: command_shell("rgi", CTX.samples[w.isolate], CTX.module_config(w.isolate, "rgi"), native_dir(w.isolate, "rgi"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module rgi --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule vfdb:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "vfdb"),
        resources=lambda w: CTX.resource_inputs("vfdb"),
        parser=native_parser_sources("vfdb")
    output:
        native=directory("{isolate}/safety/native/vfdb")
    params:
        command=lambda w, threads: command_shell("vfdb", CTX.samples[w.isolate], CTX.module_config(w.isolate, "vfdb"), native_dir(w.isolate, "vfdb"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module vfdb --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule bacmet:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "bacmet"),
        resources=lambda w: CTX.resource_inputs("bacmet"),
        parser=native_parser_sources("bacmet")
    output:
        native=directory("{isolate}/safety/native/bacmet")
    params:
        command=lambda w, threads: command_shell("bacmet", CTX.samples[w.isolate], CTX.module_config(w.isolate, "bacmet"), native_dir(w.isolate, "bacmet"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module bacmet --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule antismash:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "antismash"),
        resources=lambda w: CTX.resource_inputs("antismash"),
        parser=native_parser_sources("antismash")
    output:
        native=directory("{isolate}/safety/native/antismash")
    params:
        command=lambda w, threads: command_shell("antismash", CTX.samples[w.isolate], CTX.module_config(w.isolate, "antismash"), native_dir(w.isolate, "antismash"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module antismash --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule genomad:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "genomad"),
        resources=lambda w: CTX.resource_inputs("genomad"),
        parser=native_parser_sources("genomad")
    output:
        native=directory("{isolate}/safety/native/genomad")
    params:
        command=lambda w, threads: command_shell("genomad", CTX.samples[w.isolate], CTX.module_config(w.isolate, "genomad"), native_dir(w.isolate, "genomad"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module genomad --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule mobsuite:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "mobsuite"),
        resources=lambda w: CTX.resource_inputs("mobsuite"),
        parser=native_parser_sources("mobsuite")
    output:
        native=directory("{isolate}/safety/native/mobsuite")
    params:
        command=lambda w, threads: command_shell("mobsuite", CTX.samples[w.isolate], CTX.module_config(w.isolate, "mobsuite"), native_dir(w.isolate, "mobsuite"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module mobsuite --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule integronfinder:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "integronfinder"),
        resources=lambda w: CTX.resource_inputs("integronfinder"),
        parser=native_parser_sources("integronfinder")
    output:
        native=directory("{isolate}/safety/native/integronfinder")
    params:
        command=lambda w, threads: command_shell("integronfinder", CTX.samples[w.isolate], CTX.module_config(w.isolate, "integronfinder"), native_dir(w.isolate, "integronfinder"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module integronfinder --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule isescan:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "isescan"),
        resources=lambda w: CTX.resource_inputs("isescan"),
        parser=native_parser_sources("isescan")
    output:
        native=directory("{isolate}/safety/native/isescan")
    params:
        command=lambda w, threads: command_shell("isescan", CTX.samples[w.isolate], CTX.module_config(w.isolate, "isescan"), native_dir(w.isolate, "isescan"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module isescan --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule icescreen:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "icescreen"),
        resources=lambda w: CTX.resource_inputs("icescreen"),
        parser=native_parser_sources("icescreen")
    output:
        native=directory("{isolate}/safety/native/icescreen")
    params:
        command=lambda w, threads: command_shell("icescreen", CTX.samples[w.isolate], CTX.module_config(w.isolate, "icescreen"), native_dir(w.isolate, "icescreen"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module icescreen --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule minced:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "minced"),
        resources=lambda w: CTX.resource_inputs("minced"),
        parser=native_parser_sources("minced")
    output:
        native=directory("{isolate}/safety/native/minced")
    params:
        command=lambda w, threads: command_shell("minced", CTX.samples[w.isolate], CTX.module_config(w.isolate, "minced"), native_dir(w.isolate, "minced"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module minced --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule mlst:
    input:
        setup=lambda w: CTX.setup_target(w.isolate),
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        sources=lambda w: native_input_paths(w, "mlst"),
        resources=lambda w: CTX.resource_inputs("mlst"),
        parser=native_parser_sources("mlst")
    output:
        native=directory("{isolate}/safety/native/mlst")
    params:
        command=lambda w, threads: command_shell("mlst", CTX.samples[w.isolate], CTX.module_config(w.isolate, "mlst"), native_dir(w.isolate, "mlst"), threads),
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
        (
            {params.command}
            PYTHONPATH={params.code:q} {params.python:q} -m safety.native verify \
                --module mlst --sample {input.sample:q} --config {input.config:q} \
                --native {output.native:q}
        ) > {log:q} 2>&1
        """

rule normalize_native:
    input:
        sample=lambda w: native_metadata(w.isolate, "sample.json"),
        config=lambda w: native_metadata(w.isolate, "config.json"),
        validation=lambda w: native_metadata(w.isolate, "validation.json"),
        native=native_normalization_inputs,
        parser=lambda w: native_parser_sources(w.module) + [str(ROOT / "workflow/vendor/basis/normalize.py"),
                                                        str(ROOT / "workflow/vendor/basis/util.py")]
    output:
        "{isolate}/safety/evidence/{module}.json"
    wildcard_constraints:
        module="(?:" + "|".join(NATIVE_MODULES) + ")"
    params:
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
            --validation {input.validation:q} --native {params.native:q} --output {output:q} > {log:q} 2>&1
        """
