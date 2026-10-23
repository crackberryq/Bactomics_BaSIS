# Database preparation runs as native Snakemake jobs. A resource receipt is only
# published after the actual payload and any external inputs have been checked.
import json
import re
import shlex
from safety.resources import external_inputs, preparation_spec, source_inputs

RESOURCE_SPECS = CTX.resource_specs()
RESOURCE_NAMES = "|".join(re.escape(name) for name in RESOURCE_SPECS) or "(?!)"


def resource_dependencies(wildcards):
    return [CTX.resource_receipt(name)
            for name in RESOURCE_SPECS[wildcards.resource].get("dependencies", [])]


rule prepare_resource:
    input:
        dependencies=resource_dependencies,
        environment=lambda wc: str(Path(CTX.resource_root) / "environments" /
                                   (RESOURCE_SPECS[wc.resource]["recipe"] + ".json")),
        external=lambda wc: external_inputs(RESOURCE_SPECS[wc.resource]),
        source=lambda wc: source_inputs(RESOURCE_SPECS[wc.resource], ROOT)
    output:
        receipt=str(CTX.resource_root) + "/{resource}/provenance.json"
    wildcard_constraints:
        resource=RESOURCE_NAMES
    params:
        spec=lambda wc: json.dumps(preparation_spec(RESOURCE_SPECS[wc.resource]), sort_keys=True),
        dependency_args=lambda wc: " ".join("--dependency " + shlex.quote(path)
                                          for path in resource_dependencies(wc)),
        root=str(CTX.resource_root),
        commands=lambda wc: str(CTX.resource_root) + "/logs/" + wc.resource + ".commands.log"
    threads:
        lambda wc: int(RESOURCE_SPECS[wc.resource].get("threads", 1))
    resources:
        mem_mb=lambda wc: int(RESOURCE_SPECS[wc.resource].get("memory_mb", 4000))
    conda:
        lambda wc: CTX.env_for_resource(wc.resource)
    log:
        str(CTX.resource_root) + "/logs/{resource}.log"
    benchmark:
        str(CTX.resource_root) + "/benchmarks/{resource}.tsv"
    message:
        "Prepare and verify {wildcards.resource} resource"
    shell:
        """
        mkdir -p "$(dirname {log:q})"
        PYTHONPATH={CODE_PATH:q} {PYTHON:q} -m safety.resources prepare \
            --name {wildcards.resource:q} --spec-json {params.spec:q} \
            --resource-root {params.root:q} --logfile {params.commands:q} \
            --environment {input.environment:q} \
            --threads {threads} {params.dependency_args} > {log:q} 2>&1
        """
