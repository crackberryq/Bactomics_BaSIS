# One global installation barrier, as in HybAs: every selected tool/resource is
# ready before any sample's biological analysis starts.
SETUP_SOURCE = [str(ROOT / "workflow/lib/safety" / name) for name in
                ("setup.py", "context.py", "resources.py", "runtime.py")]
ALL_RESOURCE_RECEIPTS = [CTX.resource_receipt(name) for name in CTX.resource_specs()]
ALL_ENVIRONMENT_RECEIPTS = CTX.environment_receipts()
ENVIRONMENT_SOURCE = [str(ROOT / "workflow/lib/safety" / name)
                      for name in ("setup.py", "runtime.py")]
ENVIRONMENT_PATTERN = "|".join(re.escape(name) for name in CTX.needed_recipes()) or "(?!)"


rule setup:
    input:
        ALL_RESOURCE_RECEIPTS + ALL_ENVIRONMENT_RECEIPTS
    message:
        "All selected environments and resources are ready"


rule validate_inputs:
    input:
        annotations=lambda w: [CTX.samples[w.isolate][key] for key in
                              ("assembly", "proteins", "gff", "genbank") if CTX.samples[w.isolate].get(key)],
        source=SETUP_SOURCE + [str(ROOT / "workflow/vendor/basis/inputs.py"),
                              str(ROOT / "workflow/vendor/basis/sequence_validation.py")]
    output:
        sample="{isolate}/safety/metadata/sample.json",
        validation="{isolate}/safety/metadata/validation.json",
        requested="{isolate}/safety/metadata/requested-config.json"
    params:
        sample=lambda w: json.dumps(CTX.samples[w.isolate], sort_keys=True),
        config=lambda w: json.dumps(CTX.sample_config(w.isolate), sort_keys=True),
        directory=lambda w: w.isolate + "/safety/metadata"
    threads: 1
    resources: mem_mb=2000
    log: "{isolate}/safety/logs/input-checks.log"
    message: "Check annotation identity and capture inputs: {wildcards.isolate}"
    shell:
        "PYTHONPATH={CODE_PATH:q} {PYTHON:q} -m safety.setup validate "
        "--sample-json {params.sample:q} --config-json {params.config:q} "
        "--directory {params.directory:q} > {log:q} 2>&1"


rule capture_environment:
    input:
        recipe=lambda w: CTX.env(w.recipe),
        checks=lambda w: str(Path(CTX.env(w.recipe)).with_suffix('.post-deploy.sh')),
        source=ENVIRONMENT_SOURCE
    output:
        str(CTX.resource_root) + "/environments/{recipe}.json"
    wildcard_constraints: recipe=ENVIRONMENT_PATTERN
    conda:
        lambda w: CTX.env(w.recipe)
    threads: 1
    resources: mem_mb=1000
    log:
        str(CTX.resource_root) + "/logs/environment-{recipe}.log"
    message: "Verify and record the {wildcards.recipe} environment"
    shell:
        "(bash {input.checks:q} && "
        "PYTHONPATH={CODE_PATH:q} {PYTHON:q} -m safety.setup environment "
        "--recipe {input.recipe:q} --output {output:q}) > {log:q} 2>&1"


rule sample_setup_ready:
    input:
        requested="{isolate}/safety/metadata/requested-config.json",
        validation="{isolate}/safety/metadata/validation.json",
        sample="{isolate}/safety/metadata/sample.json",
        resources=ALL_RESOURCE_RECEIPTS,
        environments=ALL_ENVIRONMENT_RECEIPTS,
        source=SETUP_SOURCE
    output:
        config="{isolate}/safety/metadata/config.json",
        ready="{isolate}/safety/metadata/setup.ready.json"
    params: directory=lambda w: w.isolate + "/safety/metadata"
    threads: 1
    resources: mem_mb=2000
    log: "{isolate}/safety/logs/setup.log"
    message: "Confirm all setup prerequisites: {wildcards.isolate}"
    shell:
        "PYTHONPATH={CODE_PATH:q} {PYTHON:q} -m safety.setup finalize "
        "--directory {params.directory:q} --resources {input.resources:q} "
        "--environments {input.environments:q} > {log:q} 2>&1"
