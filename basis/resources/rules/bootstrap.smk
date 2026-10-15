# Included by analysis.smk after PLAN, PLAN_PATH, RECEIPTS, PYTHON and
# CONTROLLER_SOURCE are established. Resource preparation is part of that DAG.
from basis.bootstrap import bootstrap_environment, ready_path

BOOTSTRAP_SPECS = {entry["id"]: entry for entry in PLAN.get("bootstrap_resources", [])}

rule bootstrap_resource:
    input:
        plan=PLAN_PATH,
        dependencies=lambda wc: [str(ready_path(RECEIPTS, name)) for name in BOOTSTRAP_SPECS[wc.resource].get("dependencies", [])]
    output:
        RECEIPTS + "/bootstrap/{resource}.ready.json"
    wildcard_constraints:
        resource="|".join(BOOTSTRAP_SPECS) if BOOTSTRAP_SPECS else "__no_bootstrap_resources__"
    conda:
        lambda wc: bootstrap_environment(PLAN, wc.resource)
    threads:
        lambda wc: min(int(BOOTSTRAP_SPECS[wc.resource].get("threads", 1)), int(PLAN["config"]["execution"]["cores"]))
    resources:
        mem_mb=lambda wc: int(BOOTSTRAP_SPECS[wc.resource].get("memory_mb", 4000))
    params:
        python=PYTHON,
        source=CONTROLLER_SOURCE,
        plan=PLAN_PATH,
        root=RECEIPTS
    log:
        RECEIPTS + "/bootstrap/{resource}.worker.log"
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.bootstrap --plan {params.plan:q} --run-dir {params.root:q} --resource {wildcards.resource:q} --threads {threads} 2>&1 | tee {log:q}"
