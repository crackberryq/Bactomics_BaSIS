"""Resolve scheduler GPU budgets without requiring Torch in the launcher."""
from __future__ import annotations

import copy
import shutil
import subprocess


def configure_devices(config, extend=False):
    result = copy.deepcopy(config)
    execution = result["execution"]
    policy = execution.get("device", "configured")
    module = result["modules"]["pathogenfinder2"]
    if policy == "configured" or not extend or not module.get("enabled") or module.get("mode") == "import":
        return result
    available = False
    reason = "CPU selected"
    if policy != "cpu" and shutil.which("nvidia-smi"):
        try:
            probe = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                                   text=True, capture_output=True, timeout=10, check=True)
            memory = [int(line.strip()) for line in probe.stdout.splitlines() if line.strip()]
            available = bool(memory) and memory[0] >= 4096
            reason = "NVIDIA GPU detected; Torch runtime will be checked before inference" if available else "CPU selected: first GPU has less than 4096 MiB"
        except (OSError, ValueError, subprocess.SubprocessError):
            reason = "CPU selected: NVIDIA driver probe unavailable"
    elif policy != "cpu":
        reason = "CPU selected: NVIDIA driver utility unavailable"
    if policy == "cuda" and not available:
        raise ValueError("CUDA explicitly requested but a supported GPU was not detected")
    execution["gpus"] = int(available)
    module.update(gpus=int(available), device_policy=policy)
    execution["device_resolution"] = {"policy": policy, "gpu_reserved": available,
                                       "reason": reason, "torch_runtime_verified": False}
    return result


def verify_inference_device(config, runner):
    """Check the actual tool environment; auto mode can fall back to CPU."""
    policy = config.get("device_policy", "configured")
    if policy == "configured":
        return None
    import json
    from .progress import emit
    probe = runner.run([config.get("python_executable", "python"), "-c",
        "import json,torch; print(json.dumps({'cuda_available':bool(torch.cuda.is_available()),'torch_version':str(torch.__version__)}))"])
    data = json.loads(probe.stdout.strip())
    use_cuda = bool(config.get("gpus")) and data["cuda_available"]
    if policy == "cuda" and not use_cuda:
        raise ValueError("CUDA requested but unavailable in the PathogenFinder2 environment")
    config["gpus"] = runner.gpus = int(use_cuda)
    data.update(policy=policy, selected="cuda" if use_cuda else "cpu")
    emit("PathogenFinder2 inference device: " + data["selected"])
    return data
