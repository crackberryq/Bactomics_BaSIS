from pathlib import Path
import basis

SETUP_PLAN = config
SETUP_DIRECTORY = SETUP_PLAN["directory"]
SETUP_ENTRIES = {entry["name"]: entry for entry in SETUP_PLAN["environments"]}
SETUP_PLAN_PATH = str(Path(SETUP_DIRECTORY) / "plan.json")
SETUP_PYTHON = SETUP_PLAN["python"]
SETUP_CONTROLLER_SOURCE = str(Path(basis.__file__).resolve().parent.parent)

rule all:
    default_target: True
    input:
        SETUP_PLAN["summary_path"]

rule setup_environment:
    input:
        SETUP_PLAN_PATH
    output:
        SETUP_DIRECTORY + "/environments/{environment}.json"
    wildcard_constraints:
        environment="|".join(SETUP_ENTRIES)
    conda:
        lambda wc: SETUP_ENTRIES[wc.environment]["recipe"] if SETUP_ENTRIES[wc.environment].get("backend") == "managed" else None
    threads: 1
    params:
        python=SETUP_PYTHON,
        controller_source=SETUP_CONTROLLER_SOURCE,
        plan=SETUP_PLAN_PATH
    shell:
        "PYTHONPATH={params.controller_source:q} {params.python:q} -m basis.environment_workflow worker --plan {params.plan:q} --environment {wildcards.environment:q}"

rule setup_summary:
    input:
        expand(SETUP_DIRECTORY + "/environments/{environment}.json", environment=list(SETUP_ENTRIES))
    output:
        SETUP_PLAN["summary_path"]
    params:
        python=SETUP_PYTHON,
        controller_source=SETUP_CONTROLLER_SOURCE,
        plan=SETUP_PLAN_PATH
    shell:
        "PYTHONPATH={params.controller_source:q} {params.python:q} -m basis.environment_workflow summary --plan {params.plan:q}"
